# API

Interactive docs are served at `/docs` and `/redoc`.

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness, for Kubernetes probes and uptime monitoring: `{"status": "ok"}` |
| `GET /docs`, `GET /redoc` | Interactive API docs |
| `GET /api/v1/auth/google/login?next=/admin/...` | Starts Sign in with Google; Google then sends the browser to `/api/v1/auth/google/callback`, which sets the session cookie and returns to `next` |
| `GET /api/v1/auth/me` | `{"email"}` of whoever is signed in; `401` if no one |
| `POST /api/v1/auth/logout` | Ends the session (`204`) |
| `GET /api/v1/projects`, `GET /api/v1/projects/{slug}` | Published projects, in display order |
| `GET /api/v1/lab-notes`, `GET /api/v1/lab-notes/{slug}`, `GET /api/v1/lab-notes/tags` | Published notes, and their tags with counts |
| `GET /api/v1/learnings/graph` | The Learnings page's knowledge graph: `{"nodes": [{"slug", "kind", "title", "summary", "overview", "ref", "tags", "previewImage", "published"}], "edges": [{"source", "target", "type", "home"}]}`, published nodes only and the edges between them, by slug. `home` marks the one `part_of` edge that is a node's home: a node can be part of several others. A `next` edge is the reading order: the page to read after the source, at most one each way. `GET /api/v1/admin/learnings/graph` includes drafts |
| `GET /api/v1/categories` | Project categories, each with its number of published projects, after an "All" entry that counts them all |
| `POST /api/v1/contact` | The contact form. The owner gets an email with the message; replying answers the visitor |
| `GET /api/v1/admin/contact?read=false` | Contact messages, newest first; `read` filters. `PATCH /api/v1/admin/contact/{id}` with `{"read"}` or `{"replied"}`, `DELETE` to remove one |
| `POST /api/v1/ask` | A question about the owner's work, answered as a server-sent event stream (below). `503` when the model is offline or busy |
| `POST /api/v1/csp-report` | Where browsers report Content-Security-Policy violations from the site (both the `report-uri` and Reporting API formats); each becomes a log line and a `csp_reports_total` count |
| `/api/v1/admin/...` | Create, edit and delete content, list contact messages, upload images. Needs the session cookie; anything but a GET must also come from the site's own origin |
| `GET /api/v1/admin/ask/questions` | Questions asked in the chat, newest first. Filters: `who` (`visitors`, the default, `admin` or `all`), `uncited`, `passage`, `failed` (cut off or broken off), `rating` (`good`, `bad` or `none`) |
| `PATCH /api/v1/admin/ask/questions/{id}` | `{"rating": "good" \| "bad" \| null}` |
| `GET /api/v1/admin/github/contributions` | The owner's public GitHub contribution graph (`GITHUB_USER`), for the welcome page: `{"user", "total", "days": [{"date", "count", "level"}]}`, fetched at most once an hour |
| `GET /api/v1/admin/todos?day=YYYY-MM-DD`, `POST`, `PATCH /{id}`, `DELETE /{id}` | The welcome page's to-dos: each day starts with the daily items (a NeetCode problem), anything not done carries over, and what was done shows for that day |
| `GET`, `PUT /api/v1/admin/goal` | The owner's current goal, a few words shown large on the welcome page: `{"text"}`; empty clears it |
| `GET /api/v1/admin/visitors?days=7` | The site's visitors over the last 1-30 days to today (UTC), from Vercel Web Analytics (production, without the owner): `{"since", "until", "pageviews", "visitors", "days": [{"date", "pageviews", "visitors"}], "pages", "referrers", "countries", "devices"}`, the last four the top ten as `[{"name", "pageviews", "visitors"}]` (an empty referrer is none). Asked at most every ten minutes; `503` without `VERCEL_TOKEN`, `502` if Vercel fails |
| `GET /api/v1/admin/ask/questions/new`, `POST .../seen` | How many visitors asked since this admin last opened Questions (`{"count", "since"}`); opening it records the visit and returns the previous one (`{"previous"}`) |
| `POST /api/v1/admin/complete` | Note autocomplete: `{"prefix", "title", "noteId"}` → `{"id", "suggestion"}` (empty when the model is unsure or unavailable) |
| `POST /api/v1/admin/complete/{id}/feedback` | `{"outcome": "accepted" \| "rejected" \| "ignored", "acceptedChars"}`, recorded once per suggestion |
| `POST /api/v1/uploads` | An image (multipart `file`) with an upload token from the MCP server's `start_image_upload`, in `Authorization: Bearer <token>`: `{"data": {"url", "filename"}}`. `401` for an unknown, used or expired token |
| `POST /mcp` | The writing MCP server (below), with `MCP_ENABLED`. Needs a bearer token from its OAuth endpoints: `/.well-known/oauth-protected-resource/mcp`, `/.well-known/oauth-authorization-server`, `/register`, `/authorize` (then the consent page, `/api/v1/auth/mcp/consent`), `/token`, `/revoke` |

