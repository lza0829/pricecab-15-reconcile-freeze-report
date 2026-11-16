from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Additive migration for databases created before a column existed."""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_schema(conn) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skus (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          list_price REAL NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS price_tickets (
          id INTEGER PRIMARY KEY,
          ticket_no TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          effective_at TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          applied_at TEXT,
          apply_batch_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS price_ticket_lines (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER NOT NULL REFERENCES price_tickets(id) ON DELETE CASCADE,
          sku_code TEXT NOT NULL,
          old_price REAL NOT NULL,
          new_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batches (
          id INTEGER PRIMARY KEY,
          batch_no TEXT NOT NULL UNIQUE,
          clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_book (
          sku_code TEXT PRIMARY KEY,
          current_price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL,
          price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales_fixture (
          id INTEGER PRIMARY KEY,
          sale_no TEXT NOT NULL UNIQUE,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hold_applies (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reconcile_reports (
          id INTEGER PRIMARY KEY,
          report_no TEXT NOT NULL UNIQUE,
          business_clock TEXT NOT NULL,
          history_watermark INTEGER NOT NULL,
          watermark_recorded_at TEXT,
          status TEXT NOT NULL,
          retire_policy TEXT NOT NULL,
          retired_at TEXT,
          retired_reason TEXT,
          retired_by_batch_no TEXT,
          superseded_by TEXT,
          clock_applied_due INTEGER NOT NULL DEFAULT 0,
          clock_unapplied_due TEXT,
          sale_count INTEGER NOT NULL,
          match_count INTEGER NOT NULL,
          mismatch_count INTEGER NOT NULL,
          missing_count INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reconcile_report_rows (
          id INTEGER PRIMARY KEY,
          report_id INTEGER NOT NULL REFERENCES reconcile_reports(id) ON DELETE CASCADE,
          sale_no TEXT NOT NULL,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL,
          expected_price REAL,
          price_entry_id INTEGER,
          price_effective_at TEXT,
          source_ticket_no TEXT,
          delta REAL,
          status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_rows_report
          ON reconcile_report_rows(report_id);
        CREATE INDEX IF NOT EXISTS idx_price_history_sku_effective
          ON price_history(sku_code, effective_at, id);
        CREATE TABLE IF NOT EXISTS ops_locks (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          holder TEXT NOT NULL,
          token TEXT NOT NULL,
          started_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          detail TEXT
        );
        '''
    )
    _ensure_columns(
        conn,
        "reconcile_reports",
        {
            "clock_applied_due": "INTEGER NOT NULL DEFAULT 0",
            "clock_unapplied_due": "TEXT",
        },
    )


def seed_if_empty() -> None:
    conn = connect()
    try:
        init_schema(conn)
        # Defaults that must exist even on an already-seeded database.
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('report_retire_policy', 'void')"
        )
        n = conn.execute("SELECT COUNT(*) c FROM skus").fetchone()["c"]
        if n:
            conn.commit()
            return

        skus = json.loads((FIXTURES / "skus.json").read_text(encoding="utf-8"))
        sales = json.loads((FIXTURES / "sales.json").read_text(encoding="utf-8"))
        now = _now()
        # Opening price is in force from before any fixture sale, so a sale can
        # always be reconciled against the price that was effective at sold_at.
        opening_effective_at = "2026-11-01T00:00:00Z"
        for s in skus:
            conn.execute(
                "INSERT INTO skus(sku_code, title, category, list_price, enabled) VALUES (?,?,?,?,1)",
                (s["sku_code"], s["title"], s["category"], float(s["list_price"])),
            )
            conn.execute(
                "INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), opening_effective_at, now),
            )
            conn.execute(
                "INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), opening_effective_at, now),
            )
        for row in sales:
            conn.execute(
                "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
                (row["sale_no"], row["sku_code"], row["sold_at"], float(row["unit_price"]), int(row["qty"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('business_clock', ?)",
            ("2026-11-16T09:00:00Z",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_label', ?)",
            ("业务钟（可拨，用于判断改价是否到期）",),
        )
        conn.commit()
    finally:
        conn.close()
