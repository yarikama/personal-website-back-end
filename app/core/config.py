import logging
import os
import sys

from core.logging import FORMAT, InterceptHandler, add_trace_id
from loguru import logger
from starlette.config import Config
from starlette.datastructures import CommaSeparatedStrings, Secret

# Local development keeps its settings in .env. In the cluster they come from
# the environment and there is no file; Starlette warns about a missing one.
config = Config(".env" if os.path.isfile(".env") else None)

API_PREFIX = "/api"
VERSION = "0.1.0"
DEBUG: bool = config("DEBUG", cast=bool, default=False)
# The default is docker-compose's db. The models use PostgreSQL types (ARRAY,
# JSONB), so no other database would work.
DATABASE_URL: str = config(
    "DATABASE_URL", default="postgresql://postgres:postgres@localhost:5432/app"
)

PROJECT_NAME: str = config("PROJECT_NAME", default="Personal-Website-Back-End")

# Sign in with Google (api/routes/auth.py): an OAuth client of type "Web
# application" whose authorized redirect URI is GOOGLE_REDIRECT_URI. Only
# the Google accounts in ADMIN_EMAILS get in. Any of the three empty turns
# Google sign-in off.
GOOGLE_CLIENT_ID: str = config("GOOGLE_CLIENT_ID", default="")
# Secret(config(...)) rather than cast=Secret: the same value, typed Secret.
# (Starlette casts the default too, so default=Secret("") would wrap it twice
# and make str() raise.)
GOOGLE_CLIENT_SECRET: Secret = Secret(config("GOOGLE_CLIENT_SECRET", default=""))
GOOGLE_REDIRECT_URI: str = config(
    "GOOGLE_REDIRECT_URI",
    default="https://api.yarikama.com/api/v1/auth/google/callback",
)
ADMIN_EMAILS: frozenset[str] = frozenset(
    email.strip().lower()
    for email in config("ADMIN_EMAILS", cast=CommaSeparatedStrings, default="")
    if email.strip()
)
# Where the browser goes after signing in (its /admin pages).
SITE_URL: str = config("SITE_URL", default="https://yarikama.com")
# How long a sign-in lasts. Sessions live in Redis, so signing out ends one
# at once.
ADMIN_SESSION_HOURS: int = config("ADMIN_SESSION_HOURS", cast=int, default=12)

# Whose public GitHub contribution graph the admin's welcome page shows.
GITHUB_USER: str = config("GITHUB_USER", default="yarikama")

# The site's visitors, for the admin, from Vercel Web Analytics: a Vercel
# access token, and the front end's project (name or id) and team (slug).
# An empty token turns it off.
VERCEL_TOKEN: Secret = Secret(config("VERCEL_TOKEN", default=""))
# The project id, not its name: the id survives renaming the project.
VERCEL_PROJECT: str = config(
    "VERCEL_PROJECT", default="prj_Q4QGU1c6k6g01UhBxVTL43ZyuzBP"
)
VERCEL_TEAM: str = config("VERCEL_TEAM", default="yarikamas-projects")

# Pages allowed to call the API from a browser (CORS), and to send the admin
# session cookie with requests that change something.
CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",  # Local frontend dev
    "http://localhost:5173",  # Vite dev server
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "https://yarikama.com",  # Production frontend
    "https://www.yarikama.com",
)

# Rate limits (services.rate_limit) are kept in Redis, e.g.
# redis://:password@host:6379/0. Empty turns them off (local development).
REDIS_URL: Secret = Secret(config("REDIS_URL", default=""))
# Email about new contact messages, over SMTP with STARTTLS (for Gmail: the
# address and an app password). Any of user, password or recipient empty
# turns it off; messages are still saved.
SMTP_HOST: str = config("SMTP_HOST", default="smtp.gmail.com")
SMTP_PORT: int = config("SMTP_PORT", cast=int, default=587)
SMTP_USERNAME: str = config("SMTP_USERNAME", default="")
SMTP_PASSWORD: Secret = Secret(config("SMTP_PASSWORD", default=""))
CONTACT_NOTIFY_TO: str = config("CONTACT_NOTIFY_TO", default="")

# Prometheus metrics on this port, separate from the API port so the public
# Ingress never exposes them. 0 turns them off.
METRICS_PORT: int = config("METRICS_PORT", cast=int, default=0)

