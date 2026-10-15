"""
POST /ask: a visitor's question about the owner's work, answered as a
server-sent event stream:

    event: token   data: {"text": "..."}          a piece of the answer
    event: done    data: {"citations": [...],     the sources it cited, and
                          "truncated": false}     whether it hit ASK_MAX_TOKENS
    event: error   data: {"detail": "..."}        the answer broke off

Errors before the first token (limits, model down) are ordinary JSON
responses with a status code instead.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger

from api.dependencies.auth import current_admin
from api.dependencies.rate_limit import rate_limit
from core import config
from db.dependency import DB
from schemas.ask import AskRequest
from services import ask_history, ask_log
from services.ask import REQUESTS, Answer, ModelUnavailableError, Source, snapshot
from services.rate_limit import ASK, ASK_ALL, ASK_ALL_CLIENTS

router = APIRouter()

# Created lazily: a semaphore belongs to the event loop that first uses it.
_generating: asyncio.Semaphore | None = None


def generating() -> asyncio.Semaphore:
    global _generating
    if _generating is None:
        _generating = asyncio.Semaphore(config.ASK_MAX_CONCURRENT)
    return _generating


def unavailable(detail: str, retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


def event(name: str, data: dict[str, Any]) -> str:
    # JSON keeps newlines in the answer from ending the event early.
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# The visitor's own limit first: a visitor over it does not use up the
# site's daily budget.
ask_limit = rate_limit(ASK, "questions")
site_limit = rate_limit(ASK_ALL, "questions on the site today", client=ASK_ALL_CLIENTS)


def offline() -> HTTPException:
    return unavailable("The assistant is offline right now. Try again later.", 60)


def busy() -> HTTPException:
    REQUESTS.labels("busy").inc()
    return unavailable("Busy answering other questions. Try again shortly.", 10)


@router.post("/ask")
async def ask(body: AskRequest, request: Request, db: DB):
    # The limits are counted here, not as route dependencies, so questions
    # refused before reaching the model do not use them up: malformed (422,
    # checked before this runs), switched off, or all slots busy. A visitor
    # retrying while the GPU is busy is not locked out for the hour. The
    # owner, logged in, is not counted at all; the GPU slots still apply.
    slots = generating()
    # The owner trying the chat is not limited. Any other credential, valid
    # or not, is simply a visitor: the route is public.
    admin = await current_admin(request) is not None
    if not config.ASK_URL:
        REQUESTS.labels("unavailable").inc()
        raise offline()
    if slots.locked():
        raise busy()
    if not admin:
        await ask_limit(request)
        await site_limit(request)
    if slots.locked():  # taken while the limits were checked
        raise busy()

    snap = await snapshot(db)
    # Done with the database. The session would otherwise keep its pooled
    # connection, idle in a transaction, until the answer finishes
    # streaming: a yield dependency is only torn down after the response.
    await db.close()

    # The conversation so far; an unknown or expired id starts a new one.
    redis = getattr(request.app.state, "redis", None)
    history = await ask_history.load(redis, body.conversation)
    conversation = body.conversation if history else ask_history.new_id()

    answer = Answer(snap, body.question, body.quote, body.page, history)
    events = stream(answer, slots, admin, redis, conversation)
    try:
        # Runs the stream up to its connection to the model, so a model
        # that is down is still a plain 503 rather than a broken stream.
        await events.__anext__()
    except ModelUnavailableError as error:
        REQUESTS.labels("unavailable").inc()
        logger.warning(f"Ask: model unavailable: {error}")
        raise offline() from error

    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def store(
    answer: Answer, citations: list[Source], status: str, started: float, admin: bool
) -> None:
    """Keep the question and its answer (services/ask_log.py)."""
    await ask_log.record(
        ask_log.Entry(
            question=answer.question,
            quote=answer.quote,
            page=answer.page,
            answer=answer.text,
            citations=[asdict(s) for s in citations],
            status=status,
            truncated=answer.truncated,
            output_tokens=answer.output_tokens,
            duration_ms=round((time.perf_counter() - started) * 1000),
            admin=admin,
        ),
    )


async def stream(
    answer: Answer,
    slots: asyncio.Semaphore,
    admin: bool = False,
    redis=None,
    conversation: str | None = None,
) -> AsyncIterator[str]:
    """
    The GPU slot and the model connection are taken inside the generator,
    and the route starts it before returning: from then on its cleanup runs
    however the response ends, even if the visitor leaves before the first
    byte is sent and the response never iterates it.
    """
    async with slots:
        started = time.perf_counter()
        try:
            await answer.open()
            yield ""  # connected; the route takes this, the visitor never sees it
            started = time.perf_counter()
            async for piece in answer.tokens():
                yield event("token", {"text": piece})
            citations = answer.citations()
            yield event(
                "done",
                {
                    "citations": [asdict(s) for s in citations],
                    "truncated": answer.truncated,
                    # Sent back with the next question to continue.
                    "conversation": conversation,
                },
            )
            REQUESTS.labels("answered").inc()
            # The question is logged (Loki keeps it for its retention
            # period) to see what visitors ask; the visitor's address is not.
            # "Ask (admin)": the owner's own questions, apart from visitors'.
            logger.info(
                f"Ask{' (admin)' if admin else ''}: answered in "
                f"{time.perf_counter() - started:.1f}s, "
                f"{answer.output_tokens} tokens"
                f"{' (cut at the limit)' if answer.truncated else ''}"
                f"{f', after {len(answer.history)} turns' if answer.history else ''}"
                ", cited "
                f"{[s.id for s in citations]}: {answer.question!r}"
                + (
                    f" about a passage on {answer.page or '?'}: {answer.quote[:80]!r}"
                    if answer.quote
                    else ""
                )
            )
            await store(answer, citations, "answered", started, admin)
            if conversation:
                await ask_history.save(
                    redis,
                    conversation,
                    ask_history.Turn(
                        answer.question,
                        answer.text,
                        # The API accepts a page without a passage; only a
                        # passage makes the turn "about" that page.
                        answer.page if answer.quote else None,
                    ),
                )
        except ModelUnavailableError:
            raise  # before the first byte: the route answers 503
        except asyncio.CancelledError:
            # The visitor left; there is no one to send an event to.
            REQUESTS.labels("cancelled").inc()
            raise
        except Exception as error:
            # Whatever broke (the model, the connection, a malformed chunk),
            # the visitor gets an error event rather than a dropped stream.
            REQUESTS.labels("error").inc()
            logger.error(f"Ask: answer broke off: {error!r}")
            yield event("error", {"detail": "The answer broke off. Try again."})
            await store(answer, [], "error", started, admin)
        finally:
            # Also runs when the visitor leaves mid-answer: closing the
            # connection makes vLLM stop generating.
            await answer.close()
