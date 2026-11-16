from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import locks as lock_repo

HOLDER_REPORT = "reconcile_report"
HOLDER_APPLY = "apply_due"
TTL_SECONDS = 120
BUSY_DETAIL = "另一项价签操作正在写入数据库，操作被拒绝；请稍后重试。"


def _conflict_detail(lock: dict[str, Any]) -> str:
    if lock.get("holder") == HOLDER_REPORT:
        return (
            "对账冻结报告正在生成（钉扎业务钟与价格水位，开始于 "
            f"{lock.get('started_at')}），应用到期已拒绝；请等报告生成完再应用。"
        )
    return (
        "改价单应用正在进行（开始于 "
        f"{lock.get('started_at')}，批次 {lock.get('detail') or '—'}），"
        "生成冻结报告已拒绝；请等应用完成再钉扎。"
    )


def acquire(holder: str, *, detail: str | None = None) -> str:
    """Commit a visible ops lock before doing work.

    Committing first is what makes the conflict observable to a concurrent
    request instead of both requests quietly interleaving.
    """
    token = uuid.uuid4().hex
    try:
        conn = connect(immediate=True, timeout=2.0)
    except sqlite3.OperationalError:
        raise HTTPException(409, BUSY_DETAIL)
    try:
        ok = lock_repo.try_acquire(
            conn,
            holder=holder,
            token=token,
            ttl_seconds=TTL_SECONDS,
            detail=detail,
        )
        if not ok:
            raise HTTPException(409, _conflict_detail(lock_repo.peek(conn) or {}))
        conn.commit()
        return token
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release(token: str) -> None:
    conn = connect(immediate=True)
    try:
        lock_repo.release(conn, token)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_in(conn, token: str) -> None:
    """Drop the lock inside the caller's transaction so it disappears atomically
    with the commit that finishes the work (no false-conflict window in between)."""
    lock_repo.release(conn, token)
