# ImmortalNet

A tiny "mini Internet": users register, create a website under a unique
`something.immortalnet` domain, write raw HTML/CSS/JS for it, publish it,
and browse each other's sites through an in-app browser UI.

Built and tested end-to-end (register → login → create → domain-uniqueness
→ edit → authorization → public rendering) as part of this build. See
**Tested so far** below for exactly what was verified and how.

## Project structure

```
immortalnet/
├── app.py                  Flask app: all routes, auth, validation, security
├── db.py                   DB layer - SQLite locally, PostgreSQL in production
├── requirements.txt
├── render.yaml              Render Blueprint (web service + Postgres)
├── .env.example
├── templates/
│   ├── base.html            Shared layout / nav
│   ├── index.html           Homepage
│   ├── register.html
│   ├── login.html
│   ├── dashboard.html
│   ├── create.html          Create-website form
│   ├── edit.html            Editor with live sandboxed preview
│   ├── browser.html         In-app "browser" UI
│   ├── site.html            The user's actual published page (rendered standalone)
│   ├── site_not_found.html
│   └── error.html
└── static/
    ├── css/style.css        Dark theme (#0d1117 / #161b22 / #238636)
    └── js/browser.js        Address bar, back/forward/reload logic
```

## How the database switch works

The app never imports `psycopg2` unless it needs to. `db.py` checks the
`DATABASE_URL` environment variable at startup:

- **Not set** → uses a local SQLite file (`immortalnet.db`, created
  automatically). Zero setup - this is what you get by just running
  `python app.py`.
- **Set to a `postgres://...` URL** (e.g. on Render) → uses PostgreSQL via
  `psycopg2`, with the same SQL (placeholders rewritten automatically).

Both `users.username` and `websites.domain` have a real **UNIQUE**
constraint at the database level (not just checked in application code),
on both backends - see `SCHEMA_SQLITE` / `SCHEMA_POSTGRES` in `db.py`.

## Running locally

```bash
cd immortalnet
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Visit `http://localhost:5000`. No PostgreSQL install needed - it uses
SQLite automatically. Set `FLASK_DEBUG=1` for the Flask debugger, and
optionally copy `.env.example` to `.env` and export its values (or use a
tool like `python-dotenv` / `direnv`).

## Running locally against a real PostgreSQL (optional)

```bash
createdb immortalnet
export DATABASE_URL=postgresql://localhost/immortalnet
export SECRET_KEY=some-long-random-string
python app.py
```

## Deploying to Render

**Option A - Blueprint (recommended, uses `render.yaml`):**

1. Push this project to a GitHub repository.
2. In the Render dashboard, choose **New → Blueprint** and point it at the repo.
3. Render reads `render.yaml` and provisions both the web service and a
   free PostgreSQL database, wires `DATABASE_URL` automatically, and
   generates a random `SECRET_KEY`.
4. Click **Apply** - that's it.

**Option B - Manual:**

1. Push the project to GitHub.
2. Create a **PostgreSQL** instance on Render, copy its **Internal
   Connection String**.
3. Create a **Web Service** from the repo:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
4. In the web service's **Environment**, set:
   - `DATABASE_URL` = the Postgres connection string from step 2
   - `SECRET_KEY` = any long random string
5. Deploy. Tables are created automatically on first request
   (`db.init_db()` runs at import time).

## Test data

No seed data ships with the repo (a fresh deploy starts empty), but here's
a quick way to get a couple of accounts/sites to click around with, once
running locally:

```bash
# in a second terminal, with the app running
curl -c /tmp/c.txt http://localhost:5000/register   # loads a CSRF token into the cookie jar
# then use the browser UI at /register and /create - it's easier by hand,
# the curl dance above is only useful for scripted testing (see "Tested so far").
```

Just open `/register` in a browser, sign up, then `/create` to make your
first site (try domain `hien`, then visit it at `/site/hien` or through
`/browser`).

## Tested so far

Run manually against the local SQLite backend during this build (Flask's
dev server, real HTTP requests via `curl`, inspecting responses/headers/DB
rows):

- Register → session cookie issued → `/dashboard` shows the right username
- Domain uniqueness enforced: a second user creating an already-used
  domain gets `Domain "hien" is already taken.` (checked both at the
  application layer and relies on the DB's UNIQUE constraint as a backstop
  against races)
- Reserved domains (`admin`, etc.) rejected
- Invalid domain characters/case (`Hi_En`) rejected, with the exact
  validation message
