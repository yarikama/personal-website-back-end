from typing import Annotated

from pydantic import StringConstraints

from schemas.base import BaseSchema
from services.ask import MAX_QUESTION_CHARS, MAX_QUOTE_CHARS


class AskRequest(BaseSchema):
    # Stripped before the length checks: surrounding whitespace neither
    # counts toward the limit nor makes a blank question valid.
    question: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS
        ),
    ]
    # A passage the visitor highlighted on the site, and the path of the
    # page it is on, so the model knows which note or project it is from.
    quote: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=MAX_QUOTE_CHARS
            ),
        ]
        | None
    ) = None
    page: (
        Annotated[str, StringConstraints(max_length=200, pattern=r"^/[\w\-./%]*$")]
        | None
    ) = None
    # The id the previous answer's done event gave, to continue that
    # conversation (services/ask_history.py).
    conversation: (
        Annotated[str, StringConstraints(max_length=64, pattern=r"^[\w-]+$")] | None
    ) = None
