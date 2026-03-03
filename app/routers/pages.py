from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

from app.core.config import default_check_interval_minutes, public_base_url
from app.core.mailer import smtp_configured
from app.core.security import ensure_csrf_token, validate_csrf
from app.core.utils import (
    compute_next_check,
    keywords_to_storage,
    parse_keywords,
    storage_to_keywords,
    utc_now_iso,
)
from app.db import connect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _require_user(request: Request) -> Dict[str, Any]:
    uid = request.session.get("user_id")
    if not uid:
        raise _redirect("/login")
    con = connect()
    user = con.execute(
        "SELECT id, email, discord_webhook, email_notifications_enabled, created_at FROM users WHERE id = ?",
        (int(uid),),
    ).fetchone()
    con.close()
    if not user:
        request.session.clear()
        raise _redirect("/login")
    return dict(user)


def _redirect(url: str):
    return RedirectResponse(url, status_code=HTTP_303_SEE_OTHER)


def _parse_iso(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@router.get("/")
def dashboard(request: Request):
    user = _require_user(request)
    csrf_token = ensure_csrf_token(request)

    con = connect()
    watchers = con.execute(
        """
        SELECT id, name, url, is_active, keywords,
               notify_on_change, notify_on_new_jobs, notify_on_keyword,
               last_status, last_checked_at, last_error, last_http_status,
               blocked_count, failed_count
        FROM watchers
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user["id"],),
    ).fetchall()

    # last 7 days check count (nice-to-have)
    con.close()

    interval = default_check_interval_minutes()
    rows = []
    for w in watchers:
        last_checked_at = w["last_checked_at"]
        status = (w["last_status"] or "–")
        # Backoff factor: 0 ok, 1 failed, 2 blocked
        backoff = 0
        if status == "failed":
            backoff = min(4, int(w["failed_count"] or 1))
        elif status == "blocked":
            backoff = min(5, int(w["blocked_count"] or 1))
        next_iso = compute_next_check(last_checked_at, interval, backoff_factor=backoff)
        rows.append(
            {
                **dict(w),
                "keywords_list": storage_to_keywords(w["keywords"]),
                "next_check_at": next_iso,
            }
        )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "watchers": rows,
            "csrf_token": csrf_token,
            "smtp_ok": smtp_configured(),
            "public_base": public_base_url(),
            "interval_minutes": interval,
        },
    )


@router.get("/watchers/new")
def watcher_new_page(request: Request):
    user = _require_user(request)
    csrf_token = ensure_csrf_token(request)
    return templates.TemplateResponse(
        "watcher_form.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "mode": "new",
            "watcher": {
                "name": "",
                "url": "",
                "keywords": "",
                "notify_on_change": 1,
                "notify_on_new_jobs": 1,
                "notify_on_keyword": 1,
                "discord_webhook_override": "",
                "is_active": 1,
            },
        },
    )


@router.post("/watchers/new")
def watcher_create(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    keywords: str = Form(""),
    notify_on_change: Optional[str] = Form(None),
    notify_on_new_jobs: Optional[str] = Form(None),
    notify_on_keyword: Optional[str] = Form(None),
    discord_webhook_override: str = Form(""),
    is_active: Optional[str] = Form(None),
    csrf_token: str = Form(""),
):
    user = _require_user(request)
    validate_csrf(request, csrf_token)

    name = (name or "").strip()[:80]
    url = (url or "").strip()

    # Minimal validation: requires http(s)
    if not (url.startswith("http://") or url.startswith("https://")):
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "watcher_form.html",
            {
                "request": request,
                "user": user,
                "csrf_token": csrf_token,
                "mode": "new",
                "error": "Bitte eine vollständige URL mit http:// oder https:// eingeben.",
                "watcher": {
                    "name": name,
                    "url": url,
                    "keywords": keywords,
                    "notify_on_change": 1 if notify_on_change else 0,
                    "notify_on_new_jobs": 1 if notify_on_new_jobs else 0,
                    "notify_on_keyword": 1 if notify_on_keyword else 0,
                    "discord_webhook_override": discord_webhook_override,
                    "is_active": 1 if is_active else 0,
                },
            },
        )

    kw = parse_keywords(keywords)

    con = connect()
    con.execute(
        """
        INSERT INTO watchers(user_id, name, url, is_active, keywords,
                             notify_on_change, notify_on_new_jobs, notify_on_keyword,
                             discord_webhook_override, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user["id"],
            name,
            url,
            1 if is_active else 0,
            keywords_to_storage(kw),
            1 if notify_on_change else 0,
            1 if notify_on_new_jobs else 0,
            1 if notify_on_keyword else 0,
            (discord_webhook_override or "").strip() or None,
            utc_now_iso(),
        ),
    )
    con.commit()
    con.close()

    return _redirect("/")


@router.get("/watchers/{watcher_id}")
def watcher_detail(request: Request, watcher_id: int):
    user = _require_user(request)
    csrf_token = ensure_csrf_token(request)

    con = connect()
    w = con.execute(
        "SELECT * FROM watchers WHERE id = ? AND user_id = ?",
        (int(watcher_id), user["id"]),
    ).fetchone()
    if not w:
        con.close()
        return _redirect("/")

    results = con.execute(
        """
        SELECT id, checked_at, status, http_status, error_message, changed,
               keyword_hits_json, new_links_count, sample_links_json
        FROM check_results
        WHERE watcher_id = ?
        ORDER BY checked_at DESC
        LIMIT 60
        """,
        (int(watcher_id),),
    ).fetchall()

    links = con.execute(
        "SELECT url, title, first_seen_at FROM job_links WHERE watcher_id = ? ORDER BY first_seen_at DESC LIMIT 50",
        (int(watcher_id),),
    ).fetchall()
    con.close()

    def _loads(s: Optional[str]):
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None

    parsed_results = []
    for r in results:
        parsed_results.append(
            {
                **dict(r),
                "keyword_hits": _loads(r["keyword_hits_json"]) or {},
                "sample_links": _loads(r["sample_links_json"]) or [],
            }
        )

    return templates.TemplateResponse(
        "watcher_detail.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "watcher": {**dict(w), "keywords_list": storage_to_keywords(w["keywords"])},
            "results": parsed_results,
            "links": [dict(x) for x in links],
            "smtp_ok": smtp_configured(),
            "public_base": public_base_url(),
        },
    )


