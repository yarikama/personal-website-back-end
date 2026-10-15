import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from api.dependencies import CurrentAdmin
from core import config
from db.dependency import get_db
from db.models.autocomplete import AutocompleteSuggestion
from fastapi import APIRouter, Depends, HTTPException, Response
from schemas.autocomplete import (
    CompletionRequest,
    CompletionResponse,
    SuggestionFeedback,
)
from services import autocomplete
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/admin/complete")
async def complete(request: CompletionRequest, _admin: CurrentAdmin):
    """Suggest how the note continues from the cursor.

    Every failure degrades to "no suggestion": the editor must keep working
    whatever happens to the model, and the model has its own alert.
    """
    if not config.AUTOCOMPLETE_URL:
        raise HTTPException(status_code=503, detail="Autocomplete is not configured")

    empty = {
        "data": CompletionResponse(id=None, suggestion="").model_dump(by_alias=True)
    }
    prompt = autocomplete.build_prompt(request.prefix, request.title)
    if not request.prefix.strip():
        return empty

    try:
        completion = await autocomplete.complete(
            prompt.text, config.AUTOCOMPLETE_MIN_TOKEN_PROB
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as err:
        logger.warning("autocomplete request failed: %s", err)
        return empty

    suggestion = autocomplete.shape_suggestion(
        completion.tokens, prompt.trailing_space, config.AUTOCOMPLETE_MIN_TOKEN_PROB
    )
    suggestion = autocomplete.trim_repetition(request.prefix, suggestion)
    if not suggestion:
        return empty

    # Not stored yet: only once the editor reports back (see feedback), so
    # suggestions that were never shown leave no row.
    id = autocomplete.remember(
        autocomplete.Pending(
            created_at=datetime.now(timezone.utc),
            note_id=request.note_id,
            prompt=prompt.text,
            completion=completion,
            suggestion=suggestion,
            min_prob=config.AUTOCOMPLETE_MIN_TOKEN_PROB,
            model_version=config.AUTOCOMPLETE_MODEL_VERSION,
        )
    )
    return {
        "data": CompletionResponse(id=id, suggestion=suggestion).model_dump(
            by_alias=True, mode="json"
        )
    }


@router.post("/admin/complete/{id}/feedback", status_code=204)
async def feedback(
    id: UUID,
    body: SuggestionFeedback,
    _admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
):
    """Record a shown suggestion and what became of it, including where the
    author stopped taking it. Only the first report counts."""
    now = datetime.now(timezone.utc)
    pending = autocomplete.take(id)
    if pending is not None:
        completion = pending.completion
        db.add(
            AutocompleteSuggestion(
                id=id,
                created_at=pending.created_at,
                model_version=pending.model_version,
                note_id=pending.note_id,
                prompt=pending.prompt,
                completion=completion.text,
                suggestion=pending.suggestion,
                tokens=[[token, logprob] for token, logprob in completion.tokens],
                latency_ms=completion.latency_ms,
                min_token_prob=pending.min_prob,
                outcome=body.outcome,
                accepted_chars=body.accepted_chars,
                accepted_tokens=_accepted_tokens(
                    completion.tokens,
                    completion.text,
                    pending.suggestion,
                    body.accepted_chars,
                ),
                resolved_at=now,
            )
        )
        await db.commit()
        return Response(status_code=204)

    # Already recorded (a repeated report), or stored before suggestions
    # waited for feedback.
    row = await db.get(AutocompleteSuggestion, id)
    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if row.outcome is None:
        row.outcome = body.outcome
        row.accepted_chars = body.accepted_chars
        row.accepted_tokens = _accepted_tokens(
            [tuple(t) for t in row.tokens or []],
            row.completion,
            row.suggestion,
            body.accepted_chars,
        )
        row.resolved_at = now
        await db.commit()
    return Response(status_code=204)


def _accepted_tokens(tokens, completion, suggestion, accepted_chars):
    if accepted_chars is None:
        return None
    return autocomplete.accepted_token_count(
        tokens, completion, suggestion, accepted_chars
    )