Lists return `{"data": [...], "pagination": {"total", "limit", "offset", "hasMore"}}`; categories return `data` only. Single items return `{"data": {...}}`. Projects and notes use camelCase fields.

## The ask stream

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

How the answer is built: [Design notes → Ask](design.md#ask-cache-augmented-generation-not-rag).

## The writing MCP server

Claude reads and writes the lab notes, projects and categories, and the Learnings graph, and adds their images, through [MCP](https://modelcontextprotocol.io), signed in as the owner. Connect it from Claude Code:

```bash
claude mcp add --transport http yarikama-writing https://api.yarikama.com/mcp
```

or in claude.ai under Settings → Connectors → Add custom connector, with the same URL. Either opens a consent page naming the client, then Sign in with Google; only `ADMIN_EMAILS` get in.

| Tool | Does |
|---|---|
| `list_lab_notes`, `get_lab_note`, `list_lab_note_tags` | Read, drafts included; filter by published, tag or text |
| `create_lab_note` | A new note, a draft unless told otherwise; `read_time` is worked out from the body |
| `update_lab_note`, `edit_lab_note_content` | Change fields, or replace one passage of the body without resending it |
| `set_lab_note_published` | Publish (dated today, UTC) or take back to a draft |
| `delete_lab_note` | Delete, given the slug again as a check |
| `list_projects`, `get_project` | Read, unpublished included; filter by published, category, featured or tag |
| `create_project`, `update_project` | A new project (unpublished unless told otherwise; category by name or id), or change its fields |
| `set_project_published`, `delete_project` | Show or hide it on the works page; delete, given the slug again |
| `list_categories`, `create_category`, `update_category`, `delete_category` | The works page's filters. `all` is taken by its own "All"; a category with projects in it cannot be deleted |
| `get_learning_graph`, `get_learning_node` | The Learnings graph, drafts included unless asked; one node with the slugs it links to and from |
| `create_learning_node`, `update_learning_node` | A node, unpublished unless told otherwise: a `learning` (a page in the front end, at `/learnings/<slug>` unless given a `ref`), a `concept` that gathers others, or an `external` `https://` link. A field or topic also takes an `overview`: a few paragraphs that ready a reader for what is inside, rewritten as nodes join |
| `link_learning_nodes`, `unlink_learning_nodes` | Edges: `prerequisite` (source before target), `part_of` (source in target), `related`, or `next` (the reading order: target is read after source, one each way). A cycle of prerequisites, of parts or of the reading order is refused. A node can be part of several others: its first `part_of` is its home, a later one takes over with `home`, and losing its home moves it to its oldest other parent |
| `set_learning_node_home` | Moves a node's home to another node it is already part of |
| `set_learning_node_published`, `delete_learning_node` | Show or hide a node on the Learnings page (a learning only once its page is live); delete it and its edges, given the slug again |
| `start_image_upload` | For Claude Code: a single-use, 10-minute token and the `curl` command that sends a file from this machine to `POST /api/v1/uploads` |
| `import_image_from_url` | For claude.ai, which cannot send files: copies an image from a public `https://` URL |
| `delete_image` | Deletes one of the site's images and its WebP variants |

It also offers [`app/content/writing-guide.md`](../app/content/writing-guide.md) as the resource `writing://guide`, each note as `notes://{slug}`, and the prompts `draft_lab_note` and `review_lab_note`. Every write is logged with the account. How sign-in works: [Security](security.md).

## Rate limits

Limits are counted per visitor: the `CF-Connecting-IP` address that Cloudflare sets, with IPv6 grouped by /64. They are kept in Redis, so every replica shares them. Over a limit, the API answers `429` with `Retry-After` in seconds and a readable `detail`.

| Rule | Limit | Algorithm | If Redis is down |
|------|-------|-----------|------------------|
| Contact form | 3 messages per hour | Sliding log | Allow |
| Chat questions | 10 per hour per visitor, and 500 a day for the whole site; not counted for the signed-in admin | Sliding log; token bucket | Allow |
| Every other `/api/` request except `/api/v1/admin/*` and preflights, and the MCP server's `/register`, `/authorize`, `/token` and `/revoke` | Bursts of 60, then 1 per second | Token bucket | Allow |

How they're implemented: [Design notes → Rate limits](design.md#rate-limits-that-hold-under-concurrency).

## Image variants

Uploaded JPEG, PNG and WebP images get WebP variants 640 and 1600 px wide (`<name>.w640.webp`, `<name>.w1600.webp`), never upscaled. The site loads them with `srcset` and falls back to the original until they exist.

## Caching

`GET` on projects, notes, the Learnings graph and categories answers with an `ETag` and `Cache-Control: public, max-age=0, s-maxage=60, stale-while-revalidate=600`.
- **Browsers** revalidate on every request, and get a `304` when nothing changed.
- **Cloudflare** keeps a copy for a minute, so edits in the admin show up publicly within about a minute.
