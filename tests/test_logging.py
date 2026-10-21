"""What the access log and the traces keep of a request's URL."""

import logging

import pytest

from core import config  # noqa: F401  (installs the access log's filter)
from core.logging import RedactSecretQuery, without_secret_query


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        (
            "/api/v1/auth/google/callback?code=c&state=s",
            "/api/v1/auth/google/callback?redacted",
        ),
        ("/api/v1/auth/mcp/consent?mcp=id", "/api/v1/auth/mcp/consent?redacted"),
        (
            "https://api.yarikama.com/authorize?code_challenge=x&state=s",
            "https://api.yarikama.com/authorize?redacted",
        ),
        ("/api/v1/auth/me", "/api/v1/auth/me"),
        ("/api/v1/lab-notes?tag=GPU&limit=3", "/api/v1/lab-notes?tag=GPU&limit=3"),
        ("/authorized-elsewhere?x=1", "/authorized-elsewhere?x=1"),
    ],
)
def test_only_sign_in_urls_lose_their_query(url, kept):
    assert without_secret_query(url) == kept


def test_the_access_log_line_loses_the_query():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", "/api/v1/auth/google/callback?code=c", "1.1", 303),
        None,
    )
    assert RedactSecretQuery().filter(record)
    assert record.getMessage() == (
        '127.0.0.1:1 - "GET /api/v1/auth/google/callback?redacted HTTP/1.1" 303'
    )


def test_other_log_records_pass_unchanged():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s", ("text",), None
    )
    assert RedactSecretQuery().filter(record)
    assert record.args == ("text",)


def test_uvicorns_access_log_has_the_filter():
    filters = logging.getLogger("uvicorn.access").filters
    assert any(isinstance(f, RedactSecretQuery) for f in filters)
