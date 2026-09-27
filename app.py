"""
ImmortalNet - a mini "Internet" where every user can register a unique
domain and publish their own little website, browsable through an
in-app browser UI.

See README.md for setup, deployment and a list of known limitations.
"""

import os
import re
import time
import secrets
from functools import wraps
from collections import defaultdict, deque

from flask import (
    Flask, request, session, redirect, url_for, render_template,
    abort, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash

import db

# --------------------------------------------------------------------------
# App setup
# --------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024  # 300 KB hard cap per request

# ----- limits (Section 10 / 18: security & sane defaults) -----------------
MAX_CODE_LENGTH = 50_000          # max chars allowed in each of html/css/js
MAX_WEBSITES_PER_USER = 5
DOMAIN_MAX_LEN = 32
DOMAIN_RE = re.compile(r"^[a-z0-9-]{1,%d}$" % DOMAIN_MAX_LEN)

RESERVED_DOMAINS = {
    "admin", "api", "www", "login", "register", "dashboard", "settings",
    "support", "help", "static", "create", "logout", "edit", "site",
    "browser", "root", "system", "immortalnet", "null", "undefined",
}

# ----- very small in-memory rate limiter -----------------------------------
# Good enough to blunt naive abuse for an MVP; NOT a substitute for a real
# rate-limiting layer (e.g. Redis-backed) in production - see README.
_rate_buckets = defaultdict(deque)


def rate_limit(key_prefix, max_calls, window_seconds, methods=("POST",)):
    """
    Limits how often a client can hit this endpoint. By default only counts
    the given HTTP methods (POST, i.e. the actual mutating submission) so
    that simply loading the page (GET, e.g. to fetch a CSRF token) doesn't
    eat into the same budget as real submissions.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if request.method not in methods:
                return fn(*args, **kwargs)
            key = f"{key_prefix}:{request.remote_addr}"
            now = time.time()
            bucket = _rate_buckets[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= max_calls:
                return render_template(
                    "error.html",
                    message="Too many requests. Please slow down and try again shortly."
                ), 429
            bucket.append(now)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


# --------------------------------------------------------------------------
# CSRF protection (hand-rolled: no flask-wtf dependency needed)
# --------------------------------------------------------------------------

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = get_csrf_token


def csrf_protect(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not sent or not expected or not secrets.compare_digest(sent, expected):
                abort(400, description="Invalid or missing CSRF token.")
        return fn(*args, **kwargs)
    return wrapped


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    if not hasattr(g, "_user_cache"):
        conn = db.get_connection()
        g._user_cache = db.run(
            conn, "SELECT id, username, created_at FROM users WHERE id = ?",
            (session["user_id"],), fetch="one"
        )
        conn.close()
    return g._user_cache


app.jinja_env.globals["current_user"] = current_user


# --------------------------------------------------------------------------
# Domain validation
# --------------------------------------------------------------------------

def validate_domain(raw_domain):
    """
    Returns (clean_domain, error_message_or_None).
    """
    if raw_domain is None:
        return None, "Domain is required."
    domain = raw_domain.strip().lower()
    if not domain:
        return None, "Domain is required."
    if len(domain) > DOMAIN_MAX_LEN:
        return None, f"Domain must be {DOMAIN_MAX_LEN} characters or fewer."
    if not DOMAIN_RE.match(domain):
        return None, "Domain may only contain lowercase letters, numbers and hyphens."
    if domain in RESERVED_DOMAINS:
        return None, f'Domain "{domain}" is reserved and cannot be used.'
    return domain, None


# --------------------------------------------------------------------------
# Routes: static pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    conn = db.get_connection()
    latest = db.run(
        conn,
        "SELECT domain, title FROM websites WHERE published = 1 "
        "ORDER BY created_at DESC LIMIT 12" if not db.IS_POSTGRES else
        "SELECT domain, title FROM websites WHERE published = TRUE "
        "ORDER BY created_at DESC LIMIT 12",
        fetch="all",
    )
    conn.close()
    return render_template("index.html", latest=latest)


# --------------------------------------------------------------------------
# Routes: auth
# --------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
@csrf_protect
@rate_limit("register", max_calls=10, window_seconds=60)
def register():
    if request.method == "GET":
        return render_template("register.html", error=None)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not re.match(r"^[A-Za-z0-9_]{3,32}$", username):
        return render_template(
            "register.html",
            error="Username must be 3-32 characters: letters, numbers, underscore."
        )
    if len(password) < 8:
        return render_template("register.html", error="Password must be at least 8 characters.")

    conn = db.get_connection()
    existing = db.run(conn, "SELECT id FROM users WHERE username = ?", (username,), fetch="one")
    if existing:
        conn.close()
        return render_template("register.html", error=f'Username "{username}" is already taken.')

    password_hash = generate_password_hash(password)
    try:
        user_id = db.run_insert_returning_id(
            conn, "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )
    except Exception:
        conn.close()
        return render_template("register.html", error=f'Username "{username}" is already taken.')
    conn.close()

    session.clear()
    session["user_id"] = user_id
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
@csrf_protect
@rate_limit("login", max_calls=15, window_seconds=60)
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    conn = db.get_connection()
    user = db.run(conn, "SELECT * FROM users WHERE username = ?", (username,), fetch="one")
    conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid username or password.")

    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
@csrf_protect
def logout():
    session.clear()
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# Routes: dashboard / create / edit
# --------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    conn = db.get_connection()
    sites = db.run(
        conn, "SELECT domain, title, published FROM websites WHERE owner_id = ? ORDER BY created_at DESC",
        (user["id"],), fetch="all"
    )
    conn.close()
    return render_template("dashboard.html", user=user, sites=sites)


@app.route("/create", methods=["GET", "POST"])
@login_required
@csrf_protect
@rate_limit("create", max_calls=10, window_seconds=60)
def create():
    user = current_user()

    if request.method == "GET":
        return render_template("create.html", error=None, form={})

    conn = db.get_connection()
    count_row = db.run(
        conn, "SELECT COUNT(*) as c FROM websites WHERE owner_id = ?", (user["id"],), fetch="one"
    )
    if count_row["c"] >= MAX_WEBSITES_PER_USER:
        conn.close()
        return render_template(
            "create.html", error=f"You already have the maximum of {MAX_WEBSITES_PER_USER} websites.",
            form=request.form
        )

    title = (request.form.get("title") or "").strip()[:200]
    domain, err = validate_domain(request.form.get("domain"))
    html = (request.form.get("html") or "")[:MAX_CODE_LENGTH]
    css = (request.form.get("css") or "")[:MAX_CODE_LENGTH]
    js = (request.form.get("js") or "")[:MAX_CODE_LENGTH]

    if not title:
        err = err or "Website name is required."
    if err:
        conn.close()
        return render_template("create.html", error=err, form=request.form)

    existing = db.run(conn, "SELECT id FROM websites WHERE domain = ?", (domain,), fetch="one")
    if existing:
        conn.close()
        return render_template(
            "create.html", error=f'Domain "{domain}" is already taken.', form=request.form
        )

    try:
        db.run_insert_returning_id(
            conn,
            "INSERT INTO websites (owner_id, domain, title, html, css, js, published) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (user["id"], domain, title, html, css, js)
        )
    except Exception:
        conn.close()
        return render_template(
            "create.html", error=f'Domain "{domain}" is already taken.', form=request.form
        )
    conn.close()
    return redirect(url_for("edit_site", domain=domain))


@app.route("/edit/<domain>", methods=["GET", "POST"])
@login_required
@csrf_protect
def edit_site(domain):
    user = current_user()
    domain = domain.strip().lower()

    conn = db.get_connection()
    site = db.run(conn, "SELECT * FROM websites WHERE domain = ?", (domain,), fetch="one")

    if not site:
        conn.close()
        abort(404)
    if site["owner_id"] != user["id"]:
        conn.close()
        abort(403)

    if request.method == "GET":
        conn.close()
        return render_template("edit.html", site=site, error=None, saved=False)

    title = (request.form.get("title") or "").strip()[:200] or site["title"]
    html = (request.form.get("html") or "")[:MAX_CODE_LENGTH]
    css = (request.form.get("css") or "")[:MAX_CODE_LENGTH]
    js = (request.form.get("js") or "")[:MAX_CODE_LENGTH]

    db.run(
        conn,
        "UPDATE websites SET title = ?, html = ?, css = ?, js = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        if not db.IS_POSTGRES else
        "UPDATE websites SET title = ?, html = ?, css = ?, js = ?, "
        "updated_at = NOW() WHERE id = ?",
        (title, html, css, js, site["id"]),
        commit=True,
    )
    site = db.run(conn, "SELECT * FROM websites WHERE id = ?", (site["id"],), fetch="one")
    conn.close()
    return render_template("edit.html", site=site, error=None, saved=True)


# --------------------------------------------------------------------------
# Routes: browsing published websites
# --------------------------------------------------------------------------

@app.route("/site/<domain>")
def view_site(domain):
    domain = domain.strip().lower()
    conn = db.get_connection()
    site = db.run(conn, "SELECT * FROM websites WHERE domain = ?", (domain,), fetch="one")
    conn.close()

    if not site or not site["published"]:
        return render_template("site_not_found.html", domain=domain), 404

    # Build a standalone HTML document out of the user's HTML/CSS/JS.
    # This is rendered ONLY inside a sandboxed iframe (see /browser and
    # templates/site.html) - never inline in a page that carries the
    # logged-in user's session. See README "Security notes".
    page = render_template(
        "site.html", title=site["title"], html=site["html"], css=site["css"], js=site["js"]
    )
    resp = app.response_class(page, mimetype="text/html")
    # Untrusted content: the real isolation boundary is the sandboxed
    # <iframe> it's always viewed through (see /browser and static/js/
    # browser.js - no "allow-same-origin", so this document's JS can never
    # read ImmortalNet's cookies/session or touch the parent page).
    #
    # Because a user's "website" is meant to be a real website, we do NOT
    # lock down what it can load (images, fonts, embeds like a Spotify/
    # YouTube <iframe>, external scripts, etc.) - that would defeat the
    # point of the product. The one thing the CSP still enforces is
    # frame-ancestors, so this page can only ever be framed by our own app
    # (prevents someone else clickjacking a raw /site/<domain> URL).
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'self';"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/browser")
def browser_ui():
    initial_domain = request.args.get("domain", "").strip().lower()
    return render_template("browser.html", initial_domain=initial_domain)


# --------------------------------------------------------------------------
# Small JSON helper API (used by the browser UI / create form)
# --------------------------------------------------------------------------

@app.route("/api/check-domain")
def api_check_domain():
    raw = request.args.get("domain", "")
    domain, err = validate_domain(raw)
    if err:
        return jsonify({"available": False, "error": err})
    conn = db.get_connection()
    existing = db.run(conn, "SELECT id FROM websites WHERE domain = ?", (domain,), fetch="one")
    conn.close()
    if existing:
        return jsonify({"available": False, "error": f'Domain "{domain}" is already taken.'})
    return jsonify({"available": True})


@app.route("/api/site-exists/<domain>")
def api_site_exists(domain):
    domain = domain.strip().lower()
    conn = db.get_connection()
    site = db.run(
        conn, "SELECT domain, title FROM websites WHERE domain = ? AND published = 1"
        if not db.IS_POSTGRES else
        "SELECT domain, title FROM websites WHERE domain = ? AND published = TRUE",
        (domain,), fetch="one"
    )
    conn.close()
    return jsonify({"exists": bool(site), "title": site["title"] if site else None})


# --------------------------------------------------------------------------
# Error handlers
# --------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="Page not found."), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", message="You don't have permission to do that."), 403


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", message=str(e.description) or "Bad request."), 400


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

db.init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
