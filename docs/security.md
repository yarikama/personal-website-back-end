# Security

- The admin signs in with Google: the API runs the OAuth authorization code flow with PKCE, a one-time state bound to the browser by a cookie, and a nonce, and lets in only the accounts in `ADMIN_EMAILS` ([`services/google_oauth.py`](../app/services/google_oauth.py)). The site loads no script from Google.
- The session is an `HttpOnly`, `Secure`, `SameSite=Strict` `__Host-` cookie, so page scripts cannot read it. Redis keeps only its SHA-256, and signing out deletes it at once. A request that changes something must also come from the site's own origin.
- Request bodies are capped before anything reads them: 20 MB for image uploads, 2 MB for everything else (413 above that).
- An upload is stored as what its bytes are (JPEG, PNG, GIF or WebP, checked with Pillow), never as the file name or type the browser claims, and only in the known folders (`images`, `notes`, `covers`).
- CI runs `pip-audit` against the locked dependencies.
- Drafts are filtered out of every public endpoint and of the chat prompt, and tests check this.
- Every response carries HSTS, `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; API responses also get `Content-Security-Policy: default-src 'none'`, since they never load anything.
- The production image runs as an unprivileged user.
- The deploy key that can change the cluster is only available to workflows on `main`.
