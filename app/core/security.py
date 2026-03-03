from __future__ import annotations

import secrets
import time
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request
from passlib.context import CryptContext
from starlette.status import HTTP_403_FORBIDDEN, HTTP_429_TOO_MANY_REQUESTS

# PBKDF2 (no binary wheels; stable across platforms)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    password = (password or "").strip()
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    password = (password or "").strip()
    return pwd_context.verify(password, hashed)


# -------------------------
# CSRF protection (session-based)
# -------------------------
CSRF_SESSION_KEY = "_csrf_token"


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf(request: Request, submitted: Optional[str]) -> None:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="CSRF validation failed")


# -------------------------
# Simple in-memory login rate limit (local-only)
# -------------------------
_LOGIN_BUCKET: Dict[str, Tuple[int, int]] = {}
_WINDOW_SECONDS = 10 * 60
_MAX_ATTEMPTS = 12


def _bucket_key(ip: str, email: str) -> str:
    return f"{ip}|{(email or '').lower().strip()}"


def login_rate_limit(ip: str, email: str) -> None:
    now = int(time.time())
    key = _bucket_key(ip, email)
    count, start = _LOGIN_BUCKET.get(key, (0, now))

    if now - start >= _WINDOW_SECONDS:
        count, start = 0, now

    count += 1
    _LOGIN_BUCKET[key] = (count, start)

    if count > _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Login-Versuche. Bitte später erneut versuchen.",
        )
