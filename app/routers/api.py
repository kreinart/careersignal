from __future__ import annotations

from fastapi import APIRouter

from app.db import connect

router = APIRouter()


@router.get("/v1/health")
def health():
    # tiny DB ping
    con = connect()
    con.execute("SELECT 1")
    con.close()
    return {"ok": True}
