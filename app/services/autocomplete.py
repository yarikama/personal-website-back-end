"""Prompting the autocomplete model and turning its output into a suggestion."""

import json
import math
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

import httpx

from core import config

MAX_CONTEXT_CHARS = 2000  # about 500 tokens: enough context, fast prefill
MAX_TOKENS = 16  # an upper bound; generation usually stops much earlier
TIMEOUT_SECONDS = 2.0  # past this the author has typed on; give up quietly
SENTENCE_END = (".", "!", "?")
# A suggestion is one line. The base model also sometimes emits the
# thinking tags of Qwen's chat format; nothing after them belongs in a note.
STOP = ["\n", "<think>", "</think>"]


# Tests swap in an httpx.MockTransport.
_transport: httpx.AsyncBaseTransport | None = None


@dataclass
class Prompt:
    text: str
    # Spaces or tabs the author typed after the last word. They are left out
    # of the prompt (see build_prompt) and removed from the suggestion.
    trailing_space: str


@dataclass
class Completion:
    # Everything generated before the model was stopped: the kept tokens,
    # plus the unsure token that ended the suggestion, if any.
    text: str
    tokens: list[tuple[str, float]]
    latency_ms: int


def build_prompt(prefix: str, title: str) -> Prompt:
    """The model continues a Markdown document: the title as a heading, then
    the text before the cursor.

    Trailing spaces are cut. BPE tokenizers attach a word's leading space to
    the word (" was"), so a prompt ending in a space leaves the model to
    produce a token that starts with a second one; ending on the word lets it
    continue naturally. Newlines stay: a new line is a real boundary.
    """
    context = prefix[-MAX_CONTEXT_CHARS:]
    body = context.rstrip(" \t")
    header = f"# {title.strip()}\n\n" if title.strip() else ""
    return Prompt(text=header + body, trailing_space=context[len(body) :])


def keep(token: str, logprob: float, min_prob: float) -> tuple[bool, bool]:
    """Whether a token belongs in the suggestion, and whether it ends it."""
    if math.exp(logprob) < min_prob:
        return False, True
    return True, token.rstrip().endswith(SENTENCE_END)


def shape_suggestion(
    tokens: list[tuple[str, float]], trailing_space: str, min_prob: float
) -> str:
    """Keep the confident start of the completion.

    Tokens are kept while each one's probability stays at or above min_prob,
    and the suggestion ends after a sentence end. If the author already typed
    a space, the completion must start with one (it is dropped); a completion
    that instead continues the last word no longer fits and is discarded.
    """
    text = ""
    for token, logprob in tokens:
        kept, last = keep(token, logprob, min_prob)
        if kept:
            text += token
        if last:
            break
    text = text.rstrip()
    if trailing_space:
        if not text[:1].isspace():
            return ""
        text = text.lstrip(" \t")
    return text


MIN_REPEAT = 4  # shorter repeats are too often legitimate ("a a" is not)
MAX_REPEAT = 60


def trim_repetition(before: str, suggestion: str) -> str:
    """Cut the suggestion where it starts repeating the text right before.

    The penalties sent to the model only see the suggestion, so it can still
    open by echoing the sentence just typed, or loop on a phrase. Reading the
    text before the cursor and the suggestion as one, the suggestion ends at
    the first point where the next few characters (at least MIN_REPEAT, with
    at least one letter or CJK character, so "----" rules survive) are the
    same as the ones just before them.
    """
    tail = before[-MAX_REPEAT:]
    text = tail + suggestion
    for i in range(len(suggestion)):
        at = len(tail) + i
        for size in range(MIN_REPEAT, min(MAX_REPEAT, at) + 1):
            unit = text[at : at + size]
            if (
                len(unit) == size
                and unit == text[at - size : at]
                and any(ch.isalnum() for ch in unit)
            ):
                return suggestion[:i].rstrip()
    return suggestion


async def complete(prompt: str, min_prob: float) -> Completion:
    """Ask the model to continue prompt, streaming token by token.

    Reading stops at the first token the suggestion would not keep, or after
    a sentence end: leaving the stream closes the connection and vLLM aborts
    the request, instead of generating up to MAX_TOKENS that would be thrown
    away. Raises httpx.HTTPError on failure.
    """
    started = time.perf_counter()
    text, tokens = "", []
    async with (
        httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=_transport) as client,
        client.stream(
            "POST",
            f"{config.AUTOCOMPLETE_URL.rstrip('/')}/v1/completions",
            json={
                "model": config.AUTOCOMPLETE_MODEL,
                "prompt": prompt,
                "max_tokens": MAX_TOKENS,
                "temperature": 0,
                "stop": STOP,
                "frequency_penalty": config.AUTOCOMPLETE_FREQUENCY_PENALTY,
                "presence_penalty": config.AUTOCOMPLETE_PRESENCE_PENALTY,
                # 0: the log-probability of each generated token, no alternatives.
                "logprobs": 0,
                "stream": True,
            },
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            if line == "data: [DONE]":
                break
            choice = json.loads(line[len("data: ") :])["choices"][0]
            logprobs = choice.get("logprobs") or {}
            text += choice.get("text", "")
            done = False
            for token, logprob in zip(
                logprobs.get("tokens", []),
                logprobs.get("token_logprobs", []),
                strict=False,
            ):
                tokens.append((token, logprob))
                done = done or keep(token, logprob, min_prob)[1]
            if done:
                break
    return Completion(
        text=text,
        tokens=tokens,
        latency_ms=round((time.perf_counter() - started) * 1000),
    )


# ── suggestions waiting for feedback ─────────────────────────────────────────
#
# A suggestion is stored only once the editor reports what became of it, so
# the table holds suggestions that were actually shown. Many never are: the
# author types on while the request is in flight and the editor drops it.
# Until then they wait here. In memory, which is fine while the backend runs
# as a single replica (it does, see the Deployment); more replicas would need
# a shared store.

PENDING_TTL_SECONDS = 3600  # a suggestion left on screen this long is let go
PENDING_MAX = 1000


@dataclass
class Pending:
    created_at: datetime
    note_id: uuid.UUID | None
    prompt: str
    completion: Completion
    suggestion: str
    min_prob: float
    model_version: str


_pending: "OrderedDict[uuid.UUID, tuple[float, Pending]]" = OrderedDict()


def _prune() -> None:
    now = time.monotonic()
    while _pending:
        _, (stored, _) = next(iter(_pending.items()))
        if now - stored > PENDING_TTL_SECONDS or len(_pending) > PENDING_MAX:
            _pending.popitem(last=False)
        else:
            break


def remember(pending: Pending) -> uuid.UUID:
    _prune()
    id = uuid.uuid4()
    _pending[id] = (time.monotonic(), pending)
    return id


def take(id: uuid.UUID) -> Pending | None:
    """The suggestion with this id, if it is still waiting; it stops waiting."""
    _prune()
    entry = _pending.pop(id, None)
    return entry[1] if entry else None


def accepted_token_count(
    tokens: list[tuple[str, float]],
    completion: str,
    suggestion: str,
    accepted_chars: int,
) -> int:
    """How many generated tokens the accepted part of the suggestion covers:
    where the author stopped, in the model's own units.

    The suggestion is the completion minus a leading space the author had
    already typed, so positions are shifted by that. A token counts only if
    it was taken whole. Approximate when a token is a fragment of a
    multi-byte character.
    """
    offset = max(completion.find(suggestion), 0)
    end = offset + accepted_chars
    covered, count = 0, 0
    for token, _ in tokens:
        covered += len(token)
        if covered > end:
            break
        count += 1
    return count
