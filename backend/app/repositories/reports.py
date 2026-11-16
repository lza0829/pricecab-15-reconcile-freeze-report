from __future__ import annotations

import sqlite3
from typing import Any


STATUS_LABELS = {
    "active": "现行",
    "voided": "已作废",
    "archived": "非现行 · 只读存档",
    "superseded": "已被新报告取代 · 非现行",
}


def next_report_no(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(report_no, 4) AS INTEGER)) n FROM reconcile_reports"
    ).fetchone()
    n = int(row["n"] or 0) + 1
    return f"RR-{n:04d}"


def history_watermark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM price_history").fetchone()
    return int(row["m"])


def history_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM price_history WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def price_at_watermark(
    conn: sqlite3.Connection, *, sku_code: str, at: str, watermark: int
) -> dict[str, Any] | None:
    """Price entry effective at ``at`` using only history rows pinned by watermark."""
    row = conn.execute(
        '''
        SELECT id, price, source_ticket_no, effective_at, recorded_at
        FROM price_history
        WHERE sku_code=? AND effective_at <= ? AND id <= ?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, at, watermark),
    ).fetchone()
    return dict(row) if row else None


def create_report(
    conn: sqlite3.Connection,
    *,
    report_no: str,
    business_clock: str,
    history_watermark: int,
    watermark_recorded_at: str | None,
    status: str,
    retire_policy: str,
    clock_applied_due: int,
    clock_unapplied_due: str,
    sale_count: int,
    match_count: int,
    mismatch_count: int,
    missing_count: int,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO reconcile_reports(
          report_no, business_clock, history_watermark, watermark_recorded_at,
          status, retire_policy, clock_applied_due, clock_unapplied_due,
          sale_count, match_count, mismatch_count,
          missing_count, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''',
        (
            report_no,
            business_clock,
            history_watermark,
            watermark_recorded_at,
            status,
            retire_policy,
            clock_applied_due,
            clock_unapplied_due,
            sale_count,
            match_count,
            mismatch_count,
            missing_count,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def add_row(
    conn: sqlite3.Connection,
    *,
    report_id: int,
    sale_no: str,
    sku_code: str,
    sold_at: str,
    unit_price: float,
    qty: int,
    expected_price: float | None,
    price_entry_id: int | None,
    price_effective_at: str | None,
    source_ticket_no: str | None,
    delta: float | None,
    status: str,
) -> None:
    conn.execute(
        '''
        INSERT INTO reconcile_report_rows(
          report_id, sale_no, sku_code, sold_at, unit_price, qty,
          expected_price, price_entry_id, price_effective_at,
          source_ticket_no, delta, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''',
        (
            report_id,
            sale_no,
            sku_code,
            sold_at,
            unit_price,
            qty,
            expected_price,
            price_entry_id,
            price_effective_at,
            source_ticket_no,
            delta,
            status,
        ),
    )


def list_reports(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM reconcile_reports ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_report_by_no(conn: sqlite3.Connection, report_no: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM reconcile_reports WHERE report_no=?", (report_no,)
    ).fetchone()
    return dict(row) if row else None


def get_report(conn: sqlite3.Connection, report_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM reconcile_reports WHERE id=?", (report_id,)
    ).fetchone()
    return dict(row) if row else None


def list_report_rows(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM reconcile_report_rows WHERE report_id=? ORDER BY sold_at, id",
        (report_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def current_report(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM reconcile_reports WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def supersede_active(conn: sqlite3.Connection, *, by_report_no: str) -> None:
    conn.execute(
        '''
        UPDATE reconcile_reports
        SET status='superseded', superseded_by=?
        WHERE status='active'
        ''',
        (by_report_no,),
    )


def retire_all(
    conn: sqlite3.Connection,
    *,
    status: str,
    policy: str,
    retired_at: str,
    reason: str,
    batch_no: str,
) -> list[dict[str, Any]]:
    """Apply the configured fate to every report that predates this apply."""
    rows = conn.execute(
        '''
        SELECT report_no, status FROM reconcile_reports
        WHERE status IN ('active','superseded')
        ORDER BY id
        '''
    ).fetchall()
    conn.execute(
        '''
        UPDATE reconcile_reports
        SET status=?, retire_policy=?, retired_at=?, retired_reason=?,
            retired_by_batch_no=?
        WHERE status IN ('active','superseded')
        ''',
        (status, policy, retired_at, reason, batch_no),
    )
    return [dict(r) for r in rows]
