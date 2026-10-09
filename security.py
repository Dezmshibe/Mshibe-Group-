# security.py
# =============================================================================
# Security hardening for the Mshibe Group Flask app.
#
#   * Security headers (CSP, X-Frame-Options, HSTS, etc.)
#   * Session cookie hardening
#   * Optional rate limiting on /api/voice/chat and /admin/login
#   * Content length guard on the AI endpoint
#
# Call register_security(app) from app.py after the app is created.
# =============================================================================

import os
import time
from collections import defaultdict
from functools import wraps
from flask import request, jsonify, abort, session


# ---- simple in-memory rate limiter (per IP, per bucket) ---------------------
# Good enough for a single-process deploy. For multi-worker, use Flask-Limiter
# with Redis. See notes below.

_HITS = defaultdict(list)

def _client_ip():
    # Trust X-Forwarded-For only if you're behind a proxy (most hosts are).
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"

def rate_limit(bucket_name, max_per_window, window_seconds):
    """Decorator: allow at most `max_per_window` calls per IP per window."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ip = _client_ip()
            key = bucket_name + ":" + ip
            now = time.time()
            cutoff = now - window_seconds
            _HITS[key] = [t for t in _HITS[key] if t > cutoff]
            if len(_HITS[key]) >= max_per_window:
                retry = int(_HITS[key][0] + window_seconds - now) + 1
                resp = jsonify({
                    "error": "rate_limited",
                    "reply": "Too many requests. Please try again shortly.",
                })
                resp.headers["Retry-After"] = str(retry)
                return resp, 429
            _HITS[key].append(now)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def register_security(app):
    # ---------------------------------------------------------------------
    # Session cookie hardening
    # ---------------------------------------------------------------------
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(os.getenv("SESSION_COOKIE_SECURE", "0") == "1"),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,   # 8 hours
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,        # reject bodies > 1 MB
    )

    # ---------------------------------------------------------------------
    # Security headers on every response
    # ---------------------------------------------------------------------
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy",
                                "geolocation=(), camera=(), microphone=(self)")
        # CSP: allow only our own stuff + the CDNs we actually use.
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        # HSTS only makes sense over HTTPS — safe to always send.
        resp.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains"
        )
        return resp

    # ---------------------------------------------------------------------
    # Prompt-injection + abuse guard on the AI endpoint
    # ---------------------------------------------------------------------
    # The AI endpoint is public — anyone can call it. This caps damage from
    # someone trying to drain your Groq quota or abuse the prompt.
    # See ai_receptionist.py for the message sanitization.
    return app


# ---------------------------------------------------------------------
# Login throttling helper (used by app.py's /admin/login)
# ---------------------------------------------------------------------
_LOGIN_FAILS = defaultdict(list)

def login_allowed(ip, max_fails=8, window=600):
    now = time.time()
    cutoff = now - window
    _LOGIN_FAILS[ip] = [t for t in _LOGIN_FAILS[ip] if t > cutoff]
    return len(_LOGIN_FAILS[ip]) < max_fails

def record_login_fail(ip):
    _LOGIN_FAILS[ip].append(time.time())

def clear_login_fails(ip):
    _LOGIN_FAILS.pop(ip, None)