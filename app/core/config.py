from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_secret_key() -> str:
    """Secret for session cookies.

    Priority:
      1) CAREERSIGNAL_SECRET_KEY env
      2) data/.secret_key file (created once)
    """
    env = os.getenv("CAREERSIGNAL_SECRET_KEY")
    if env:
        return env

    key_file = BASE_DIR / "data" / ".secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    import secrets

    key = secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding="utf-8")
    return key


def public_base_url() -> str:
    """Base URL used in emails/discord links. Example: https://careersignal.de"""
    return os.getenv("CAREERSIGNAL_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def default_check_interval_minutes() -> int:
    try:
        return max(5, int(os.getenv("CAREERSIGNAL_CHECK_INTERVAL_MINUTES", "60")))
    except Exception:
        return 60
