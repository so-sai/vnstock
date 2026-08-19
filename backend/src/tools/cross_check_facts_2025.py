"""cross_check_facts_2025.py - Đối chiếu BCTC NĂM 2025: financial_facts.db vs vnfinancialdata.

WHY: vnfinancialdata (annual, HSX/HNX) làm nguồn ĐỐI CHIẾU cho số liệu crawl
trong financial_facts.db (theo quý). Sum 4 quý -> annual rồi so delta. Zero-
Hallucination: thiếu quý / thiếu item / mã vắng mặt -> NO_DATA, không bịa số.

Item mapping theo NORMALIZED ITEM_NAME (không dựa item_code): cùng khái niệm
"Vốn chủ sở hữu" có item_code khác nhau giữa entity (bs_von_chu_so_huu_*). Map
entity-specific: BANK/SECURITIES/INSURANCE dùng line items khác STANDARD.

Usage:
    python backend/src/tools/cross_check_facts_2025.py [--year 2025] [--threshold 0.05]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path


# ── Sentinel v2.1 (Anchor Fix) ────────────────────────────────────────
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in (root_path, root_path / "backend", root_path / "backend" / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path


PROJECT_ROOT = _hydrate_path()
FF_DB = PROJECT_ROOT / "backend" / "data" / "financial_facts.db"
STAGING_DIR = PROJECT_ROOT / "backend" / "data" / "staging" / "vnfinancialdata"

STATEMENT_TYPES = ("balance_sheet", "income_statement", "cash_flow")
EXCHANGES = ("HSX", "HNX")

# entity_type -> metric -> normalized item_name trong vnfinancialdata
ENTITY_METRIC_MAP: dict[str, dict[str, str]] = {
    "STANDARD": {
        "REVENUE": "doanh so thuan",
        "NET_INCOME": "lai lo thuan sau thue",
        "TOTAL_EQUITY": "von chu so huu",
    },
    "BANK": {
        # Ngân hàng không có "Doanh số thuần" — không map revenue (zero-hallucination)
        "NET_INCOME": "loi nhuan sau thue",
        "TOTAL_EQUITY": "von chu so huu",
    },
    "SECURITIES": {
        "REVENUE": "doanh thu hoat dong",
        "NET_INCOME": "loi nhuan ke toan sau thue",
        "TOTAL_EQUITY": "von chu so huu",
    },
    "INSURANCE": {
        "NET_INCOME": "loi nhuan sau thue thu nhap doanh nghiep",
        "TOTAL_EQUITY": "von chu so huu",
    },
}

# flow: sum các quý; stock: lấy quý cao nhất có
METRIC_SEMANTICS = {
    "REVENUE": "flow",
    "NET_INCOME": "flow",
    "TOTAL_EQUITY": "stock",
}

METRICS = ("REVENUE", "NET_INCOME", "TOTAL_EQUITY")
FULL_YEAR_QUARTERS = 4

# KNOWN_DATA_GAPS — ngoại lệ ĐÃ CHỨNG MINH: (symbol, metric) -> (mã lý do, ghi chú).
# WHY (Zero-Hallucination): một số MISMATCH đến từ khác biệt định nghĩa kế toán
# hoặc đối chứng, KHÔNG phải lỗi số liệu DB. Phân loại riêng KNOWN_GAP_EXPLAINED
# để MISMATCH còn lại phản ánh đúng lỗi thật cần audit. Chỉ áp dụng khi trạng thái
# hiện tại là MISMATCH (không biến MATCH/NO_DATA thành gap).
#   - MBB NET_INCOME: vnf (vnfinancialdata) lấy LNST Cổ đông mẹ (27.38T) trong khi
#     DB dùng LNST Hợp nhất (28.96T, nguồn vnstock VCI + cafef) — định nghĩa khác
#     nhau, không phải lỗi. STB từng nằm ở đây với lý do "ground truth thiếu H2"
#     NHƯNG probe CafeF Bank API (parser đã fix) chứng minh Q4/2025 thật = LỖ
#     -2.75T (DB đang giữ số 2026Q1 lệch kỳ) → STB đã được vá, giờ MATCH — không
#     được đưa lại vào gaps.
KNOWN_DATA_GAPS: dict[tuple[str, str], tuple[str, str]] = {
    ("MBB", "NET_INCOME"): (
        "ACCOUNTING_DEFINITION_VARIANCE",
        "vnf lấy LNST Cổ đông mẹ (27.38T) vs DB dùng LNST Hợp nhất (28.96T) — "
        "định nghĩa kế toán khác nhau, không phải lỗi số liệu.",
    ),
}


def _normalize_item_name(name: str) -> str:
    """'VỐN CHỦ SỞ HỮU' / 'Vốn chủ sở hữu' / 'Lãi/(lỗ) thuần sau thuế' -> chuẩn.

    Ký tự đặc biệt ('/', '(', ')') -> space (không bỏ trống) để 'Lãi/(lỗ)' và
    'Lãi lỗ' quy về cùng chuẩn; sau đó collapse khoảng trắng.
    """
    nfkd = unicodedata.normalize("NFKD", str(name).lower())
    ascii_ = "".join(c for c in nfkd if not unicodedata.combining(c))
    out = []
    for c in ascii_:
        out.append(c if (c.isalnum() or c.isspace()) else " ")
    return " ".join("".join(out).split())


def load_vnf_annual(staging_dir: Path, year: int = 2025) -> dict[str, dict[str, float]]:
    """Đọc parquet staging -> {ticker: {metric: annual_value}} theo entity map.

    Tra cứu theo normalized item_name; nếu 1 khái niệm có nhiều giá trị khác nhau
    (ambiguous) -> bỏ qua (không bịa).
    """
    import pandas as _pd

    frames = []
    try:
        for stmt in STATEMENT_TYPES:
            for exc in EXCHANGES:
                p = staging_dir / "data" / stmt / f"{exc}.parquet"
                if p.exists():
                    frames.append(_pd.read_parquet(p))
    except OSError, ValueError, RuntimeError:
        return {}
    if not frames:
        return {}
    df = _pd.concat(frames, ignore_index=True).copy()
    df["year"] = _pd.to_numeric(df["year"], errors="coerce")
    df["item_name_norm"] = df["item_name"].map(_normalize_item_name)

    # item_name -> normalized -> tập giá trị (nếu ambiguous cùng khái niệm -> loại)
    val_map: dict[tuple[str, str], set[float]] = {}
    for row in df.itertuples(index=False):
        if row.year != year:
            continue
        key = (row.ticker, row.item_name_norm)
        val_map.setdefault(key, set()).add(float(row.value))

    out: dict[str, dict[str, float]] = {}
    for (ticker, norm), vals in val_map.items():
        if len(vals) != 1:
            continue
        value = vals.pop()
        for entity, metric_map in ENTITY_METRIC_MAP.items():
            for metric, item_norm in metric_map.items():
                if norm == item_norm:
                    out.setdefault(ticker, {})[metric] = value
    return out


def load_ff_annual(db_path: Path, year: int = 2025) -> dict[str, dict[str, dict]]:
    """Đọc financial_facts.db -> {symbol: {metric: {value, quarters}}}.

    flow: sum 4 quý (chỉ khi đủ 4 quý); stock: giá trị quý cao nhất có.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT symbol, fiscal_quarter, metric, value FROM financial_facts "
            "WHERE fiscal_year=? AND metric IN (?,?,?) "
            "ORDER BY symbol, metric, fiscal_quarter",
            (year, "REVENUE", "NET_INCOME", "TOTAL_EQUITY"),
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for symbol, q, metric, value in rows:
        if value is None:
            continue
        grouped.setdefault((symbol, metric), []).append((int(q), float(value)))

    out: dict[str, dict[str, dict]] = {}
    for (symbol, metric), vals in sorted(grouped.items()):
        quarters = sorted(vals)
        if METRIC_SEMANTICS[metric] == "flow":
            if len(quarters) != FULL_YEAR_QUARTERS:
                out.setdefault(symbol, {})[metric] = {"value": None, "quarters": len(quarters)}
                continue
            out.setdefault(symbol, {})[metric] = {
                "value": sum(v for _, v in quarters),
                "quarters": len(quarters),
            }
        else:  # stock: quý cao nhất có
            out.setdefault(symbol, {})[metric] = {
                "value": quarters[-1][1],
                "quarters": len(quarters),
            }
    return out


