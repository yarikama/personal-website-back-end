# Design notes

## Ask: cache-augmented generation, not RAG

All published content, about 13k tokens (October 2026), fits in the model's 24k context. Instead of retrieving chunks, [`services/ask.py`](../app/services/ask.py) puts every published project and note, plus the resume, into the system prompt. That prefix is the same for every question, so vLLM's prefix cache computes it once: the first token arrives in about 0.1–0.2 s once the cache is warm.

- **Freshness.** The prompt is rebuilt whenever published content changes, and drafts never enter it.
- **Citations.** The model cites `[P1]`, `[N1]` or `[R1]`. Citations to ids that don't exist are dropped before they reach the client.
- **Size guard.** A token estimate guards the context window. The `ask_prompt_tokens` metric triggers an alert well before the limit. If the limit is reached anyway, the oldest notes are left out first, so the chat keeps working.
- **Highlighted passages.** The backend works out which document a passage comes from: a note's page, or else the one document containing the text. It tells the model, and lists that document first among the citations even when the model forgets to cite it.
- **Short memory.** A follow-up sees the conversation's last two questions and answers. They are kept in Redis for 30 minutes under a random conversation id, which the browser sends back; the browser never sends the history itself, so it can't be forged. The prompt budget keeps room for them, and without Redis, questions are answered without history.
- **Questions are kept.** Each question, its answer and citations go to the `ask_questions` table for 30 days, without the visitor's address. The admin area lists them, filters for answers that cite nothing or broke off, and rates answers good or bad, which turns real questions into evaluation data.
- **Injection.** The visitor's question and any highlighted passage are treated as untrusted. The model has no tools, so an injected instruction can only change the text of an answer.
- **Model choice.** The model was chosen, and prompt changes are checked, with a 40-question evaluation set in [`eval/ask/`](../eval/ask/).

Design and when to switch to retrieval: homelab `docs/14-ask-chat-plan.md`.

## Autocomplete that knows when to stop

[`services/autocomplete.py`](../app/services/autocomplete.py) streams the completion token by token, using the last 2,000 characters before the cursor.

- **Stopping early.** It stops at the first token whose probability is below a threshold (0.5 by default), at the end of a sentence, or where the model starts repeating the text before it. Closing the stream also makes vLLM abort the rest of the generation.
- **Failures.** A slow or failed model gives an empty suggestion, never an error.
- **Training data.** The outcome of each suggestion (accepted, typed along, rejected or ignored, and how many tokens were taken) is stored for later fine-tuning.

## Rate limits that hold under concurrency

Each check is a single Lua script in Redis, so reading, deciding and recording happen atomically ([`services/rate_limit.py`](../app/services/rate_limit.py)). Two algorithms are used: sliding logs for small, exact budgets, and token buckets for bursts. Each rule chooses what happens when Redis is down; today's all fail open. The admin, whose sessions live in Redis, gets 503 instead. A 429 still carries CORS headers, so the browser can show the message instead of a network error.

The rules themselves are in [API → Rate limits](api.md#rate-limits). Design and trade-offs: homelab `docs/11-rate-limiting.md`.

## Background jobs on Redis Streams

[`services/jobs.py`](../app/services/jobs.py) is a small at-least-once queue built on a consumer group.

- **Retries.** A job is acknowledged only after it succeeds. A job whose worker died is claimed again by another worker (`XAUTOCLAIM`).
- **Dead letters.** Jobs that keep failing move to a dead-letter stream.
- **Recovery.** Redis keeps no data across restarts, so the worker reconciles with R2 on start and every hour, queuing any image still missing its variants.

## Edge caching without stale CORS

Public `GET`s answer with an `ETag` and `s-maxage=60, stale-while-revalidate=600`, so Cloudflare serves most traffic. Cloudflare's cache ignores `Vary: Origin`, so these responses use `Access-Control-Allow-Origin: *` instead of echoing the origin ([`api/cache.py`](../app/api/cache.py)).

## Observability

- **Traces.** OpenTelemetry traces every request, with spans for SQL, Redis and model calls. Log lines inside a request carry its `trace_id`.
- **Metrics.** Prometheus metrics (answers, tokens, time to first token, prompt size, queue depth) are served on a separate port, so the public Ingress never exposes them.
