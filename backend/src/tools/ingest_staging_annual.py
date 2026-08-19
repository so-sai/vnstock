"""ingest_staging_annual.py — Nạp BCTC ANNUAL từ Staging (vnfinancialdata Parquet) vào DB.

WHY (BƯỚC 3 — Staging Ingestion): thư viện đối chứng chứa dữ liệu KIỂM TOÁN theo NĂM
(không có quý). Bảng `financial_facts_annual` lưu bản sao chuẩn hóa này trong DB với
source=`audited_staging` (Provenance Lock bảo vệ, không ai ghi đè được) — làm nguồn
kiểm toán annual-level mà các engine có thể truy vấn mà không cần đọc parquet.

Fail-closed: item_name trùng (ambiguous, nhiều giá trị khác nhau cho cùng khái niệm)
→ SKIP, không bịa. Chỉ nạp metric trong ENTITY_METRIC_MAP (REVENUE/NET_INCOME/
TOTAL_EQUITY) — ngoài map → không đoán.

CLI: python backend/src/tools/ingest_staging_annual.py [--year 2025] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _hydrate_path() -> Path:
    """Thêm project root vào sys.path (giống cross_check_facts_2025)."""
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

from src.tools.cross_check_facts_2025 import (
    ENTITY_METRIC_MAP,
    EXCHANGES,
    STAGING_DIR,
    STATEMENT_TYPES,
    _normalize_item_name,
)

# metric -> statement_type (đồng nhất mọi entity)
_METRIC_STATEMENT = {
    "REVENUE": "IS",
    "NET_INCOME": "IS",
    "TOTAL_EQUITY": "BS",
}

SOURCE = "audited_staging"


def _load_staging_frames(staging_dir: Path) -> list:
    import pandas as pd

    frames = []
    for stmt in STATEMENT_TYPES:
        for exc in EXCHANGES:
            p = staging_dir / "data" / stmt / f"{exc}.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                if "item_name" not in df.columns or "ticker" not in df.columns or "year" not in df.columns:
                    continue
                df["_stmt"] = stmt
                frames.append(df)
    return frames


def ingest_staging_annual(db, staging_dir: Path, year: int | None = None) -> dict:
    """Nạp staging annual vào bảng financial_facts_annual (upsert, fail-closed)."""
    import pandas as pd

    frames = _load_staging_frames(staging_dir)
    if not frames:
        return {"symbols": 0, "facts": 0, "ambiguous": 0, "skipped": 0}
    df = pd.concat(frames, ignore_index=True).copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["item_name_norm"] = df["item_name"].map(_normalize_item_name)
    if year is not None:
        df = df[df["year"] == year]

    conn = db.connect()
    cur = conn.cursor()
    written = 0
    ambiguous = 0
    skipped = 0
    symbols: set[str] = set()

    # Nhóm theo (ticker, year, item_name_norm) — nếu nhiều giá trị khác nhau → ambiguous
    grouped = df.groupby(["ticker", "year", "item_name_norm", "_stmt"], dropna=False)
    for (ticker, fy, norm, stmt), grp in grouped:
        try:
            ticker = str(ticker).upper()
            fy = int(fy)
        except TypeError, ValueError:
            skipped += 1
            continue
        if pd.isna(ticker) or not ticker or pd.isna(fy):
            skipped += 1
            continue

        values = {float(v) for v in grp["value"] if v is not None}
        if len(values) != 1:
            ambiguous += 1
            continue
        value = values.pop()

        # Map metric theo entity: dùng registry nếu có, else thử mọi entity map
        et_row = conn.execute("SELECT entity_type FROM entity_registry WHERE symbol=?", (ticker,)).fetchone()
        matched = False
        candidate_maps: list[tuple[str, dict]] = []
        if et_row is not None:
            et = et_row[0]
            if et in ENTITY_METRIC_MAP:
                candidate_maps.append((et, ENTITY_METRIC_MAP[et]))
        if not candidate_maps:
            candidate_maps = list(ENTITY_METRIC_MAP.items())

        for _et, metric_map in candidate_maps:
            for metric, item_norm in metric_map.items():
                if norm == item_norm:
                    st = _METRIC_STATEMENT.get(metric)
                    if st is None:
                        continue
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO financial_facts_annual
                            (symbol, fiscal_year, statement_type, metric, value, unit, source,
                             is_synthetic, integrity_flags)
                        VALUES (?, ?, ?, ?, ?, 'VND', ?, 0, '')
                        """,
                        (ticker, fy, st, metric, value, SOURCE),
                    )
                    written += 1
                    symbols.add(ticker)
                    matched = True
        if not matched:
            skipped += 1

    conn.commit()
    return {
        "symbols": len(symbols),
        "facts": written,
        "ambiguous": ambiguous,
        "skipped": skipped,
    }


