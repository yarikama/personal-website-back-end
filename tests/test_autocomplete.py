import asyncio
import json
import math
import os
from datetime import datetime, timezone

import httpx
import pytest
from api.dependencies import get_current_admin
from core import config
from db.dependency import get_async_db
from db.models.autocomplete import AutocompleteSuggestion
from db.session import Base
from fastapi.testclient import TestClient
from main import get_application
from services import autocomplete
from services.autocomplete import Completion, build_prompt, shape_suggestion
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

VERSION = "test-model@autocomplete-tests"


def lp(prob):
    return math.log(prob)


# ── build_prompt ─────────────────────────────────────────────────────────────


def test_prompt_puts_the_title_as_a_heading():
    assert build_prompt("Some text", "My Note").text == "# My Note\n\nSome text"
    assert build_prompt("Some text", "  ").text == "Some text"


def test_prompt_drops_trailing_spaces_but_remembers_them():
    prompt = build_prompt("The model was ", "")
    assert prompt.text == "The model was"
    assert prompt.trailing_space == " "


def test_prompt_keeps_a_trailing_newline():
    prompt = build_prompt("A paragraph.\n", "")
    assert prompt.text == "A paragraph.\n"
    assert prompt.trailing_space == ""


def test_prompt_keeps_only_the_tail_of_long_notes():
    prompt = build_prompt("x" * 5000 + "end", "")
    assert len(prompt.text) == autocomplete.MAX_CONTEXT_CHARS
    assert prompt.text.endswith("end")


# ── shape_suggestion ─────────────────────────────────────────────────────────


def test_suggestion_stops_at_the_first_unsure_token():
    tokens = [(" the", lp(0.9)), (" model", lp(0.6)), (" learns", lp(0.2))]
    assert shape_suggestion(tokens, "", 0.5) == " the model"


def test_suggestion_stops_after_a_sentence_end():
    tokens = [(" it", lp(0.9)), (" works.", lp(0.9)), (" Then", lp(0.9))]
    assert shape_suggestion(tokens, "", 0.5) == " it works."


def test_suggestion_is_empty_when_the_first_token_is_unsure():
    assert shape_suggestion([(" maybe", lp(0.3))], "", 0.5) == ""


def test_suggestion_drops_the_space_the_author_already_typed():
    tokens = [(" was", lp(0.9)), (" his", lp(0.8))]
    assert shape_suggestion(tokens, " ", 0.5) == "was his"


def test_suggestion_continuing_the_word_does_not_fit_after_a_space():
    # The author typed "portfol" + space; "io" would glue onto the old word.
    assert shape_suggestion([("io", lp(0.9))], " ", 0.5) == ""


def test_suggestion_can_finish_the_current_word():
    assert shape_suggestion([("lete", lp(0.9))], "", 0.5) == "lete"


# ── endpoints ────────────────────────────────────────────────────────────────


@pytest.fixture
def session_factory():
    database_url = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
    )
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    with factory() as db:
        db.query(AutocompleteSuggestion).filter(
            AutocompleteSuggestion.model_version == VERSION
        ).delete(synchronize_session=False)
        db.commit()


@pytest.fixture
def client(session_factory, async_db, monkeypatch):
    monkeypatch.setattr(config, "AUTOCOMPLETE_URL", "http://model.test")
    monkeypatch.setattr(config, "AUTOCOMPLETE_MODEL_VERSION", VERSION)
    monkeypatch.setattr(config, "AUTOCOMPLETE_MIN_TOKEN_PROB", 0.5)
    autocomplete._pending.clear()

    app = get_application()
    app.dependency_overrides[get_async_db] = async_db
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    return TestClient(app)


def fake_model(monkeypatch, tokens: list[tuple[str, float]] | None = None, error=None):
    async def complete(prompt, min_prob):
        if error:
            raise error
        assert tokens is not None, "a model that answers needs tokens"
        return Completion(
            text="".join(t for t, _ in tokens), tokens=tokens, latency_ms=42
        )

    monkeypatch.setattr(autocomplete, "complete", complete)


def count_rows(session_factory):
    with session_factory() as db:
        return (
            db.query(AutocompleteSuggestion)
            .filter(AutocompleteSuggestion.model_version == VERSION)
            .count()
        )


