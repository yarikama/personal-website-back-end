# Personal Website Back-End

[![CI](https://github.com/yarikama/personal-website-back-end/actions/workflows/ci.yaml/badge.svg)](https://github.com/yarikama/personal-website-back-end/actions/workflows/ci.yaml)

The API behind [yarikama.com](https://www.yarikama.com), Henry Hsu's personal website. It serves the projects and notes, answers visitors' questions about them with a self-hosted language model, and gives the author an editor with inline autocomplete from a second model.

It is built with FastAPI and PostgreSQL. It runs on a single-node k3s cluster on a home server, together with its database, Redis, both models and the monitoring stack, and is reached through a Cloudflare Tunnel. The front end is [personal-website-front-end](https://github.com/yarikama/personal-website-front-end).

## What it does

- **Content API**: projects, notes and categories, cached at the edge, plus a contact form that emails the owner.
- **Ask about my work**: `POST /api/v1/ask` streams an answer from Qwen3.5-4B over server-sent events, with checked citations to the projects, notes and resume it used.
- **Note autocomplete**: suggestions from Qwen3.5-0.8B-Base while the author writes. Every suggestion and what became of it is stored as training data.
- **Images**: uploads go to Cloudflare R2. A background worker makes WebP variants of each image.
- **Protection**: rate limits in Redis, a cap on concurrent answers, a daily limit on questions for the whole site, and an admin area behind Sign in with Google.

## Architecture

![Architecture: visitors reach the API through Cloudflare and a tunnel into a single-node k3s cluster at home, where FastAPI talks to PostgreSQL, Redis and two vLLM models on one GPU; GitHub Actions and Argo CD deploy it, and backups and images go to Cloudflare R2](docs/architecture.png)

The two models share one 8 GB laptop GPU, through Kubernetes time-slicing with a fixed memory budget for each. The cluster is managed with GitOps (Argo CD). Its manifests and runbooks live in a separate, private `homelab` repo. References below to "homelab `docs/…`" point there.

The diagram is code: edit [`docs/architecture.py`](docs/architecture.py) and run `uv run --with diagrams python docs/architecture.py` (needs Graphviz). It uses [mingrammer/diagrams](https://github.com/mingrammer/diagrams) and the vLLM logo from the vLLM project.

## Milestones

Built in January 2026 on Cloud Run and Neon, then moved onto a home server in late September 2026, where it grew a GPU and two self-hosted models.

```mermaid
timeline
    title From Cloud Run to a home GPU cluster
    section January 2026
        Jan 19–25 : Personal website and FastAPI API
                  : Projects, notes, admin, image uploads
                  : Cloud Run and Neon
    section September 2026
        Sep 27–28 : Single-node k3s with the GPU on a laptop
                  : API moved home behind Cloudflare Tunnel
                  : Cloud Run removed
        Sep 29 : PostgreSQL on CloudNativePG, backups to R2
               : Argo CD GitOps, Sealed Secrets, deploy on merge
               : Prometheus, Loki, alerts by email
               : Note autocomplete on vLLM (Qwen3.5-0.8B)
        Sep 30 : Rate limits in Redis, edge caching, tracing
               : Image worker for WebP variants
               : GPU time-slicing with priority preemption
               : Ask chat (Qwen3.5-4B, CAG, streamed, cited)
    section October 2026
        Oct 1 : Ask about a highlighted passage
              : Questions kept for review in the admin
              : Follow-ups with conversation memory
              : 24k context, lint, tests and CI on both repos
```

## Roadmap

Planned, not promised; each item has a reason or a trigger.

- [ ] **Learn from real questions.** Rate answers in the admin's Questions page, and grow the 40-question evaluation set in [`eval/ask/`](eval/ask/) from them.
- [ ] **Retrieval when the content outgrows the context.** Once the prompt nears its budget (the `AskPromptNearLimit` alert), the next step is a narrow agent with two tools, `search` over PostgreSQL full-text and `read` by document id. The evaluation set decides whether it beats reading everything.
- [ ] **Autocomplete, data and evaluation.** An admin page for suggestions and their outcomes, and offline evaluation against what was actually written.
- [ ] **Autocomplete, fine-tuning.** LoRA on the home GPU as a Kubernetes Job that pre-empts both models, with adapters stored in R2 and served by vLLM.
- [ ] **Autocomplete, reinforcement learning.** DPO from accepted and rejected suggestions, then GRPO with a verifiable reward, and A/B tests of adapters.
- [ ] **A small decision model.** Classify contact messages with a fine-tuned encoder on the CPU, compared with the answer model's structured outputs.

## Design notes

**Ask: cache-augmented generation, not RAG.** All published content, about 13k tokens (October 2026), fits in the model's 24k context. Instead of retrieving chunks, [`services/ask.py`](app/services/ask.py) puts every published project and note, plus the resume, into the system prompt. That prefix is the same for every question, so vLLM's prefix cache computes it once: the first token arrives in about 0.1–0.2 s once the cache is warm.
- **Freshness.** The prompt is rebuilt whenever published content changes, and drafts never enter it.
- **Citations.** The model cites `[P1]`, `[N1]` or `[R1]`. Citations to ids that don't exist are dropped before they reach the client.
- **Size guard.** A token estimate guards the context window. The `ask_prompt_tokens` metric triggers an alert well before the limit. If the limit is reached anyway, the oldest notes are left out first, so the chat keeps working.
- **Highlighted passages.** The backend works out which document a passage comes from: a note's page, or else the one document containing the text. It tells the model, and lists that document first among the citations even when the model forgets to cite it.
- **Short memory.** A follow-up sees the conversation's last two questions and answers. They are kept in Redis for 30 minutes under a random conversation id, which the browser sends back; the browser never sends the history itself, so it can't be forged. The prompt budget keeps room for them, and without Redis, questions are answered without history.
- **Questions are kept.** Each question, its answer and citations go to the `ask_questions` table for 30 days, without the visitor's address. The admin area lists them, filters for answers that cite nothing or broke off, and rates answers good or bad, which turns real questions into evaluation data.
- **Injection.** The visitor's question and any highlighted passage are treated as untrusted. The model has no tools, so an injected instruction can only change the text of an answer.
- **Model choice.** The model was chosen, and prompt changes are checked, with a 40-question evaluation set in [`eval/ask/`](eval/ask/).

**Autocomplete that knows when to stop.** [`services/autocomplete.py`](app/services/autocomplete.py) streams the completion token by token, using the last 2,000 characters before the cursor.
- **Stopping early.** It stops at the first token whose probability is below a threshold (0.5 by default), at the end of a sentence, or where the model starts repeating the text before it. Closing the stream also makes vLLM abort the rest of the generation.
- **Failures.** A slow or failed model gives an empty suggestion, never an error.
- **Training data.** The outcome of each suggestion (accepted, typed along, rejected or ignored, and how many tokens were taken) is stored for later fine-tuning.

**Rate limits that hold under concurrency.** Each check is a single Lua script in Redis, so reading, deciding and recording happen atomically ([`services/rate_limit.py`](app/services/rate_limit.py)). Two algorithms are used: sliding logs for small, exact budgets, and token buckets for bursts. Each rule chooses what happens when Redis is down; today's all fail open. The admin, whose sessions live in Redis, gets 503 instead. A 429 still carries CORS headers, so the browser can show the message instead of a network error.

**Background jobs on Redis Streams.** [`services/jobs.py`](app/services/jobs.py) is a small at-least-once queue built on a consumer group.
- **Retries.** A job is acknowledged only after it succeeds. A job whose worker died is claimed again by another worker (`XAUTOCLAIM`).
- **Dead letters.** Jobs that keep failing move to a dead-letter stream.
- **Recovery.** Redis keeps no data across restarts, so the worker reconciles with R2 on start and every hour, queuing any image still missing its variants.

**Edge caching without stale CORS.** Public `GET`s answer with an `ETag` and `s-maxage=60, stale-while-revalidate=600`, so Cloudflare serves most traffic. Cloudflare's cache ignores `Vary: Origin`, so these responses use `Access-Control-Allow-Origin: *` instead of echoing the origin ([`api/cache.py`](app/api/cache.py)).

**Observability.**
- **Traces.** OpenTelemetry traces every request, with spans for SQL, Redis and model calls. Log lines inside a request carry its `trace_id`.
- **Metrics.** Prometheus metrics (answers, tokens, time to first token, prompt size, queue depth) are served on a separate port, so the public Ingress never exposes them.

**Security.**
- The admin signs in with Google: the API runs the OAuth authorization code flow with PKCE, a one-time state bound to the browser by a cookie, and a nonce, and lets in only the accounts in `ADMIN_EMAILS` ([`services/google_oauth.py`](app/services/google_oauth.py)). The site loads no script from Google.
- The session is an `HttpOnly`, `Secure`, `SameSite=Strict` `__Host-` cookie, so page scripts cannot read it. Redis keeps only its SHA-256, and signing out deletes it at once. A request that changes something must also come from the site's own origin.
- Request bodies are capped before anything reads them: 20 MB for image uploads, 2 MB for everything else (413 above that).
- An upload is stored as what its bytes are (JPEG, PNG, GIF or WebP, checked with Pillow), never as the file name or type the browser claims, and only in the known folders (`images`, `notes`, `covers`).
- CI runs `pip-audit` against the locked dependencies.
- Drafts are filtered out of every public endpoint and of the chat prompt, and tests check this.
- Every response carries HSTS, `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; API responses also get `Content-Security-Policy: default-src 'none'`, since they never load anything.
- The production image runs as an unprivileged user.
- The deploy key that can change the cluster is only available to workflows on `main`.

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

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make install                  # dependencies; also creates .env.local and .env.prod from .env.example
cp .env.example .env          # make run reads .env
docker-compose up -d db redis # local PostgreSQL on :5432 (postgres/postgres, database app) and Redis on :6379
make run                      # API with hot reload on http://localhost:8080
```

- Interactive docs: http://localhost:8080/docs.
- **Rate limits** are off unless `REDIS_URL` is set (for example `redis://localhost:6379/0`).
- **Ask and autocomplete** answer `503` until `ASK_URL` and `AUTOCOMPLETE_URL` point at an OpenAI-compatible server.

Which env file is read depends on how the app starts:
- `make run`, and anything run from the repo root, reads `.env`.
- The Docker stack (`make deploy`) reads `.env.<ENV>`, which is `.env.local` by default.
- `.env.prod` is the source for the production Secret (see [Deployment](#deployment)).

All three are gitignored.

### Docker

```bash
make deploy     # the app, PostgreSQL and Redis in Docker, with hot reload
make logs       # follow the logs
make shell      # a shell in the app container
make rebuild    # rebuild the image after changing dependencies
make down       # stop everything
```

## Testing

```bash
docker-compose up -d db
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/app make test
make lint       # ruff check and ruff format --check
make format     # fix what can be fixed
```

**Database tests.** Most tests are self-contained. The ones that check queries against a real PostgreSQL (project order, drafts by slug) default to `localhost:5432/test_db`, which the compose database does not create; that is why the command above sets `DATABASE_URL`.

**Redis tests.** The rate-limit tests run their Lua scripts in fakeredis by default. Set `REDIS_TEST_URL=redis://localhost:6379/15` to run them against a real Redis as well. That database is flushed.

**CI.** [GitHub Actions](.github/workflows/ci.yaml) runs Ruff, then the tests on Python 3.10, 3.11 and 3.12 against PostgreSQL and Redis service containers.

## Deployment

**Merging to `main` is the deploy.** It takes about five minutes from merge to live.

1. CI runs lint and tests, then builds the production image for `linux/amd64` and pushes it to GHCR as `ghcr.io/yarikama/website-backend:sha-<short>` and `latest`.
2. The `deploy` job commits the new tag to `apps/website-backend/kustomization.yaml` in the homelab repo. It authenticates with a deploy key held in the `production` environment, which only `main` can use. Two quick merges queue rather than race.
3. Argo CD picks up that commit within about three minutes and rolls the pods. The new pod must pass `/health` before the old one stops, so there is no downtime.

**Rolling back.** `git revert` the `deploy(website-backend): sha-…` commit in the homelab repo. `kubectl rollout undo` does not stick, because Argo CD restores what Git says.

**Configuration.** Production configuration is a Sealed Secret in the homelab repo, generated from `.env.prod` by `scripts/create-secret.sh` there. `DATABASE_URL` is not part of it: it comes from the Secret that CloudNativePG generates.

**Migrations** run on their own: the pod's init container runs `alembic upgrade head` before the API starts. To run Alembic by hand against production, run it inside the pod, which already has the right `DATABASE_URL`:

```bash
kubectl -n website exec deploy/website-backend -c api -- alembic -c alembic.ini current
```

**The image worker** runs from the same image with `python -m worker`.

**Database backups.** WAL is archived continuously to the Cloudflare R2 bucket `yarikama-db-backups`, and a base backup is taken daily. The database can be restored to any point in the last 30 days, and the restore was tested before production moved onto it. Manifests and the restore runbook: homelab `apps/website-db` and `docs/05-database.md`.

### Migrations locally

```bash
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini upgrade head
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini revision --autogenerate -m "description"
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini downgrade -1
```

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

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string (`postgresql://…`; the app picks the psycopg 3 driver). In production it comes from CloudNativePG, not from `.env.prod` | `postgresql://postgres:postgres@localhost:5432/app` (docker-compose's `db`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client (type "Web application") for Sign in with Google; empty turns it off | empty |
| `GOOGLE_REDIRECT_URI` | The callback registered with that client | `https://api.yarikama.com/api/v1/auth/google/callback` |
| `ADMIN_EMAILS` | Comma-separated Google accounts that may sign in | empty |
| `SITE_URL` | Where the browser goes after signing in | `https://yarikama.com` |
| `ADMIN_SESSION_HOURS` | How long a Google sign-in lasts | `12` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` | Cloudflare R2, for image uploads | bucket `yarikama-portfolio-backend` |
| `DEBUG` | Debug mode | `False` |
| `VERCEL_TOKEN` | Vercel access token, for the site's visitors (Web Analytics) in the admin; empty turns `/admin/visitors` off (503) | empty |
| `VERCEL_PROJECT`, `VERCEL_TEAM` | The front end's Vercel project (name or id) and its team's slug | `portfolio`, `yarikamas-projects` |
| `REDIS_URL` | Redis for rate limits and the job queue, e.g. `redis://:password@host:6379/0`; empty turns rate limiting off | empty |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server (STARTTLS) for contact-form notifications | `smtp.gmail.com`, `587` |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `CONTACT_NOTIFY_TO` | Sender account (for Gmail: the address and an app password) and who gets an email per contact message; if any is empty, no email is sent | empty |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP endpoint for traces (e.g. Grafana Tempo, `http://tempo:4318`); empty turns tracing off | empty |
| `OTEL_SERVICE_NAME` | Service name on the traces | `website-backend` |
| `METRICS_PORT` | Serve Prometheus metrics on this port, apart from the API; `0` turns them off | `0` |
| `AUTOCOMPLETE_URL` | OpenAI-compatible completions server for note autocomplete; empty disables it (503) | empty |
| `AUTOCOMPLETE_MODEL`, `AUTOCOMPLETE_MODEL_VERSION` | Model name to request, and the version recorded with each suggestion | `autocomplete`, `unknown` |
| `AUTOCOMPLETE_MIN_TOKEN_PROB` | Suggestions stop at the first token less likely than this | `0.5` |
| `AUTOCOMPLETE_FREQUENCY_PENALTY`, `AUTOCOMPLETE_PRESENCE_PENALTY` | Discourage repeating what the suggestion itself already wrote (not the note) | `2.0`, `1.0` |
| `ASK_URL` | OpenAI-compatible chat server with an instruct model, for the ask chat; empty disables it (503) | empty |
| `ASK_MODEL`, `ASK_MAX_TOKENS`, `ASK_TEMPERATURE` | Model name to request, answer length cap in tokens (Chinese takes about one per character), sampling temperature | `ask`, `800`, `0.3` |
| `ASK_CONTEXT_TOKENS` | The answer model's context length (vLLM `--max-model-len`); the system prompt gets what the question and the answer leave | `16384` |
| `ASK_MAX_CONCURRENT` | Answers generated at once; past this the API answers `503` right away | `4` |
| `ASK_QUESTION_RETENTION_DAYS` | How long questions asked in the chat are kept in `ask_questions` | `30` |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness, for Kubernetes probes and uptime monitoring: `{"status": "ok"}` |
| `GET /docs`, `GET /redoc` | Interactive API docs |
| `GET /api/v1/auth/google/login?next=/admin/...` | Starts Sign in with Google; Google then sends the browser to `/api/v1/auth/google/callback`, which sets the session cookie and returns to `next` |
| `GET /api/v1/auth/me` | `{"email"}` of whoever is signed in; `401` if no one |
| `POST /api/v1/auth/logout` | Ends the session (`204`) |
| `GET /api/v1/projects`, `GET /api/v1/projects/{slug}` | Published projects, in display order |
| `GET /api/v1/lab-notes`, `GET /api/v1/lab-notes/{slug}`, `GET /api/v1/lab-notes/tags` | Published notes, and their tags with counts |
| `GET /api/v1/categories` | Project categories |
| `POST /api/v1/contact` | The contact form. The owner gets an email with the message; replying answers the visitor |
| `GET /api/v1/admin/contact?read=false` | Contact messages, newest first; `read` filters. `PATCH /api/v1/admin/contact/{id}` with `{"read"}` or `{"replied"}`, `DELETE` to remove one |
| `POST /api/v1/ask` | A question about the owner's work, answered as a server-sent event stream (below). `503` when the model is offline or busy |
| `POST /api/v1/csp-report` | Where browsers report Content-Security-Policy violations from the site (both the `report-uri` and Reporting API formats); each becomes a log line and a `csp_reports_total` count |
| `/api/v1/admin/...` | Create, edit, reorder and delete content, list contact messages, upload images. Needs the session cookie; anything but a GET must also come from the site's own origin |
| `GET /api/v1/admin/ask/questions` | Questions asked in the chat, newest first. Filters: `who` (`visitors`, the default, `admin` or `all`), `uncited`, `passage`, `failed` (cut off or broken off), `rating` (`good`, `bad` or `none`) |
| `PATCH /api/v1/admin/ask/questions/{id}` | `{"rating": "good" \| "bad" \| null}` |
| `GET /api/v1/admin/github/contributions` | The owner's public GitHub contribution graph (`GITHUB_USER`), for the welcome page: `{"user", "total", "days": [{"date", "count", "level"}]}`, fetched at most once an hour |
| `GET /api/v1/admin/todos?day=YYYY-MM-DD`, `POST`, `PATCH /{id}`, `DELETE /{id}` | The welcome page's to-dos: each day starts with the daily items (a NeetCode problem), anything not done carries over, and what was done shows for that day |
| `GET`, `PUT /api/v1/admin/goal` | The owner's current goal, a few words shown large on the welcome page: `{"text"}`; empty clears it |
| `GET /api/v1/admin/visitors?days=7` | The site's visitors over the last 1-30 days to today (UTC), from Vercel Web Analytics (production, without the owner): `{"since", "until", "pageviews", "visitors", "days": [{"date", "pageviews", "visitors"}], "pages", "referrers", "countries", "devices"}`, the last four the top ten as `[{"name", "pageviews", "visitors"}]` (an empty referrer is none). Asked at most every ten minutes; `503` without `VERCEL_TOKEN`, `502` if Vercel fails |
| `GET /api/v1/admin/ask/questions/new`, `POST .../seen` | How many visitors asked since this admin last opened Questions (`{"count", "since"}`); opening it records the visit and returns the previous one (`{"previous"}`) |
| `POST /api/v1/admin/complete` | Note autocomplete: `{"prefix", "title", "noteId"}` → `{"id", "suggestion"}` (empty when the model is unsure or unavailable) |
| `POST /api/v1/admin/complete/{id}/feedback` | `{"outcome": "accepted" \| "rejected" \| "ignored", "acceptedChars"}`, recorded once per suggestion |

Lists return `{"data": [...], "pagination": {"total", "limit", "offset", "hasMore"}}`; categories return `data` only. Single items return `{"data": {...}}`. Projects and notes use camelCase fields.

### The ask stream

Request: `{"question", "quote"?, "page"?, "conversation"?}`.
- `question`: up to 500 characters.
- `quote`: optional; a passage the visitor highlighted on the site, up to 600 characters.
- `page`: optional; the path the passage is on.
- `conversation`: optional; the id from the previous answer's `done` event, to continue that conversation. An unknown or expired id starts a new one.

Response: a stream of server-sent events.

| Event | Data |
|---|---|
| `token` | `{"text"}`: the next piece of the answer |
| `done` | `{"citations": [{"id", "kind", "title", "url"}], "truncated", "conversation"}`: the sources the answer cited, in order; for a passage, the document it comes from is first. `truncated` means the answer hit `ASK_MAX_TOKENS`; `conversation` is the id to send with a follow-up |
| `error` | `{"detail"}`: the answer broke off |

Design and when to switch to retrieval: homelab `docs/14-ask-chat-plan.md`.

### Rate limits

Limits are counted per visitor: the `CF-Connecting-IP` address that Cloudflare sets, with IPv6 grouped by /64. They are kept in Redis, so every replica shares them. Over a limit, the API answers `429` with `Retry-After` in seconds and a readable `detail`.

| Rule | Limit | Algorithm | If Redis is down |
|------|-------|-----------|------------------|
| Contact form | 3 messages per hour | Sliding log | Allow |
| Chat questions | 10 per hour per visitor, and 500 a day for the whole site; not counted for the signed-in admin | Sliding log; token bucket | Allow |
| Every other `/api/` request except `/api/v1/admin/*` and preflights | Bursts of 60, then 1 per second | Token bucket | Allow |

Design and trade-offs: homelab `docs/11-rate-limiting.md`.

### Image variants

Uploaded JPEG, PNG and WebP images get WebP variants 640 and 1600 px wide (`<name>.w640.webp`, `<name>.w1600.webp`), never upscaled. The site loads them with `srcset` and falls back to the original until they exist.

### Caching

`GET` on projects, notes and categories answers with an `ETag` and `Cache-Control: public, max-age=0, s-maxage=60, stale-while-revalidate=600`.
- **Browsers** revalidate on every request, and get a `304` when nothing changed.
- **Cloudflare** keeps a copy for a minute, so edits in the admin show up publicly within about a minute.

## Make commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies and create the env files |
| `make run` | Local dev server with hot reload |
| `make test` | Run the tests |
| `make lint` / `make format` | Check / fix code style |
| `make deploy` / `make down` | Start / stop the local Docker Compose stack (not production) |
| `make logs` / `make shell` / `make rebuild` | Docker logs, a shell in the container, rebuild the image |
| `make revision` / `make upgrade` / `make downgrade` | Alembic migrations |
| `make clean` | Remove caches |

## Known limitations

- The `request_logs` table is left over from a removed ML predictor. It is empty and unused; the model stays only so Alembic does not propose dropping it.
