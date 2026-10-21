# Configuration

Settings come from environment variables. Which env file is read locally is described in [Development → Env files](development.md#env-files); production's come from a Sealed Secret ([Deployment → Configuration](deployment.md#configuration)).

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string (`postgresql://…`; the app picks the psycopg 3 driver). In production it comes from CloudNativePG, not from `.env.prod` | `postgresql://postgres:postgres@localhost:5432/app` (docker-compose's `db`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client (type "Web application") for Sign in with Google; empty turns it off | empty |
| `GOOGLE_REDIRECT_URI` | The callback registered with that client | `https://api.yarikama.com/api/v1/auth/google/callback` |
| `ADMIN_EMAILS` | Comma-separated Google accounts that may sign in | empty |
| `SITE_URL` | Where the browser goes after signing in | `https://yarikama.com` |
| `ADMIN_SESSION_HOURS` | How long a Google sign-in lasts | `12` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL` | Cloudflare R2, for image uploads | bucket `yarikama-website-backend` |
| `DEBUG` | Debug mode | `False` |
| `VERCEL_TOKEN` | Vercel access token, for the site's visitors (Web Analytics) in the admin; empty turns `/admin/visitors` off (503) | empty |
| `VERCEL_PROJECT`, `VERCEL_TEAM` | The front end's Vercel project (name or id) and its team's slug | `prj_Q4QGU1c6k6g01UhBxVTL43ZyuzBP`, `yarikamas-projects` |
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
