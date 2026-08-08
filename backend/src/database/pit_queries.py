"""pit_queries.py — Point-in-Time (PIT) capping helpers (Test Contract 2: PIT Boundary Guard).

Mọi query nạp dữ liệu tài chính phải chặn dữ liệu tương lai tại mốc mô phỏng t:
  - financial_facts: WHERE period < anchor_quarter AND (ingested_at IS NULL OR ingested_at <= t)
  - health_ratios  : WHERE period < anchor_quarter (bảng này KHÔNG có ingested_at → chặn theo period)
  - valuation_scores: WHERE period < anchor_quarter

⚠️ PHẠM VI BẢO VỆ THỰC TẾ (2026-08-08):
  Rào chắn KHẢ THI duy nhất hiện nay là **period-based only** (`period < anchor_quarter`).
  Cột `ingested_at` trong financial_facts bị ghi đè cùng một ngày backfill (2026-08) cho gần như
  mọi bản ghi cũ → điều kiện `ingested_at <= t` hoặc luôn đúng hoặc luôn sai hàng loạt, KHÔNG phản
  ánh thời điểm công bố thực tế theo từng mã/từng quý. Do đó KHÔNG dùng `ingested_at` làm tầng bảo vệ
  tin cậy. Hệ quả: mức nghiêm ngặt thực tế = chặn theo kỳ kế toán, giả định độ trễ công bố cố định
  theo quý — không phản ánh công ty A công bố sớm/B trễ, hay báo cáo điều chỉnh sau kiểm toán.
  Hướng nâng cấp tương lai: thêm publication_date thực từ nguồn cào → tầng 2 PIT cho Live Execution.

Rào chắn này triệt tiêu Look-ahead Bias cho backtest walk-forward (LAW PIT Integrity).

Module Sentinel v2.1 (Anchor Fix) — mọi module src/ phải có _hydrate_path.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()


def date_to_quarter(target_date: str) -> str:
    """Đổi date 'YYYY-MM-DD' → period 'YYYYQx'."""
    try:
        y, m, _ = str(target_date).split("-")
        return f"{int(y)}Q{(int(m) - 1) // 3 + 1}"
    except TypeError, ValueError, AttributeError, IndexError:
        return "0000Q1"


def fetch_financial_facts_pit(
    conn: sqlite3.Connection,
    symbol: str,
    as_of_date: str,
    metrics: list[str] | None = None,
) -> list[dict]:
    """Nạp BCTC PIT: KHÔNG trả về bản ghi có period > anchor_quarter, hoặc có
    ingested_at > as_of_date (dữ liệu chưa tồn tại tại thời điểm mô phỏng).

    Bảng financial_facts có cột ingested_at; nếu NULL → chỉ chặn theo period.
    """
    anchor_q = date_to_quarter(as_of_date)
    # Chuẩn nghiêm: period < anchor_quarter — BCTC quý hiện tại chưa được công bố
    # tại thời điểm mô phỏng (VD: tại 2025-01-02, quý 2025Q1 chưa tồn tại).
    sql = (
        "SELECT period, metric, value, ingested_at FROM financial_facts "
        "WHERE symbol = ? AND period < ? "
        "AND (ingested_at IS NULL OR ingested_at <= ?)"
    )
    params: list = [symbol, anchor_q, as_of_date]
    if metrics:
        placeholders = ",".join("?" for _ in metrics)
        sql += f" AND metric IN ({placeholders})"
        params.extend(metrics)
    sql += " ORDER BY period"
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def fetch_health_ratios_pit(
    conn: sqlite3.Connection,
    symbol: str,
    as_of_date: str,
    ratio_name: str | None = None,
) -> list[dict]:
    """Nạp health_ratios PIT. Bảng này không có ingested_at → chỉ chặn theo
    period < anchor_quarter (best-effort theo cấu trúc bảng hiện có)."""
    anchor_q = date_to_quarter(as_of_date)
    sql = "SELECT symbol, period, ratio_name, ratio_value FROM health_ratios WHERE symbol = ? AND period < ?"
    params: list = [symbol, anchor_q]
    if ratio_name:
        sql += " AND ratio_name = ?"
        params.append(ratio_name)
    sql += " ORDER BY period"
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def period_end_date(period: str) -> str:
    """Suy Period End Date từ period 'YYYYQx' → ngày cuối quý 'YYYY-MM-DD'."""
    try:
        y, _, q = period.partition("Q")
        if not q:
            return ""
        q = int(q)
        m = (q - 1) * 3 + 1
        last_day = {3: 31, 6: 30, 9: 30, 12: 31}[m + 2]
        return f"{int(y):04d}-{m + 2:02d}-{last_day:02d}"
    except ValueError, KeyError, TypeError, AttributeError:
        return ""


def run_pit_audit(
    conn: sqlite3.Connection,
    symbols: list[str] | None = None,
    as_of_date: str | None = None,
    limit: int = 50,
) -> dict:
    """PIT Audit Report — đối soát 2 mốc CÓ THẬT: Period End + Ingested.

    Báo cáo KHÔNG bịa Publication Date (reported_at NULL 100% trong DB hiện tại).
    Với mỗi bản ghi: tính Period End từ `period`, so với `ingested_at` và mốc
    `as_of_date` → phân loại khả dụng PIT. Trả về dict (không ghi DB).
    """
    sql = "SELECT symbol, period, metric, value, ingested_at, source FROM financial_facts WHERE 1=1"
    params: list = []
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        sql += f" AND symbol IN ({placeholders})"
        params.extend(symbols)
    sql += " ORDER BY symbol, period, metric"
    if limit:
        sql += f" LIMIT {limit}"
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        return {"error": str(e), "records": [], "summary": {}}

    anchor_q = date_to_quarter(as_of_date) if as_of_date else None
    records = []
    for r in rows:
        d = dict(r)
        d["period_end"] = period_end_date(d.get("period", ""))
        ing = (d.get("ingested_at") or "")[:10]
        d["ingested_date"] = ing
        # Guard PIT: period < anchor (luôn áp dụng) + ingested <= as_of (nếu có)
        period_ok = anchor_q is None or str(d.get("period", "")) < anchor_q
        ingest_ok = anchor_q is None or not ing or ing <= as_of_date
        d["visible_at_asof"] = bool(period_ok and ingest_ok)
        records.append(d)

    total = len(records)
    visible = sum(1 for x in records if x["visible_at_asof"])
    summary = {
        "as_of_date": as_of_date,
        "total_records": total,
        "visible_at_asof": visible,
        "blocked_by_period": sum(1 for x in records if anchor_q and str(x.get("period", "")) >= anchor_q),
        "blocked_by_ingested": sum(
            1
            for x in records
            if anchor_q and str(x.get("period", "")) < anchor_q and x.get("ingested_date") and x["ingested_date"] > as_of_date
        ),
        "note": "Publication Date KHÔNG tồn tại trong DB (reported_at NULL 100%) — audit 2 mốc Period End + Ingested.",
    }
    return {"records": records, "summary": summary}


if __name__ == "__main__":
    import json

    conn = sqlite3.connect(str(PROJECT_ROOT / "backend" / "data" / "financial_facts.db"))
    rows = fetch_financial_facts_pit(conn, "VCB", "2025-01-02", metrics=["NET_PROFIT"])
    print(json.dumps(rows[:3], default=str, ensure_ascii=False, indent=2))
    conn.close()