- CSRF: a request with a missing/wrong `csrf_token` is rejected (400)
- Authorization: a non-owner gets 403 on `GET /edit/<domain>`; a logged-out
  visitor is redirected to `/login`; the owner sees their own content
- Per-user website limit (5) enforced; the 6th attempt is blocked with a
  clear message
- `GET /site/<domain>` renders the user's HTML/CSS/JS standalone, with
  `X-Frame-Options: SAMEORIGIN` and a restrictive `Content-Security-Policy`
  header; a nonexistent domain returns 404
- Password is stored as a salted hash (`werkzeug.security`), never plaintext
- Wrong password on login is rejected without revealing which field was wrong
- Homepage "Latest Websites" lists newly created, published sites

Not exercised in this pass (would need a browser, not just curl): the
`/browser` address-bar/back/forward JS, and the live iframe preview in the
editor. Both were reviewed by hand for correctness but you should click
through them yourself after deploying.

## Known limitations / what's NOT production-hardened

Read this before putting real user data on it:

1. **Same-origin sandboxing, not a separate subdomain.** All websites are
   served from `/site/<domain>` on the *same* Flask app/origin, isolated
   only by the browser's `iframe sandbox` attribute (no
   `allow-same-origin`, so each site's JS gets an opaque, cookie-less
   origin and cannot reach `document.cookie`, the parent page, or other
   sites' iframes). This is a reasonable MVP mitigation, but the
   architecturally cleaner fix mentioned in the spec - giving every site
   its own real subdomain (e.g. `hien.yourapp.com`) - was **not**
   implemented, since that needs wildcard DNS + wildcard TLS, which is a
   deployment/infra decision beyond a Render free-tier MVP.
2. **Rate limiting is in-memory**, per-process. It resets on every deploy
   or restart, and won't coordinate across multiple server instances if
   you scale horizontally. Fine for an MVP; swap for Redis-backed limits
   (e.g. `Flask-Limiter` with a Redis backend) before real production use.
3. **No email verification, password reset, or account recovery.** If a
   user forgets their password, there is currently no way to recover the
   account.
4. **No HTML sanitization on the published site's markup** - and this is
   intentional: a user's HTML/CSS/JS *is* their website, so stripping tags
   would break the product. The isolation instead comes from the iframe
   sandbox described in (1). Do not remove the sandbox attribute.
5. **CSP allows `'unsafe-inline'`** for scripts/styles on `/site/<domain>`,
   because sites are arbitrary inline user code by design. This does not
   protect against a malicious site attacking *its own visitors*, only
   against it attacking ImmortalNet itself or other domains (frames, cookies).
6. **No image/file uploads** - `html`/`css`/`js` are the only content
   types; anything else (images, fonts) would need to be hot-linked from
   elsewhere.
7. **No HTTPS enforcement in app code** - Render terminates TLS for you
   automatically, but if you self-host, put this behind a reverse proxy
   that enforces HTTPS.
8. **`unpublished` sites**: the schema/column exists (`published`), but no
   UI currently lets a user unpublish a site - all created sites are
   published immediately. Easy to add (a checkbox in the editor + a
   route), just not wired up in this MVP.
9. **Session cookies use Flask's default signed-cookie sessions** (not
   server-side sessions), which is fine at this scale but means session
   data is limited in size and can't be revoked server-side before it
   expires - consider server-side sessions (e.g. Flask-Session + Redis) if
   you need to force-logout users.

## Feature checklist against the spec

- [x] Register / Login / Logout / Session
- [x] Passwords hashed (Werkzeug `scrypt`), never plaintext
- [x] Create website with name, domain, HTML, CSS, JS
- [x] Domain uniqueness enforced at the DB level (UNIQUE constraint) +
      checked in application code with a friendly error message
- [x] Domain format validation (lowercase, `a-z0-9-`, ≤32 chars, reserved list)
- [x] Dashboard: username, site count, site list with Open/Edit
- [x] `/site/<domain>` route, displayed to the user as `domain.immortalnet`
- [x] `/browser` in-app browser UI with back/forward/reload/address bar
- [x] `/edit/<domain>` editor, owner-only, with sandboxed live preview
- [x] CSRF protection (hand-rolled token, no extra dependency)
- [x] Basic rate limiting (register/login/create)
- [x] Size limits on HTML/CSS/JS and per-user website count
- [x] Dark, minimal, responsive homepage/dashboard UI
- [x] `render.yaml` Blueprint for one-click Render deploy
- [x] `DATABASE_URL` / `SECRET_KEY` read from environment, nothing hardcoded
