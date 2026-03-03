from __future__ import annotations

import json
from typing import Optional

import requests


def send_discord(webhook_url: str, content: str) -> Optional[str]:
    if not webhook_url:
        return None
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=12)
        if r.status_code >= 400:
            return f"HTTP {r.status_code}: {r.text[:200]}"
        return None
    except Exception as e:
        return str(e)
