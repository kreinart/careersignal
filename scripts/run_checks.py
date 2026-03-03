from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Allow running as `python scripts/run_checks.py` as well as `python -m scripts.run_checks`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.checker import extract_links, fetch_html, keyword_hits, normalize_text, sha256_text
from app.core.config import default_check_interval_minutes, public_base_url
from app.core.discord import send_discord
from app.core.logging import setup_logging
from app.core.mailer import send_email, smtp_configured
from app.core.utils import compute_next_check, storage_to_keywords, utc_now_iso
from app.db import connect, init_db

log = logging.getLogger("careersignal.checks")


EVENT_PAGE_CHANGED = "PAGE_CHANGED"
EVENT_NEW_JOB_LINKS = "NEW_JOB_LINKS"
EVENT_KEYWORD_MATCH = "KEYWORD_MATCH"
EVENT_CHECK_FAILED = "CHECK_FAILED"


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _should_run(w: dict, interval_min: int) -> bool:
    if not int(w.get("is_active") or 0):
        return False
    last = w.get("last_checked_at")
    if not last:
        return True

    status = (w.get("last_status") or "ok")
    backoff = 0
    if status == "failed":
        backoff = min(4, int(w.get("failed_count") or 1))
    elif status == "blocked":
        backoff = min(5, int(w.get("blocked_count") or 1))

    next_iso = compute_next_check(last, interval_min, backoff_factor=backoff)
    if not next_iso:
        return True

    try:
        nxt = datetime.fromisoformat(next_iso)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
    except Exception:
        return True

    return _now_dt() >= nxt


def _pick_discord_webhook(user: dict, watcher: dict) -> Optional[str]:
    return (watcher.get("discord_webhook_override") or "").strip() or (user.get("discord_webhook") or "").strip() or None


def _render_email(event_type: str, watcher: dict, detail_url: str, extra: str = "") -> Tuple[str, str]:
    name = watcher.get("name")
    subject_map = {
        EVENT_PAGE_CHANGED: f"CareerSignal: Änderung erkannt – {name}",
        EVENT_NEW_JOB_LINKS: f"CareerSignal: Neue Job-Links – {name}",
        EVENT_KEYWORD_MATCH: f"CareerSignal: Keyword-Treffer – {name}",
        EVENT_CHECK_FAILED: f"CareerSignal: Check fehlgeschlagen – {name}",
    }
    subject = subject_map.get(event_type, f"CareerSignal: Event – {name}")

    body = [
        f"Hallo!\n",
        f"Event: {event_type}",
        f"Beobachtung: {name}",
        f"URL: {watcher.get('url')}",
    ]
    if extra:
        body.append("")
        body.append(extra.strip())
    body += ["", f"Details: {detail_url}", "", "—", "CareerSignal"]
    return subject, "\n".join(body)


def _discord_text(event_type: str, watcher: dict, detail_url: str, extra: str = "") -> str:
    bits = [f"**{event_type}**", f"**{watcher.get('name')}**", detail_url]
    if extra:
        bits.append(extra.strip()[:500])
    return "\n".join(bits)