def test_a_suggestion_is_recorded_when_its_outcome_is_reported(
    client, session_factory, monkeypatch
):
    fake_model(monkeypatch, [(" was", lp(0.9)), (" his", lp(0.7)), (" 3D", lp(0.1))])

    response = client.post(
        "/api/v1/admin/complete",
        json={"prefix": "What impressed me ", "title": "Notes"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["suggestion"] == "was his"
    # Not yet: it may never be shown.
    assert count_rows(session_factory) == 0

    client.post(
        f"/api/v1/admin/complete/{data['id']}/feedback",
        json={"outcome": "rejected", "acceptedChars": 4},
    )

    with session_factory() as db:
        row = db.get(AutocompleteSuggestion, data["id"])
        assert row.prompt == "# Notes\n\nWhat impressed me"
        assert row.completion == " was his 3D"
        assert row.suggestion == "was his"
        assert row.model_version == VERSION
        assert row.latency_ms == 42
        assert row.min_token_prob == 0.5
        assert row.outcome == "rejected"
        # "was " is the whole of the " was" token (its space was typed).
        assert row.accepted_chars == 4
        assert row.accepted_tokens == 1


def test_a_suggestion_never_reported_leaves_no_row(
    client, session_factory, monkeypatch
):
    fake_model(monkeypatch, [(" world", lp(0.9))])

    client.post("/api/v1/admin/complete", json={"prefix": "Hello"})

    assert count_rows(session_factory) == 0


def test_an_unsure_completion_shows_nothing_and_is_not_recorded(
    client, session_factory, monkeypatch
):
    fake_model(monkeypatch, [(" perhaps", lp(0.2))])

    response = client.post("/api/v1/admin/complete", json={"prefix": "Hello"})

    assert response.json()["data"] == {"id": None, "suggestion": ""}
    assert count_rows(session_factory) == 0


def test_a_model_failure_shows_nothing(client, monkeypatch):
    fake_model(monkeypatch, error=httpx.ConnectError("model down"))

    response = client.post("/api/v1/admin/complete", json={"prefix": "Hello"})

    assert response.status_code == 200
    assert response.json()["data"] == {"id": None, "suggestion": ""}


def test_unconfigured_autocomplete_answers_503(client, monkeypatch):
    monkeypatch.setattr(config, "AUTOCOMPLETE_URL", "")
    assert (
        client.post("/api/v1/admin/complete", json={"prefix": "Hi"}).status_code == 503
    )


def test_feedback_is_recorded_once(client, session_factory, monkeypatch):
    fake_model(monkeypatch, [(" world", lp(0.9))])
    suggestion_id = client.post(
        "/api/v1/admin/complete", json={"prefix": "Hello"}
    ).json()["data"]["id"]

    first = client.post(
        f"/api/v1/admin/complete/{suggestion_id}/feedback",
        json={"outcome": "accepted", "acceptedChars": 6},
    )
    second = client.post(
        f"/api/v1/admin/complete/{suggestion_id}/feedback",
        json={"outcome": "rejected"},
    )

    assert first.status_code == second.status_code == 204
    with session_factory() as db:
        row = db.get(AutocompleteSuggestion, suggestion_id)
        assert row.outcome == "accepted"
        assert row.accepted_chars == 6
        assert row.accepted_tokens == 1
        assert row.resolved_at is not None


def test_feedback_still_updates_rows_stored_on_generation(client, session_factory):
    # Before suggestions waited for feedback, rows were written right away.
    with session_factory() as db:
        row = AutocompleteSuggestion(
            model_version=VERSION,
            prompt="p",
            completion=" a b c",
            suggestion=" a b c",
            tokens=[[" a", -0.1], [" b", -0.1], [" c", -0.1]],
            latency_ms=1,
        )
        db.add(row)
        db.commit()
        row_id = row.id

    client.post(
        f"/api/v1/admin/complete/{row_id}/feedback",
        json={"outcome": "rejected", "acceptedChars": 4},
    )

    with session_factory() as db:
        row = db.get(AutocompleteSuggestion, row_id)
        assert row.outcome == "rejected"
        assert row.accepted_tokens == 2


def test_feedback_for_an_unknown_suggestion_is_404(client):
    response = client.post(
        "/api/v1/admin/complete/00000000-0000-0000-0000-000000000000/feedback",
        json={"outcome": "rejected"},
    )
    assert response.status_code == 404


def test_autocomplete_needs_the_admin_token():
    client = TestClient(get_application())
    assert client.post("/api/v1/admin/complete", json={"prefix": "Hi"}).status_code in (
        401,
        403,
    )


# ── streaming from the model ─────────────────────────────────────────────────


def sse(tokens, done=True):
    """A vLLM-style completions stream: one token and its logprob per event."""
    events = [
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "text": token,
                        "logprobs": {"tokens": [token], "token_logprobs": [logprob]},
                    }
                ]
            }
        )
        for token, logprob in tokens
    ]
    if done:
        events.append("data: [DONE]")
    return "\n\n".join(events) + "\n\n"


@pytest.fixture
def model_stream(monkeypatch):
    sent = {}

    def serve(body):
        def handler(request):
            sent["json"] = json.loads(request.content)
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )

        monkeypatch.setattr(autocomplete, "_transport", httpx.MockTransport(handler))
        return sent

    monkeypatch.setattr(config, "AUTOCOMPLETE_URL", "http://model.test")
    return serve