@router.get("/watchers/{watcher_id}/edit")
def watcher_edit_page(request: Request, watcher_id: int):
    user = _require_user(request)
    csrf_token = ensure_csrf_token(request)

    con = connect()
    w = con.execute(
        "SELECT * FROM watchers WHERE id = ? AND user_id = ?",
        (int(watcher_id), user["id"]),
    ).fetchone()
    con.close()
    if not w:
        return _redirect("/")

    return templates.TemplateResponse(
        "watcher_form.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "mode": "edit",
            "watcher": dict(w),
        },
    )


@router.post("/watchers/{watcher_id}/edit")
def watcher_edit(
    request: Request,
    watcher_id: int,
    name: str = Form(...),
    url: str = Form(...),
    keywords: str = Form(""),
    notify_on_change: Optional[str] = Form(None),
    notify_on_new_jobs: Optional[str] = Form(None),
    notify_on_keyword: Optional[str] = Form(None),
    discord_webhook_override: str = Form(""),
    is_active: Optional[str] = Form(None),
    csrf_token: str = Form(""),
):
    user = _require_user(request)
    validate_csrf(request, csrf_token)

    name = (name or "").strip()[:80]
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "watcher_form.html",
            {
                "request": request,
                "user": user,
                "csrf_token": csrf_token,
                "mode": "edit",
                "error": "Bitte eine vollständige URL mit http:// oder https:// eingeben.",
                "watcher": {
                    "id": watcher_id,
                    "name": name,
                    "url": url,
                    "keywords": keywords,
                    "notify_on_change": 1 if notify_on_change else 0,
                    "notify_on_new_jobs": 1 if notify_on_new_jobs else 0,
                    "notify_on_keyword": 1 if notify_on_keyword else 0,
                    "discord_webhook_override": discord_webhook_override,
                    "is_active": 1 if is_active else 0,
                },
            },
        )

    kw = parse_keywords(keywords)

    con = connect()
    con.execute(
        """
        UPDATE watchers
        SET name = ?, url = ?, is_active = ?, keywords = ?,
            notify_on_change = ?, notify_on_new_jobs = ?, notify_on_keyword = ?,
            discord_webhook_override = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            name,
            url,
            1 if is_active else 0,
            keywords_to_storage(kw),
            1 if notify_on_change else 0,
            1 if notify_on_new_jobs else 0,
            1 if notify_on_keyword else 0,
            (discord_webhook_override or "").strip() or None,
            int(watcher_id),
            user["id"],
        ),
    )
    con.commit()
    con.close()

    return _redirect(f"/watchers/{watcher_id}")


@router.post("/watchers/{watcher_id}/delete")
def watcher_delete(
    request: Request,
    watcher_id: int,
    csrf_token: str = Form(""),
):
    user = _require_user(request)
    validate_csrf(request, csrf_token)

    con = connect()
    con.execute(
        "DELETE FROM watchers WHERE id = ? AND user_id = ?",
        (int(watcher_id), user["id"]),
    )
    con.commit()
    con.close()

    return _redirect("/")


@router.post("/watchers/{watcher_id}/toggle")
def watcher_toggle(
    request: Request,
    watcher_id: int,
    csrf_token: str = Form(""),
):
    user = _require_user(request)
    validate_csrf(request, csrf_token)

    con = connect()
    w = con.execute(
        "SELECT is_active FROM watchers WHERE id = ? AND user_id = ?",
        (int(watcher_id), user["id"]),
    ).fetchone()
    if w:
        new_val = 0 if int(w["is_active"] or 0) == 1 else 1
        con.execute(
            "UPDATE watchers SET is_active = ? WHERE id = ? AND user_id = ?",
            (new_val, int(watcher_id), user["id"]),
        )
        con.commit()
    con.close()
    return _redirect("/")


@router.get("/settings")
def settings_page(request: Request):
    user = _require_user(request)
    csrf_token = ensure_csrf_token(request)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "csrf_token": csrf_token,
            "smtp_ok": smtp_configured(),
            "public_base": public_base_url(),
        },
    )


@router.post("/settings")
def settings_save(
    request: Request,
    discord_webhook: str = Form(""),
    email_notifications_enabled: Optional[str] = Form(None),
    csrf_token: str = Form(""),
):
    user = _require_user(request)
    validate_csrf(request, csrf_token)

    webhook = (discord_webhook or "").strip() or None
    enabled = 1 if email_notifications_enabled else 0

    con = connect()
    con.execute(
        "UPDATE users SET discord_webhook = ?, email_notifications_enabled = ? WHERE id = ?",
        (webhook, enabled, user["id"]),
    )
    con.commit()
    con.close()

    return _redirect("/settings")
