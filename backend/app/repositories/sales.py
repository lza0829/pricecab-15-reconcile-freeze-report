from __future__ import annotations

import sqlite3
from typing import Any


def list_sales(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sales_fixture ORDER BY sold_at, id"
    ).fetchall()
    return [dict(r) for r in rows]


def count_sales_after(conn: sqlite3.Connection, clock_at: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM sales_fixture WHERE sold_at > ?", (clock_at,)
    ).fetchone()
    return int(row["c"])
