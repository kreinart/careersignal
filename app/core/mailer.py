from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def send_email(to_addr: str, subject: str, body: str) -> Optional[str]:
    """Send SMTP email.

    Returns None on success, or an error string if sending failed or SMTP not configured.
    """
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    from_addr = os.getenv("SMTP_FROM")

    if not host or not from_addr:
        return "SMTP nicht konfiguriert"

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            # StartTLS for typical 587, but allow plain if port 25/others.
            if os.getenv("SMTP_STARTTLS", "1").strip() != "0":
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPException:
                    # server might not support it
                    pass
            if user:
                s.login(user, password)
            s.send_message(msg)
        return None
    except Exception as e:
        return str(e)
