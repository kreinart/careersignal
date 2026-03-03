from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_keywords(raw: str) -> List[str]:
    if not raw:
        return []
    # allow comma or newline separated
    parts = []
    for chunk in raw.replace("\n", ",").split(","):
        k = chunk.strip()
        if k:
            parts.append(k)
    # unique preserving order
    seen = set()
    out = []
    for k in parts:
        lk = k.lower()
        if lk in seen:
            continue
        seen.add(lk)
        out.append(k)
    return out


def keywords_to_storage(keywords: List[str]) -> str:
    return ",".join([k.strip() for k in keywords if k.strip()])


def storage_to_keywords(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return parse_keywords(s)


def compute_next_check(last_checked_at_iso: Optional[str], interval_minutes: int, backoff_factor: int = 0) -> Optional[str]:
    if not last_checked_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(last_checked_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minutes = interval_minutes * max(1, 2 ** backoff_factor)
        return (dt + timedelta(minutes=minutes)).isoformat()
    except Exception:
        return None
