# Architecture

![Architecture: visitors reach the API through Cloudflare and a tunnel into a single-node k3s cluster at home, where FastAPI talks to PostgreSQL, Redis and two vLLM models on one GPU; GitHub Actions and Argo CD deploy it, and backups and images go to Cloudflare R2](architecture.png)

The two models share one 8 GB laptop GPU, through Kubernetes time-slicing with a fixed memory budget for each. The cluster is managed with GitOps (Argo CD). Its manifests and runbooks live in a separate, private `homelab` repo. References in these docs to "homelab `docs/…`" point there.

The diagram is code: edit [`architecture.py`](architecture.py) and run `uv run --extra docs python docs/architecture.py` (needs Graphviz). It uses [mingrammer/diagrams](https://github.com/mingrammer/diagrams) and the vLLM logo from the vLLM project.

## Tech stack

| Area | Choice |
|---|---|
| API | FastAPI, Pydantic 2, Uvicorn |
| Data | PostgreSQL 17 (CloudNativePG), SQLAlchemy 2 (asyncio, psycopg 3), Alembic |
| Cache and queue | Redis (Lua scripts, Streams) |
| Models | vLLM, OpenAI-compatible API: Qwen3.5-4B AWQ and Qwen3.5-0.8B-Base |
| Storage | Cloudflare R2 (S3 API through aioboto3), Pillow for WebP |
| Observability | OpenTelemetry, Prometheus client, Loguru |
| Tooling | uv, Ruff, pytest, pre-commit, GitHub Actions |
| Runtime | Docker, k3s, Argo CD, Cloudflare Tunnel |

## Project structure

```
app/
├── main.py                 App factory: middleware (rate limit, CORS, edge cache) and routers
├── worker.py               Background worker for image variants (python -m worker)
├── api/
│   ├── routes/             Endpoints: content, auth, contact, upload, ask, autocomplete, health
│   ├── dependencies/       Admin auth, per-route rate limits
│   ├── middleware.py       The site-wide public rate limit
│   └── cache.py            ETag, Cache-Control and CORS for cacheable responses
├── services/
│   ├── ask.py              Prompt snapshot, citation checking, streaming answers
│   ├── ask_log.py          Keeping each question and answer for 30 days
│   ├── ask_history.py      A conversation's last two turns, in Redis
│   ├── autocomplete.py     Prompting, confidence cut-off, repetition trimming
│   ├── rate_limit.py       Lua-scripted sliding logs and token buckets
│   ├── google_oauth.py     Sign in with Google (authorization code flow, PKCE)
│   ├── admin_session.py    Admin sessions in Redis
│   ├── jobs.py             At-least-once queue on Redis Streams
│   ├── image_jobs.py       Image jobs and reconciliation with R2
│   ├── images.py           WebP variants with Pillow
│   ├── storage.py          Cloudflare R2
│   └── notify.py           Contact-form emails
├── core/                   Settings, tracing, logging, startup
├── db/models/              SQLAlchemy models
├── schemas/                Pydantic request and response models (camelCase JSON)
├── alembic/                Migrations
└── content/resume.md       The resume the chat answers from
eval/ask/                   The chat's evaluation questions and scripts
tests/                      pytest suite
```
