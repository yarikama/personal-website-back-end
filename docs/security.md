# Security

- The admin signs in with Google: the API runs the OAuth authorization code flow with PKCE, a one-time state bound to the browser by a cookie, and a nonce, and lets in only the accounts in `ADMIN_EMAILS` ([`services/google_oauth.py`](../app/services/google_oauth.py)). The site loads no script from Google.
- The session is an `HttpOnly`, `Secure`, `SameSite=Strict` `__Host-` cookie, so page scripts cannot read it. Redis keeps only its SHA-256, and signing out deletes it at once. A request that changes something must also come from the site's own origin.
- Claude signs in to the writing MCP server with OAuth 2.1, served by the API as the MCP spec asks ([`services/mcp_oauth.py`](../app/services/mcp_oauth.py)), through the same Google sign-in. Clients register themselves, but only with a redirect URI in `MCP_REDIRECT_URIS`, so a code only ever goes to Claude. Since anyone can start a sign-in, a consent page names the client and where the code will go before Google is asked; its answer needs a `SameSite=Strict` cookie and the page's token, so a form on another site cannot answer for the owner. Without it, a link from someone else could end with their client holding the owner's tokens.
- MCP access tokens last an hour (Redis) and refresh tokens 30 days (PostgreSQL), both kept as SHA-256 hashes; a code and a refresh token each work once, and revoking either token ends both. Every token is checked against `ADMIN_EMAILS` each time it is used.
- Request bodies are capped before anything reads them: 20 MB for image uploads, 2 MB for everything else (413 above that).
- An upload is stored as what its bytes are (JPEG, PNG, GIF or WebP, checked with Pillow), never as the file name or type the browser claims, and only in the known folders (`images`, `notes`, `covers`).
- CI runs `pip-audit` against the locked dependencies.
- Drafts are filtered out of every public endpoint and of the chat prompt, and tests check this.
- Every response carries HSTS, `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; API responses also get `Content-Security-Policy: default-src 'none'`, since they never load anything.
- The production image runs as an unprivileged user.
- The deploy key that can change the cluster is only available to workflows on `main`.