# logging configuration
LOGGING_LEVEL = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    handlers=[InterceptHandler(level=LOGGING_LEVEL)], level=LOGGING_LEVEL
)
logger.configure(
    handlers=[{"sink": sys.stderr, "level": LOGGING_LEVEL, "format": FORMAT}],
    patcher=add_trace_id,
)


# R2 Storage configuration
R2_ACCOUNT_ID: str = config("R2_ACCOUNT_ID", default="")
R2_ACCESS_KEY_ID: str = config("R2_ACCESS_KEY_ID", default="")
R2_SECRET_ACCESS_KEY: str = config("R2_SECRET_ACCESS_KEY", default="")
R2_BUCKET_NAME: str = config("R2_BUCKET_NAME", default="yarikama-website-backend")
R2_PUBLIC_URL: str = config("R2_PUBLIC_URL", default="")

# Note autocomplete: an OpenAI-compatible completions server (vLLM in the
# homelab cluster). Empty disables the feature; the endpoint then answers 503.
AUTOCOMPLETE_URL: str = config("AUTOCOMPLETE_URL", default="")
AUTOCOMPLETE_MODEL: str = config("AUTOCOMPLETE_MODEL", default="autocomplete")
# Recorded with every suggestion, to compare models and adapters later.
AUTOCOMPLETE_MODEL_VERSION: str = config(
    "AUTOCOMPLETE_MODEL_VERSION", default="unknown"
)
# A suggestion keeps tokens while each one's probability stays at or above
# this. In the bake-off 0.5 showed a suggestion about half the time, and about
# half of those were exactly what was written next; lower shows more and
# longer suggestions that are right less often (homelab
# docs/10-autocomplete-plan.md). Recorded with every suggestion.
# Discourage the model from repeating what it just wrote in the same
# suggestion (it looped on phrases like "要符合台灣大學的學生，"). Both count
# only the suggestion's own tokens, not the note: frequency per repeat,
# presence once per token already used. 2.0 and 1.0 removed every loop seen
# in real suggestions with no loss of accuracy, while repetition_penalty,
# which also counts the note, cost 10 points of first-word accuracy (homelab
# docs/10-autocomplete-plan.md). services.autocomplete.trim_repetition is the
# safety net for what gets through.
AUTOCOMPLETE_FREQUENCY_PENALTY: float = config(
    "AUTOCOMPLETE_FREQUENCY_PENALTY", cast=float, default=2.0
)
AUTOCOMPLETE_PRESENCE_PENALTY: float = config(
    "AUTOCOMPLETE_PRESENCE_PENALTY", cast=float, default=1.0
)
AUTOCOMPLETE_MIN_TOKEN_PROB: float = config(
    "AUTOCOMPLETE_MIN_TOKEN_PROB", cast=float, default=0.5
)

# "Ask about my work" chat: an OpenAI-compatible chat server with an
# instruct model (vLLM in the homelab cluster). Empty disables the feature;
# the endpoint then answers 503. Design: homelab docs/14-ask-chat-plan.md.
ASK_URL: str = config("ASK_URL", default="")
ASK_MODEL: str = config("ASK_MODEL", default="ask")
# 400 cut a Chinese overview of his research mid-sentence: Chinese takes
# about a token per character. 800 is about 14 s at 56 tokens/s.
ASK_MAX_TOKENS: int = config("ASK_MAX_TOKENS", cast=int, default=800)
ASK_TEMPERATURE: float = config("ASK_TEMPERATURE", cast=float, default=0.3)
# The answer model's context (vLLM's --max-model-len). The system prompt,
# which holds every published document, gets what the question and the
# answer leave; past that the oldest documents are left out.
ASK_CONTEXT_TOKENS: int = config("ASK_CONTEXT_TOKENS", cast=int, default=16384)
# Answers generated at once; one more gets 503 at once instead of waiting
# behind them on a GPU that cannot go any faster.
ASK_MAX_CONCURRENT: int = config("ASK_MAX_CONCURRENT", cast=int, default=4)
# Questions asked in the chat are kept this long (services/ask_log.py).
ASK_QUESTION_RETENTION_DAYS: int = config(
    "ASK_QUESTION_RETENTION_DAYS", cast=int, default=30
)
