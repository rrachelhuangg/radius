"""Google sign-in and user accounts, stored in Postgres.

Needs these environment variables (accounts are switched off if any are missing):
    DATABASE_URL          Postgres connection string (Vercel's Neon integration sets this)
    GOOGLE_CLIENT_ID      OAuth client from Google Cloud Console
    GOOGLE_CLIENT_SECRET
    SECRET_KEY            random string used to sign the session cookie
"""

import os
import secrets
from datetime import timedelta
from urllib.parse import quote

import psycopg
from authlib.integrations.flask_client import OAuth, OAuthError
from flask import Blueprint, jsonify, redirect, session, url_for
from psycopg.rows import dict_row
from werkzeug.middleware.proxy_fix import ProxyFix

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
ENABLED = all([DATABASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, os.environ.get("SECRET_KEY")])

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    google_sub    TEXT UNIQUE NOT NULL,  -- Google's stable account ID
    email         TEXT NOT NULL,
    name          TEXT,
    picture       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

bp = Blueprint("auth", __name__)
oauth = OAuth()
_schema_ready = False


def init_app(app):
    # Vercel sits in front of the app as a proxy; trust its headers so redirect URLs use https
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),  # https-only when deployed
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    oauth.init_app(app)
    oauth.register(
        "google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    app.register_blueprint(bp)


def db():
    """New connection per request; serverless functions can't hold a pool open."""
    global _schema_ready
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    if not _schema_ready:
        conn.execute(SCHEMA)
        _schema_ready = True
    return conn


def auth_error(message):
    """Back to the home page, which shows the message in its status line."""
    return redirect("/?auth_error=" + quote(message))


def current_user():
    user_id = session.get("user_id")
    if not ENABLED or user_id is None:
        return None
    with db() as conn:
        user = conn.execute(
            "SELECT id, email, name, picture, created_at FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    if user is None:  # account was deleted
        session.clear()
    return user


@bp.get("/login")
def login():
    if not ENABLED:
        return auth_error("Accounts aren't set up on this server.")
    return oauth.google.authorize_redirect(url_for("auth.callback", _external=True))


@bp.get("/auth/callback")
def callback():
    try:
        info = oauth.google.authorize_access_token()["userinfo"]
    except OAuthError as e:  # e.g. the user hit "Cancel" on Google's screen
        return auth_error(e.description or "Sign-in was cancelled.")

    with db() as conn:
        user = conn.execute(
            """INSERT INTO users (google_sub, email, name, picture) VALUES (%s, %s, %s, %s)
               ON CONFLICT (google_sub) DO UPDATE
               SET email = EXCLUDED.email, name = EXCLUDED.name, picture = EXCLUDED.picture, last_login_at = now()
               RETURNING id""",
            (info["sub"], info["email"], info.get("name"), info.get("picture")),
        ).fetchone()
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    return redirect("/")


@bp.post("/logout")
def logout():
    session.clear()
    return "", 204


@bp.get("/api/me")
def me():
    user = current_user()
    if user:
        user = {**user, "created_at": user["created_at"].isoformat()}
    return jsonify(enabled=ENABLED, user=user)


@bp.delete("/api/me")
def delete_account():
    user = current_user()
    if user is None:
        return jsonify(error="Not signed in."), 401
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user["id"],))
    session.clear()
    return "", 204