def _record_check(
    con,
    watcher_id: int,
    status: str,
    http_status: Optional[int],
    error_message: Optional[str],
    content_hash: Optional[str],
    changed: bool,
    keyword_hits_obj: Dict[str, int],
    new_links_count: int,
    sample_links: List[dict],
) -> int:
    cur = con.execute(
        """
        INSERT INTO check_results(
            watcher_id, checked_at, status, http_status, error_message,
            content_hash, changed, keyword_hits_json, new_links_count, sample_links_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            watcher_id,
            utc_now_iso(),
            status,
            http_status,
            error_message,
            content_hash,
            1 if changed else 0,
            json.dumps(keyword_hits_obj, ensure_ascii=False) if keyword_hits_obj else None,
            int(new_links_count or 0),
            json.dumps(sample_links, ensure_ascii=False) if sample_links else None,
        ),
    )
    return int(cur.lastrowid)


def run_one(con, watcher: dict, user: dict) -> None:
    wid = int(watcher["id"])
    url = watcher["url"]

    log.info("check_start", extra={"watcher_id": wid, "url": url})

    fr = fetch_html(url)
    status = fr.status

    changed = False
    content_hash: Optional[str] = None
    kw_hits: Dict[str, int] = {}
    new_links_count = 0
    sample_links: List[dict] = []

    # previous state
    prev_hash = watcher.get("last_content_hash")
    prev_status = watcher.get("last_status")

    if status == "ok" and fr.html:
        text = normalize_text(fr.html)
        content_hash = sha256_text(text)
        if prev_hash and content_hash != prev_hash:
            changed = True

        kws = storage_to_keywords(watcher.get("keywords"))
        kw_hits = keyword_hits(text, kws) if kws else {}

        sample_links, _ats = extract_links(url, fr.html)

        # new link detection via job_links table
        if sample_links:
            inserted = 0
            for link in sample_links:
                link_url = link.get("url")
                title = link.get("title")
                if not link_url:
                    continue
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO job_links(watcher_id, url, title, first_seen_at) VALUES(?,?,?,?)",
                        (wid, link_url, title, utc_now_iso()),
                    )
                    if con.total_changes:
                        inserted += 1
                except Exception:
                    pass
            new_links_count = inserted

    # record check result
    check_id = _record_check(
        con,
        watcher_id=wid,
        status=status,
        http_status=fr.http_status,
        error_message=fr.error,
        content_hash=content_hash,
        changed=changed,
        keyword_hits_obj=kw_hits,
        new_links_count=new_links_count,
        sample_links=sample_links[:10],
    )

    # update watcher summary
    blocked_count = int(watcher.get("blocked_count") or 0)
    failed_count = int(watcher.get("failed_count") or 0)

    last_error = None
    if status == "blocked":
        blocked_count += 1
        last_error = fr.error
    elif status == "failed":
        failed_count += 1
        last_error = fr.error

    con.execute(
        """
        UPDATE watchers
        SET last_status = ?, last_checked_at = ?, last_error = ?, last_http_status = ?,
            last_content_hash = ?, blocked_count = ?, failed_count = ?
        WHERE id = ?
        """,
        (
            status,
            utc_now_iso(),
            last_error,
            fr.http_status,
            content_hash if status == "ok" else prev_hash,
            blocked_count,
            failed_count,
            wid,
        ),
    )

    con.commit()

    # Events + notifications
    base = public_base_url()
    detail_url = f"{base}/watchers/{wid}"

    events: List[Tuple[str, str]] = []

    if status != "ok":
        # only notify on first failure in a row (transition ok->failed/blocked)
        if prev_status == "ok" or prev_status is None or prev_status == "":
            events.append((EVENT_CHECK_FAILED, fr.error or "Check fehlgeschlagen"))
    else:
        if changed and int(watcher.get("notify_on_change") or 0):
            events.append((EVENT_PAGE_CHANGED, "Seite hat sich geändert."))
        if new_links_count > 0 and int(watcher.get("notify_on_new_jobs") or 0):
            events.append((EVENT_NEW_JOB_LINKS, f"Neue Links: {new_links_count}"))
        if kw_hits and int(watcher.get("notify_on_keyword") or 0):
            top = ", ".join([f"{k} ({v})" for k, v in list(kw_hits.items())[:6]])
            events.append((EVENT_KEYWORD_MATCH, f"Treffer: {top}"))

    email_enabled = int(user.get("email_notifications_enabled") or 0) == 1
    email_ok = smtp_configured()

    webhook = _pick_discord_webhook(user, watcher)

    for event_type, extra in events:
        log.info(
            "event",
            extra={
                "watcher_id": wid,
                "check_id": check_id,
                "event_type": event_type,
                "extra": extra,
            },
        )

        if email_enabled and email_ok:
            subject, body = _render_email(event_type, watcher, detail_url, extra=extra)
            err = send_email(user["email"], subject, body)
            if err:
                log.warning("email_failed", extra={"watcher_id": wid, "error": err})
        elif email_enabled and not email_ok:
            log.warning("email_not_configured", extra={"watcher_id": wid})

        if webhook:
            derr = send_discord(webhook, _discord_text(event_type, watcher, detail_url, extra=extra))
            if derr:
                log.warning("discord_failed", extra={"watcher_id": wid, "error": derr})

    log.info(
        "check_done",
        extra={
            "watcher_id": wid,
            "status": status,
            "changed": changed,
            "new_links": new_links_count,
            "kw_hits": len(kw_hits),
        },
    )


def main() -> int:
    setup_logging()
    init_db()

    interval = default_check_interval_minutes()

    con = connect()
    watchers = con.execute(
        """
        SELECT w.*, u.email AS user_email, u.discord_webhook AS user_discord, u.email_notifications_enabled AS user_email_enabled
        FROM watchers w
        JOIN users u ON u.id = w.user_id
        ORDER BY w.id ASC
        """
    ).fetchall()

    ran = 0
    skipped = 0

    for row in watchers:
        watcher = dict(row)
        user = {
            "id": watcher["user_id"],
            "email": watcher["user_email"],
            "discord_webhook": watcher["user_discord"],
            "email_notifications_enabled": watcher["user_email_enabled"],
        }

        if not _should_run(watcher, interval):
            skipped += 1
            continue

        ran += 1
        try:
            run_one(con, watcher, user)
        except Exception:
            log.exception("check_crash", extra={"watcher_id": watcher.get("id")})

    con.close()

    log.info("run_done", extra={"ran": ran, "skipped": skipped, "total": len(watchers)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
