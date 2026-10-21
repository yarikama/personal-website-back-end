import logging
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from opentelemetry import trace

# Sign-in URLs carry one-time secrets in their query: Google's code and
# state on /api/v1/auth/google/callback, and on /authorize and the consent
# page the MCP request's id, state and PKCE challenge. The access log and
# the traces keep only their path.
SECRET_QUERY_PREFIX = "/api/v1/auth/"
SECRET_QUERY_PATHS = frozenset({"/authorize"})


def has_secret_query(path: str) -> bool:
    return path.startswith(SECRET_QUERY_PREFIX) or path in SECRET_QUERY_PATHS


def without_secret_query(url: str) -> str:
    """`url` (a full URL, or a path with its query), with the query
    replaced by "redacted" on the sign-in paths."""
    parts = urlsplit(url)
    if parts.query and has_secret_query(parts.path):
        return urlunsplit(parts._replace(query="redacted"))
    return url


class RedactSecretQuery(logging.Filter):
    """
    For uvicorn's access log, whose lines are '%s - "%s %s HTTP/%s" %d'
    (client, method, path with query, HTTP version, status): the path goes
    through without_secret_query.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], without_secret_query(args[2]), *args[3:])
        return True


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        logger_opt = logger.opt(depth=7, exception=record.exc_info)
        logger_opt.log(record.levelname, record.getMessage())


# Loguru's default format, plus the trace id when there is one.
FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>{extra[trace]}"
)


def add_trace_id(record) -> None:
    """
    Loguru patcher: lines logged while handling a traced request end with
    its trace id, so Grafana can jump between a trace and its logs.
    """
    context = trace.get_current_span().get_span_context()
    record["extra"]["trace"] = (
        f" trace_id={context.trace_id:032x}" if context.is_valid else ""
    )