VERIFIED = "VERIFIED_BY_AUDITED_ANNUAL"
MISMATCHED = "MISMATCH_FAIL_CLOSED"


def apply_annual_verification(db, staging_dir: Path, year: int = 2025, threshold: float = 0.05) -> dict:
    """Gắn cờ kiểm định annual lên bảng financial_facts_annual từ kết quả cross-check.

    Audit Verification Gate (kiến trúc 2 tầng): đối chiếu Σ(4 quý DB) vs annual staging.
    - MATCH (|Δ| ≤ threshold) → verification_status='VERIFIED_BY_AUDITED_ANNUAL'
      (cấp phép dùng cho VN20 Funnel / định giá).
    - MISMATCH → 'MISMATCH_FAIL_CLOSED' (tự động VETO, fail-closed).
    - NO_DATA/incomplete → giữ '' (chưa đủ điều kiện kiểm định, không phê duyệt).
    Chỉ ghi lên dòng annual đã tồn tại trong DB (source=audited_staging).
    """
    from src.tools.cross_check_facts_2025 import (
        build_cross_check,
        load_ff_annual,
        load_vnf_annual,
    )

    ff = load_ff_annual(Path(db.db_path), year=year)
    vnf = load_vnf_annual(staging_dir, year=year)
    entities = {s: e for s, e in db.connect().execute("SELECT symbol, entity_type FROM entity_registry")}
    rows = build_cross_check(ff, vnf, entities, year=year, threshold=threshold)

    conn = db.connect()
    cur = conn.cursor()
    verified = 0
    mismatched = 0
    for r in rows:
        status = r["status"]
        flag = None
        if status == "MATCH":
            flag = VERIFIED
        elif status == "MISMATCH":
            flag = MISMATCHED
        elif status == "KNOWN_GAP_EXPLAINED":
            flag = VERIFIED  # đã chứng minh nguyên nhân → vẫn đủ điều kiện dùng
        if flag is None:
            continue
        cur.execute(
            "UPDATE financial_facts_annual SET verification_status=? WHERE symbol=? AND fiscal_year=? AND metric=?",
            (flag, r["symbol"], year, r["metric"]),
        )
        if cur.rowcount:
            if flag == VERIFIED:
                verified += 1
            else:
                mismatched += 1
    conn.commit()
    return {"verified": verified, "mismatched": mismatched, "total_rows": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Nạp staging annual vào financial_facts_annual")
    ap.add_argument("--staging-dir", type=Path, default=STAGING_DIR)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="gắn cờ VERIFIED_BY_AUDITED_ANNUAL sau khi nạp")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.financial.financial_facts import FinancialFactsDB

    db = FinancialFactsDB()
    db.init_schema()
    if args.dry_run:
        print("[ingest] dry-run — chỉ đếm staging, không ghi DB")
        frames = _load_staging_frames(args.staging_dir)
        if not frames:
            print("[ingest] staging rỗng")
            return 1
        import pandas as pd

        df = pd.concat(frames, ignore_index=True)
        yrs = sorted(df["year"].dropna().unique())[-3:]
        print(f"[ingest] staging rows={len(df)} tickers={df['ticker'].nunique()} years={yrs}")
        return 0

    stats = ingest_staging_annual(db, args.staging_dir, year=args.year)
    print(f"[ingest] done: {stats}")
    if args.verify:
        year = args.year or 2025
        vstats = apply_annual_verification(db, args.staging_dir, year=year)
        print(f"[verify] {year}: {vstats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
