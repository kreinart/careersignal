from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER

from app.core.security import (
    ensure_csrf_token,
    hash_password,
    login_rate_limit,
    validate_csrf,
    verify_password,
)
from app.core.utils import utc_now_iso
from app.db import connect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user_id"))


def _valid_email(email: str) -> bool:
    email = (email or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


@router.get("/login")
def login_page(request: Request):
    csrf_token = ensure_csrf_token(request)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "csrf_token": csrf_token},
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
):
    validate_csrf(request, csrf_token)

    email = (email or "").strip().lower()
    ip = request.client.host if request.client else "unknown"
    login_rate_limit(ip, email)

    con = connect()
    row = con.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()
    con.close()

    if not row or not verify_password(password, row["password_hash"]):
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Login fehlgeschlagen.", "csrf_token": csrf_token},
        )

    request.session["user_id"] = int(row["id"])
    request.session["user_email"] = row["email"]
    return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)


@router.get("/register")
def register_page(request: Request):
    csrf_token = ensure_csrf_token(request)
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None, "csrf_token": csrf_token},
    )


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    csrf_token: str = Form(""),
):
    validate_csrf(request, csrf_token)

    email = (email or "").strip().lower()
    if not _valid_email(email):
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Bitte eine gültige E-Mail eingeben.", "csrf_token": csrf_token},
        )
    if len(password or "") < 8:
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Passwort muss mindestens 8 Zeichen haben.", "csrf_token": csrf_token},
        )
    if password != password2:
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Passwörter stimmen nicht überein.", "csrf_token": csrf_token},
        )

    con = connect()
    try:
        con.execute(
            "INSERT INTO users(email, password_hash, created_at) VALUES(?,?,?)",
            (email, hash_password(password), utc_now_iso()),
        )
        con.commit()
    except Exception:
        con.close()
        csrf_token = ensure_csrf_token(request)
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "E-Mail ist bereits registriert.", "csrf_token": csrf_token},
        )
    con.close()

    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
