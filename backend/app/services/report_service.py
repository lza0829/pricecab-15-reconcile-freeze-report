from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import reports as report_repo
from app.repositories import sales as sales_repo
from app.repositories import settings as settings_repo
from app.services import ops_lock

RETIRE_POLICIES = {
    "void": {
        "label": "旧报告作废",
        "status": "voided",
        "description": (
            "应用到期改写价签后，该批次之前的报告一律作废：查询标明作废，"
            "不得再当现行结论，须用新业务钟或新价格水位重切报告。"
        ),
    },
    "archive": {
        "label": "旧报告只读存档",
        "status": "archived",
        "description": (
            "应用到期改写价签后，该批次之前的报告转为只读存档：仍可打开查看，"
            "但必须标「非现行」；现行结论只能来自最新未作废报告。"
        ),
    },
}
DEFAULT_POLICY = "void"


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _policy_key(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key not in RETIRE_POLICIES:
        raise HTTPException(400, f"未知的旧报告策略：{value!r}（可选 void / archive）")
    return key


def get_policy(conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or connect()
    try:
        key = settings_repo.get(conn, "report_retire_policy", DEFAULT_POLICY) or DEFAULT_POLICY
        if key not in RETIRE_POLICIES:
            key = DEFAULT_POLICY
        return {"policy": key, **RETIRE_POLICIES[key]}
    finally:
        if own:
            conn.close()


def set_policy(policy: str) -> dict[str, Any]:
    key = _policy_key(policy)
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "report_retire_policy", key)
        conn.commit()
    finally:
        conn.close()
    return get_policy()


def set_policy_conn(conn, policy: str) -> str:
    """Validate + persist the policy inside a caller's open transaction."""
    key = _policy_key(policy)
    settings_repo.set_value(conn, "report_retire_policy", key)
    return key


def _clock_snapshot(conn, clock: str) -> dict[str, Any]:
    """State of the pinned clock: what the clock says should be in force.

    Makes the pinned business clock auditable instead of a decorative field: a
    ticket due at the clock but still ``scheduled`` means the clock and the price
    book disagreed at cut time, which is exactly what a frozen report must record.
    """
    rows = conn.execute(
        '''
        SELECT ticket_no, effective_at FROM price_tickets
        WHERE status='scheduled' AND effective_at <= ?
        ORDER BY effective_at, id
        ''',
        (clock,),
    ).fetchall()
    applied = conn.execute(
        "SELECT COUNT(*) c FROM price_tickets WHERE status='applied' AND effective_at <= ?",
        (clock,),
    ).fetchone()["c"]
    return {
        "applied_due_ticket_count": int(applied),
        "unapplied_due_ticket_nos": [r["ticket_no"] for r in rows],
        "sales_after_clock": sales_repo.count_sales_after(conn, clock),
    }


def _decorate(report: dict[str, Any]) -> dict[str, Any]:
    data = dict(report)
    status = data["status"]
    data["status_label"] = report_repo.STATUS_LABELS.get(status, status)
    policy = data.get("retire_policy")
    data["retire_policy_label"] = (
        RETIRE_POLICIES[policy]["label"] if policy in RETIRE_POLICIES else None
    )
    data["is_current"] = status == "active"
    data["read_only"] = status != "active"
    if status == "active":
        data["notice"] = "现行冻结报告：可作为当前对账结论。"
    elif status == "voided":
        data["notice"] = "此报告已作废，不得再当现行结论；请用新业务钟或新价格水位重切报告。"
    elif status == "archived":
        data["notice"] = "非现行只读存档：仍可打开复查，但现行结论只能来自最新未作废报告。"
    else:
        data["notice"] = "非现行：已被更新的冻结报告取代，仅供参考。"
    data["pin_statement"] = (
        f"业务钟钉扎于 {data['business_clock']}；"
        f"价格历史水位钉扎于 price_history.id ≤ {data['history_watermark']}"
        + (
            f"（该水位记录时间 {data['watermark_recorded_at']}）"
            if data.get("watermark_recorded_at")
            else ""
        )
        + "。生成后新增的应用不改变本报告内数字。"
    )
    return data


def _build_rows(conn, *, watermark: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    # The sales fixture is a fixed ledger, so every recorded sale is in scope —
    # the pinned business clock is the "as-of" identity of the conclusion, not a
    # row filter. Prices are taken only from history rows at or below the pinned
    # watermark, so a later apply cannot change any number inside this report.
    sales = sales_repo.list_sales(conn)
    rows: list[dict[str, Any]] = []
    counts = {"match": 0, "mismatch": 0, "missing_price": 0}
    for s in sales:
        entry = report_repo.price_at_watermark(
            conn, sku_code=s["sku_code"], at=s["sold_at"], watermark=watermark
        )
        sold = float(s["unit_price"])
        if entry is None:
            status = "missing_price"
            expected = None
            delta = None
        else:
            expected = float(entry["price"])
            if abs(expected - sold) < 1e-9:
                status = "match"
                delta = 0.0
            else:
                status = "mismatch"
                delta = round(sold - expected, 4)
        counts[status] += 1
        rows.append(
            {
                "sale_no": s["sale_no"],
                "sku_code": s["sku_code"],
                "sold_at": s["sold_at"],
                "unit_price": sold,
                "qty": int(s["qty"]),
                "expected_price": expected,
                "price_entry_id": int(entry["id"]) if entry else None,
                "price_effective_at": entry["effective_at"] if entry else None,
                "source_ticket_no": entry["source_ticket_no"] if entry else None,
                "delta": delta,
                "status": status,
            }
        )
    return rows, counts


def create_frozen_report(*, note: str | None = None, hold_ms: int = 0) -> dict[str, Any]:
    """Pin the business clock + price-history watermark and persist the verdict.

    The ops lock (committed up front, so an ``apply_due`` racing us sees it) plus
    the IMMEDIATE read transaction below guarantee no ticket can be applied
    while the numbers are being pinned. The watermark makes the report
    reproducible later even after the price book is rewritten.

    ``hold_ms`` is a bounded demo knob: it keeps the pin visibly "in flight" so
    the apply-vs-pin conflict can be observed on a small fixture ledger.
    """
    token = ops_lock.acquire(ops_lock.HOLDER_REPORT)
    try:
        hold_ms = max(0, min(int(hold_ms or 0), 5000))
        if hold_ms:
            time.sleep(hold_ms / 1000.0)
        try:
            conn = connect(immediate=True, timeout=2.0)
        except sqlite3.OperationalError:
            raise HTTPException(409, ops_lock.BUSY_DETAIL)
        try:
            clock = settings_repo.get(conn, "business_clock")
            if not clock:
                raise HTTPException(400, "业务钟未设置，无法钉扎对账基线")

            watermark = report_repo.history_watermark(conn)
            policy = get_policy(conn)["policy"]

            rows, counts = _build_rows(conn, watermark=watermark)
            created = now_iso()
            report_no = report_repo.next_report_no(conn)

            # Exactly one report can be the current verdict. A new cut replaces
            # the previous active report but never rewrites its stored numbers.
            previous = report_repo.current_report(conn)
            same_basis = bool(
                previous
                and previous["business_clock"] == clock
                and int(previous["history_watermark"]) == watermark
            )
            if previous:
                report_repo.supersede_active(conn, by_report_no=report_no)
            watermark_row = conn.execute(
                "SELECT recorded_at FROM price_history WHERE id=?", (watermark,)
            ).fetchone()
            watermark_ts = watermark_row["recorded_at"] if watermark_row else None

            clock_snapshot = _clock_snapshot(conn, clock)

            report_id = report_repo.create_report(
                conn,
                report_no=report_no,
                business_clock=clock,
                history_watermark=watermark,
                watermark_recorded_at=watermark_ts,
                status="active",
                retire_policy=policy,
                clock_applied_due=clock_snapshot["applied_due_ticket_count"],
                clock_unapplied_due=",".join(clock_snapshot["unapplied_due_ticket_nos"]),
                sale_count=len(rows),
                match_count=counts["match"],
                mismatch_count=counts["mismatch"],
                missing_count=counts["missing_price"],
                created_at=created,
            )
            for row in rows:
                report_repo.add_row(conn, report_id=report_id, **row)

            ops_lock.release_in(conn, token)
            conn.commit()
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

    conn = connect()
    try:
        data = get_frozen_report(report_no)
    finally:
        conn.close()
    data["superseded_report_no"] = previous["report_no"] if previous else None
    data["retire_policy"] = policy
    data["retire_policy_label"] = RETIRE_POLICIES[policy]["label"]
    data["note"] = (note or "").strip() or None
    data["same_basis_as_previous"] = same_basis
    if same_basis:
        data["basis_warning"] = (
            f"与上一份现行报告 {previous['report_no']} 业务钟和水位相同，"
            "结论不会变化；如需反映新应用，请先拨业务钟或应用到期改写价签后再重切。"
        )
    return data


def list_frozen_reports() -> dict[str, Any]:
    conn = connect()
    try:
        policy = get_policy(conn)
        current = report_repo.current_report(conn)
        reports = [_decorate(r) for r in report_repo.list_reports(conn)]
        return {
            **policy,
            "current_report_no": current["report_no"] if current else None,
            "reports": reports,
        }
    finally:
        conn.close()


def get_frozen_report(report_no: str) -> dict[str, Any]:
    conn = connect()
    try:
        report = report_repo.get_report_by_no(conn, report_no)
        if not report:
            raise HTTPException(404, "冻结报告不存在")
        data = _decorate(report)
        rows = report_repo.list_report_rows(conn, report["id"])

        # Reproducibility check: replay the pinned watermark rows exactly as the
        # report did and re-derive price / status / delta / bucket totals. This
        # never consults the live price book, so a later apply cannot change what
        # the report says; any drift would be surfaced, not hidden.
        replay_rows: list[dict[str, Any]] = []
        mismatches = 0
        replay_counts = {"match": 0, "mismatch": 0, "missing_price": 0}
        for row in rows:
            entry = report_repo.price_at_watermark(
                conn,
                sku_code=row["sku_code"],
                at=row["sold_at"],
                watermark=report["history_watermark"],
            )
            replay_price = float(entry["price"]) if entry else None
            sold = float(row["unit_price"])
            if replay_price is None:
                replay_status = "missing_price"
                replay_delta = None
            elif abs(replay_price - sold) < 1e-9:
                replay_status = "match"
                replay_delta = 0.0
            else:
                replay_status = "mismatch"
                replay_delta = round(sold - replay_price, 4)
            replay_counts[replay_status] += 1
            recorded = row["expected_price"]
            price_same = (entry is None and recorded is None) or (
                entry is not None
                and recorded is not None
                and abs(replay_price - float(recorded)) < 1e-9
            )
            same = (
                price_same
                and replay_status == row["status"]
                and (
                    (row["delta"] is None and replay_delta is None)
                    or (
                        row["delta"] is not None
                        and replay_delta is not None
                        and abs(float(row["delta"]) - replay_delta) < 1e-9
                    )
                )
            )
            if not same:
                mismatches += 1
            replay_rows.append(
                {
                    "sale_no": row["sale_no"],
                    "recorded_price": recorded,
                    "replay_price": replay_price,
                    "recorded_status": row["status"],
                    "replay_status": replay_status,
                    "same": same,
                }
            )
        data["rows"] = rows
        data["replay_verified"] = mismatches == 0
        data["replay_mismatch_count"] = mismatches
        data["replay_rows"] = replay_rows
        stored_counts = {
            "match_count": report["match_count"],
            "mismatch_count": report["mismatch_count"],
            "missing_count": report["missing_count"],
        }
        data["counts"] = {
            "sale_count": report["sale_count"],
            **stored_counts,
        }
        data["counts_replay_verified"] = (
            report["sale_count"] == len(rows)
            and stored_counts["match_count"] == replay_counts["match"]
            and stored_counts["mismatch_count"] == replay_counts["mismatch"]
            and stored_counts["missing_count"] == replay_counts["missing_price"]
        )
        unapplied = [t for t in (report.get("clock_unapplied_due") or "").split(",") if t]
        data["clock_snapshot"] = {
            "applied_due_ticket_count": report["clock_applied_due"],
            "unapplied_due_ticket_nos": unapplied,
            "sales_after_clock": sales_repo.count_sales_after(
                conn, report["business_clock"]
            ),
        }
        data["clock_consistent"] = not unapplied
        if unapplied:
            data["clock_warning"] = (
                "钉扎时钟下仍有到期但未应用的改价单："
                + "、".join(unapplied)
                + "；报告数字按当时价格历史水位，未含这些改价。"
            )
        return data
    finally:
        conn.close()


def retire_reports_for_apply(conn, *, batch_no: str, applied_at: str) -> dict[str, Any]:
    """Apply the configured fate to every report cut before this apply batch.

    Runs inside the apply transaction, so a report either sees the pre-apply
    history or is marked retired alongside the price change — never half of each.
    """
    policy_key = get_policy(conn)["policy"]
    spec = RETIRE_POLICIES[policy_key]
    retired = report_repo.retire_all(
        conn,
        status=spec["status"],
        policy=policy_key,
        retired_at=applied_at,
        reason=f"应用批次 {batch_no} 改写了价签，报告水位之前的价格历史已非最新。",
        batch_no=batch_no,
    )
    return {
        "policy": policy_key,
        "policy_label": spec["label"],
        "new_status": spec["status"],
        "retired_report_nos": [r["report_no"] for r in retired],
        "retired_count": len(retired),
        "description": spec["description"],
    }
