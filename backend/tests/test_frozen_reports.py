"""End-to-end checks for frozen reconcile reports.

Run from backend/:
    DATA_DIR=/tmp/<fresh> python -m pytest -q        # if pytest available
or directly:
    DATA_DIR=/tmp/<fresh> python tests/test_frozen_reports.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Configure the DB location before any app module binds DB_PATH.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pricecab-test-"))

from app.config import DB_PATH  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


def _fresh_env() -> None:
    """Drop the SQLite file and re-seed so each check starts from the fixture."""
    DB_PATH.unlink(missing_ok=True)
    seed_if_empty()


def _services():
    from app.services import apply_service, report_service, ticket_service

    return report_service, apply_service, ticket_service


def test_report_pins_clock_and_watermark() -> None:
    _fresh_env()
    rs, aps, _ = _services()
    aps.set_clock("2026-11-16T13:00:00Z")
    r = rs.create_frozen_report(note="cut")
    assert r["status"] == "active"
    assert r["business_clock"] == "2026-11-16T13:00:00Z"
    assert r["history_watermark"] > 0
    assert r["counts"]["sale_count"] == 5
    assert r["counts"]["match_count"] + r["counts"]["mismatch_count"] + r["counts"]["missing_count"] == 5
    assert r["replay_verified"] is True


def test_void_policy_voids_old_report_and_freezes_numbers() -> None:
    _fresh_env()
    rs, aps, ts = _services()
    aps.set_clock("2026-11-16T13:00:00Z")
    rs.set_policy("void")
    r1 = rs.create_frozen_report()
    before = {x["sale_no"]: x["expected_price"] for x in r1["rows"]}
    assert r1["counts"]["mismatch_count"] == 1

    ts.create_ticket(
        {
            "title": "promo",
            "effective_at": "2026-11-16T10:00:00Z",
            "lines": [{"sku_code": "SKU-DAY-021", "new_price": 42.0}],
        }
    )
    res = aps.apply_due()
    assert res["applied"] is True
    assert res["reports"]["policy"] == "void"
    assert res["reports"]["retired_report_nos"] == [r1["report_no"]]

    old = rs.get_frozen_report(r1["report_no"])
    assert old["status"] == "voided"
    assert old["is_current"] is False
    assert old["read_only"] is True
    assert "作废" in old["notice"]
    # Frozen numbers unchanged even though the price book was rewritten.
    assert {x["sale_no"]: x["expected_price"] for x in old["rows"]} == before
    assert old["replay_verified"] is True

    listing = rs.list_frozen_reports()
    assert listing["current_report_no"] is None

    # A recut on the new basis sees the applied price and matches.
    r2 = rs.create_frozen_report()
    assert r2["status"] == "active"
    assert r2["counts"]["mismatch_count"] == 0


def test_archive_policy_keeps_report_readable_but_not_current() -> None:
    _fresh_env()
    rs, aps, ts = _services()
    aps.set_clock("2026-11-16T13:00:00Z")
    r1 = rs.create_frozen_report()
    ts.create_ticket(
        {
            "title": "promo",
            "effective_at": "2026-11-16T10:00:00Z",
            "lines": [{"sku_code": "SKU-DAY-021", "new_price": 42.0}],
        }
    )
    res = aps.apply_due(retire_policy="archive")
    assert res["reports"]["new_status"] == "archived"

    old = rs.get_frozen_report(r1["report_no"])
    assert old["status"] == "archived"
    assert old["is_current"] is False
    assert old["read_only"] is True
    assert "非现行" in old["notice"]
    assert old["rows"][2]["expected_price"] == 45.0
    assert old["rows"][2]["status"] == "mismatch"

    listing = rs.list_frozen_reports()
    assert listing["policy"] == "archive"
    assert listing["current_report_no"] is None

    r2 = rs.create_frozen_report()
    listing = rs.list_frozen_reports()
    assert listing["current_report_no"] == r2["report_no"]
    statuses = {x["report_no"]: x["status"] for x in listing["reports"]}
    assert statuses[r1["report_no"]] == "archived"
    assert statuses[r2["report_no"]] == "active"


def test_new_cut_supersedes_previous_active_report() -> None:
    _fresh_env()
    rs, aps, _ = _services()
    aps.set_clock("2026-11-16T13:00:00Z")
    r1 = rs.create_frozen_report()
    r2 = rs.create_frozen_report()
    assert r2["superseded_report_no"] == r1["report_no"]
    assert rs.get_frozen_report(r1["report_no"])["status"] == "superseded"
    assert r2["same_basis_as_previous"] is True
    assert "业务钟和水位相同" in r2["basis_warning"]


def test_pin_and_apply_are_mutually_exclusive() -> None:
    _fresh_env()
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import ops_lock

    client = TestClient(app)
    client.put("/api/apply/clock", json={"business_clock": "2026-11-16T13:00:00Z"})

    tok = ops_lock.acquire(ops_lock.HOLDER_REPORT)
    r = client.post("/api/apply/due")
    assert r.status_code == 409
    assert "冻结报告正在生成" in r.json()["detail"]
    ops_lock.release(tok)

    tok = ops_lock.acquire(ops_lock.HOLDER_APPLY, detail="AB-9999")
    r = client.post("/api/reconcile/reports", json={})
    assert r.status_code == 409
    assert "应用正在进行" in r.json()["detail"]
    ops_lock.release(tok)

    assert client.post("/api/reconcile/reports", json={}).status_code == 200


def test_apply_reports_clock_inconsistency() -> None:
    _fresh_env()
    rs, aps, ts = _services()
    aps.set_clock("2026-11-16T13:00:00Z")
    ts.create_ticket(
        {
            "title": "still scheduled",
            "effective_at": "2026-11-16T10:00:00Z",
            "lines": [{"sku_code": "SKU-DAY-021", "new_price": 42.0}],
        }
    )
    r = rs.create_frozen_report()
    assert r["clock_snapshot"]["unapplied_due_ticket_nos"] == ["PT-0001"]
    detail = rs.get_frozen_report(r["report_no"])
    assert detail["clock_consistent"] is False
    assert "PT-0001" in detail["clock_warning"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback

            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
