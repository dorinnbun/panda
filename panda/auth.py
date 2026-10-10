"""Single-user password login with an HMAC-signed session cookie."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from .config import settings

log = logging.getLogger("panda.auth")
COOKIE = "panda_session"
SESSION_DAYS = 30

PASSWORD = settings.access_password
if not PASSWORD:
    PASSWORD = secrets.token_urlsafe(12)
    log.warning("ACCESS_PASSWORD is not set. Generated a one-off password for this run: %s", PASSWORD)

_failures: dict[str, list[float]] = {}


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token() -> str:
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(expires)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(expires)):
        return False
    return expires.isdigit() and int(expires) > time.time()


def throttled(ip: str) -> bool:
    """At most 5 failed logins per IP per 15 minutes."""
    recent = [t for t in _failures.get(ip, []) if t > time.time() - 900]
    _failures[ip] = recent
    return len(recent) >= 5


def check_password(ip: str, password: str) -> bool:
    ok = hmac.compare_digest(password.encode(), PASSWORD.encode())
    if not ok:
        _failures.setdefault(ip, []).append(time.time())
    return ok