def test_streaming_stops_at_the_first_unsure_token(model_stream):
    sent = model_stream(
        sse([(" the", lp(0.9)), (" model", lp(0.2)), (" learns", lp(0.9))])
    )

    result = asyncio.run(autocomplete.complete("prompt", 0.5))

    assert sent["json"]["stream"] is True
    assert sent["json"]["logprobs"] == 0
    assert sent["json"]["stop"] == ["\n", "<think>", "</think>"]
    assert sent["json"]["frequency_penalty"] == config.AUTOCOMPLETE_FREQUENCY_PENALTY
    assert sent["json"]["presence_penalty"] == config.AUTOCOMPLETE_PRESENCE_PENALTY
    # Read up to and including the unsure token, then stopped.
    assert [t for t, _ in result.tokens] == [" the", " model"]
    assert result.text == " the model"
    assert shape_suggestion(result.tokens, "", 0.5) == " the"


def test_streaming_stops_after_a_sentence_end(model_stream):
    model_stream(sse([(" works.", lp(0.9)), (" Then", lp(0.9))]))

    result = asyncio.run(autocomplete.complete("prompt", 0.5))

    assert [t for t, _ in result.tokens] == [" works."]


def test_streaming_reads_to_the_end_when_every_token_is_sure(model_stream):
    model_stream(sse([(" a", lp(0.9)), (" b", lp(0.8))]))

    result = asyncio.run(autocomplete.complete("prompt", 0.5))

    assert result.text == " a b"


def test_streaming_surfaces_http_errors(monkeypatch):
    monkeypatch.setattr(config, "AUTOCOMPLETE_URL", "http://model.test")
    monkeypatch.setattr(
        autocomplete,
        "_transport",
        httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(autocomplete.complete("prompt", 0.5))


# ── where the author stopped, in tokens ─────────────────────────────────────


def test_accepted_tokens_counts_whole_tokens_only():
    tokens = [(" the", 0.0), (" model", 0.0), (" learns", 0.0)]
    completion = " the model learns"
    assert autocomplete.accepted_token_count(tokens, completion, completion, 0) == 0
    assert autocomplete.accepted_token_count(tokens, completion, completion, 4) == 1
    # Halfway into " model" is still one whole token.
    assert autocomplete.accepted_token_count(tokens, completion, completion, 7) == 1
    assert autocomplete.accepted_token_count(tokens, completion, completion, 17) == 3


def test_accepted_tokens_allows_for_the_space_the_author_typed():
    tokens = [(" was", 0.0), (" his", 0.0)]
    # Shown as "was his": " was" is taken whole once "was" is accepted.
    assert autocomplete.accepted_token_count(tokens, " was his", "was his", 3) == 1
    assert autocomplete.accepted_token_count(tokens, " was his", "was his", 7) == 2


def test_pending_suggestions_expire(monkeypatch):
    autocomplete._pending.clear()
    now = [1000.0]
    monkeypatch.setattr(autocomplete.time, "monotonic", lambda: now[0])
    id = autocomplete.remember(
        autocomplete.Pending(
            created_at=datetime.now(timezone.utc),
            note_id=None,
            prompt="",
            completion=Completion(text="", tokens=[], latency_ms=0),
            suggestion="",
            min_prob=0.5,
            model_version="test",
        )
    )
    now[0] += autocomplete.PENDING_TTL_SECONDS + 1
    assert autocomplete.take(id) is None


# ── repetition guard ─────────────────────────────────────────────────────────


def test_an_echo_of_the_sentence_just_typed_is_dropped():
    before = "請你幫我寫一個關於我的個人介紹，要符合台灣大學的學生，"
    assert (
        autocomplete.trim_repetition(before, "要符合台灣大學的學生，不要寫成英文") == ""
    )


def test_a_loop_inside_the_suggestion_is_cut_where_it_starts():
    assert (
        autocomplete.trim_repetition("我想", "寫一篇文章，寫一篇文章，寫一篇")
        == "寫一篇文章，"
    )
    assert (
        autocomplete.trim_repetition("It is", " very good and very good and")
        == " very good and"
    )


def test_ordinary_suggestions_are_untouched():
    before = "SVD factors a matrix into a rotation, a scaling, and another"
    assert autocomplete.trim_repetition(before, " rotation.") == " rotation."
    assert (
        autocomplete.trim_repetition("你最近有沒有任何的", "煩惱心事？") == "煩惱心事？"
    )


def test_repeated_punctuation_is_not_a_loop():
    assert autocomplete.trim_repetition("Title\n", "--------") == "--------"
