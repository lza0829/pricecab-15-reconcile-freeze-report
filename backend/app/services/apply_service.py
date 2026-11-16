from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo
from app.services import ops_lock
from app.services import report_service


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "business_clock": settings_repo.get(conn, "business_clock"),
            "clock_label": settings_repo.get(conn, "clock_label"),
        }
    finally:
        conn.close()


def set_clock(clock_at: str) -> dict[str, Any]:
    clock_at = (clock_at or "").strip()
    if not clock_at:
        raise HTTPException(400, "业务钟不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_clock", clock_at)
        conn.commit()
        return get_clock()
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def apply_due(retire_policy: str | None = None) -> dict[str, Any]:
    """Apply all scheduled tickets whose effective_at <= business clock.

    Mutually exclusive with frozen-report generation: the visible ops lock is
    taken first, so a report being pinned rejects this apply, and an apply in
    flight rejects report generation. Reports that predate the batch are retired
    in the same transaction as the price-book rewrite.
    """
    token = ops_lock.acquire(ops_lock.HOLDER_APPLY)
    try:
        try:
            conn = connect(immediate=True, timeout=2.0)
        except sqlite3.OperationalError:
            raise HTTPException(409, ops_lock.BUSY_DETAIL)
        try:
            clock = settings_repo.get(conn, "business_clock")
            if not clock:
                raise HTTPException(400, "业务钟未设置")
            if retire_policy is not None:
                # Explicit choice for this apply; persisted so later applies and
                # the report list agree on the fate of retired reports.
                report_service.set_policy_conn(conn, retire_policy)
            due = ticket_repo.due_tickets(conn, clock)
            if not due:
                ops_lock.release_in(conn, token)
                conn.commit()
                return {
                    "applied": False,
                    "message": "当前业务钟下没有到期改价单",
                    "business_clock": clock,
                    "ticket_count": 0,
                    "line_count": 0,
                    "reports": None,
                }

            batch_no = batch_repo.next_batch_no(conn)
            created = _now()
            line_total = 0
            applied_tickets = []
            # create batch first with zeros, update after
            batch_id = batch_repo.create_batch(
                conn,
                batch_no=batch_no,
                clock_at=clock,
                ticket_count=0,
                line_count=0,
                status="running",
                created_at=created,
            )

            for t in due:
                lines = ticket_repo.list_lines(conn, t["id"])
                for line in lines:
                    price_repo.upsert_book(
                        conn,
                        sku_code=line["sku_code"],
                        price=float(line["new_price"]),
                        ticket_no=t["ticket_no"],
                        effective_at=t["effective_at"],
                        updated_at=created,
                    )
                    line_total += 1
                ticket_repo.mark_applied(conn, t["id"], applied_at=created, batch_id=batch_id)
                applied_tickets.append(t["ticket_no"])

            conn.execute(
                "UPDATE apply_batches SET ticket_count=?, line_count=?, status=? WHERE id=?",
                (len(due), line_total, "done", batch_id),
            )

            # Old reports lose their "current verdict" meaning in the same
            # transaction that rewrote the price book — never half-applied.
            reports = report_service.retire_reports_for_apply(
                conn, batch_no=batch_no, applied_at=created
            )

            ops_lock.release_in(conn, token)
            conn.commit()
            return {
                "applied": True,
                "batch_no": batch_no,
                "business_clock": clock,
                "ticket_count": len(due),
                "line_count": line_total,
                "tickets": applied_tickets,
                "reports": reports,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        ops_lock.release(token)