def _entity_for(symbol: str, entities: dict[str, str]) -> str:
    return entities.get(symbol, "STANDARD")


def build_cross_check(
    ff: dict[str, dict[str, dict]],
    vnf: dict[str, dict[str, float]],
    entities: dict[str, str],
    year: int = 2025,
    threshold: float = 0.05,
) -> list[dict]:
    """Xây bảng đối chiếu: mỗi (symbol, metric) 1 dòng với status.

    status: MATCH / MISMATCH / NO_DATA (+ reason). Fail-closed, không bịa số.
    """
    rows: list[dict] = []
    # Chỉ cross-check mã có trong financial_facts (nguồn chính); mã vnf-only không thuộc đối tượng
    symbols = sorted(ff)
    for symbol in symbols:
        entity = _entity_for(symbol, entities)
        metrics = sorted(set(ENTITY_METRIC_MAP.get(entity, {})) & set(METRICS))
        for metric in metrics:
            fv = ff.get(symbol, {}).get(metric)
            nv = vnf.get(symbol, {}).get(metric)
            # FAIL-CLOSED: thiếu một bên -> NO_DATA
            if fv is None:
                rows.append(_row(symbol, entity, metric, None, nv, "NO_DATA", None, "ff_missing"))
                continue
            if fv["quarters"] != FULL_YEAR_QUARTERS:
                rows.append(_row(symbol, entity, metric, fv["value"], nv, "NO_DATA", None, "ff_quarters_incomplete"))
                continue
            if nv is None:
                reason = "vnf_missing_symbol" if symbol not in vnf else "vnf_missing_metric"
                rows.append(_row(symbol, entity, metric, fv["value"], None, "NO_DATA", None, reason))
                continue
            base = fv["value"]
            if base == 0:
                delta = None
                status = "MATCH" if nv == 0 else "MISMATCH"
            else:
                delta = 100.0 * (nv - base) / abs(base)
                status = "MATCH" if abs(delta) <= threshold * 100.0 else "MISMATCH"
            # KNOWN_DATA_GAPS: MISMATCH đã chứng minh nguyên nhân -> phân loại riêng
            reason = None
            gap = KNOWN_DATA_GAPS.get((symbol, metric))
            if status == "MISMATCH" and gap is not None:
                status = "KNOWN_GAP_EXPLAINED"
                reason = f"{gap[0]}: {gap[1]}"
            rows.append(_row(symbol, entity, metric, base, nv, status, delta, reason))
    return rows


