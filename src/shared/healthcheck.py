"""Shared healthcheck helpers for Cartograph HTTP services."""

from __future__ import annotations


def db_status() -> tuple[bool, str | None]:
    """Ping Postgres via the shared pool. Returns (ok, error_message)."""
    try:
        from shared.db import execute_one, get_pool

        get_pool()
        execute_one("SELECT 1 AS ok")
        return True, None
    except RuntimeError as exc:
        if "not initialized" in str(exc):
            return False, "db pool not initialized"
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def healthcheck_payload(service: str) -> tuple[dict, int]:
    """Build JSON body and HTTP status for GET /healthcheck."""
    db_ok, db_err = db_status()
    body: dict = {
        "status": "ok" if db_ok else "unhealthy",
        "service": service,
        "db": db_ok,
    }
    if db_err:
        body["db_error"] = db_err
    return body, 200 if db_ok else 503
