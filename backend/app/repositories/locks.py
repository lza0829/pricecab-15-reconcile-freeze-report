from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def peek(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the currently valid (non-expired) ops lock, if any."""
    row = conn.execute("SELECT * FROM ops_locks WHERE id=1").fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires <= now_utc():
        return None
    return data


def try_acquire(
    conn: sqlite3.Connection,
    *,
    holder: str,
    token: str,
    ttl_seconds: int,
    detail: str | None = None,
) -> bool:
    """Acquire the single ops lock inside an open IMMEDIATE transaction.

    Returns False when another (non-expired) operation holds it. Expired locks
    are overwritten so a crashed request cannot wedge the system forever.
    """
    current = peek(conn)
    if current and current["token"] != token:
        return False
    now = now_utc()
    conn.execute(
        '''
        INSERT INTO ops_locks(id, holder, token, started_at, expires_at, detail)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          holder=excluded.holder,
          token=excluded.token,
          started_at=excluded.started_at,
          expires_at=excluded.expires_at,
          detail=excluded.detail
        ''',
        (holder, token, iso(now), iso(now + timedelta(seconds=ttl_seconds)), detail),
    )
    return True


def release(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM ops_locks WHERE id=1 AND token=?", (token,))