def _row(symbol, entity, metric, ff_val, vnf_val, status, delta_pct, reason) -> dict:
    return {
        "symbol": symbol,
        "entity_type": entity,
        "metric": metric,
        "ff_value": ff_val,
        "vnf_value": vnf_val,
        "status": status,
        "delta_pct": delta_pct,
        "reason": reason,
    }


def _fmt(v) -> str:
    if v is None:
        return "NO_DATA"
    return f"{v:,.0f}"


def print_report(rows: list[dict]) -> None:
    stats = {"MATCH": 0, "MISMATCH": 0, "KNOWN_GAP_EXPLAINED": 0, "NO_DATA": 0}
    for r in rows:
        stats[r["status"]] += 1
    print(f"{'symbol':<6}{'entity':<11}{'metric':<12}{'ff_value':>18}{'vnf_value':>18}{'delta%':>9}  {'status':<20}{'reason'}")
    print("-" * 110)
    for r in rows:
        d = "" if r["delta_pct"] is None else f"{r['delta_pct']:+.1f}"
        reason = r["reason"] or ""
        print(
            f"{r['symbol']:<6}{r['entity_type']:<11}{r['metric']:<12}"
            f"{_fmt(r['ff_value']):>18}{_fmt(r['vnf_value']):>18}{d:>9}  "
            f"{r['status']:<20}{reason}"
        )
    print("-" * 110)
    print(
        f"MATCH={stats['MATCH']}  MISMATCH={stats['MISMATCH']}  "
        f"KNOWN_GAP_EXPLAINED={stats['KNOWN_GAP_EXPLAINED']}  NO_DATA={stats['NO_DATA']}  (total={len(rows)})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-check BCTC năm: financial_facts.db vs vnfinancialdata")
    parser.add_argument("--db", type=Path, default=FF_DB)
    parser.add_argument("--staging-dir", type=Path, default=STAGING_DIR)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--threshold", type=float, default=0.05)
    args = parser.parse_args()

    ff = load_ff_annual(args.db, year=args.year)
    vnf = load_vnf_annual(args.staging_dir, year=args.year)

    # entity_registry từ financial_facts.db
    entities: dict[str, str] = {}
    if args.db.exists():
        conn = sqlite3.connect(str(args.db))
        try:
            for sym, et in conn.execute("SELECT symbol, entity_type FROM entity_registry"):
                entities[sym] = et
        finally:
            conn.close()

    rows = build_cross_check(ff, vnf, entities, year=args.year, threshold=args.threshold)
    print_report(rows)
    return 0 if all(r["status"] != "MISMATCH" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
