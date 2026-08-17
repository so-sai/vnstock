"""ifs_china_gold_reserve_adapter.py — Adapter ingest IMF IFS "Changes in World
Official Gold Reserves" cho RIÊNG country China (P.R.: Mainland) → gold_h2_series.

Vì sao cần module riêng (KHÔNG merge vào ifs_gold_reserve_adapter):
  - Adapter gốc tính `entity=GLOBAL` (tổng theo entity rule). GLOBAL ĐÃ gồm
    China — ingest China riêng KHÔNG được overwrite GLOBAL (contract user).
  - M3 feature `China_Treasury_Gold_Shift` cần RIÊNG China gold Δ tonnes; nếu
    dùng GLOBAL sẽ trộn ~160 entity khác → nhiễu.
  - Per-country là extraction MỚI từ cùng 52 vintage files — phải là series
    riêng (source=IMF_IFS, series=IFS_CHINA_GOLD_RESERVE_CHANGE).

Vintage semantics (giống adapter gốc, KHÔNG tự suy diễn):
  - file `as_of_M` -> observation T có pub = HTTP Last-Modified của file (chỉ từ
    backend/data/cache/wgc_cb/ifs_last_modified.csv). File không có LM -> bỏ,
    đưa NEEDS_MANUAL. KHÔNG đoán publication.
  - observation = cuối tháng (month-end), như parse_snapshot của adapter gốc.
  - PIT invariant: observation <= publication (chặn phòng hờ).

LOOKUP = 'China, P.R.: Mainland' (entity name chuẩn trong IFS). Dùng substring
match (giống entity rule gốc). 'China, P.R.: Macao' / 'Taiwan Province of China'
KHÔNG match lookup này — giữ riêng biệt, không cộng.

Usage (từ project root):
  python -X utf8 backend/src/research/ifs_china_gold_reserve_adapter.py --ingest --db backend/data/gold_h2.db
  python -X utf8 backend/src/research/ifs_china_gold_reserve_adapter.py --ingest --db x.db --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl  # noqa: F401  (giữ phụ thuộc đồng bộ với adapter gốc)


# ── Sentinel v2.1 (Anchor Fix) — đồng bộ với các module research khác ──────
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
    for p in [str(root_path / "backend" / "src"), str(root_path / "backend"), str(root_path)]:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
CACHE_DIR = DATA_DIR / "cache" / "wgc_cb"
IFS_DIR = CACHE_DIR / "ifs_changes"
LM_CSV = CACHE_DIR / "ifs_last_modified.csv"
REPORT_DIR = DATA_DIR / "reports"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

from src.research.ifs_gold_reserve_adapter import (
    _parse_lm_csv,
    parse_snapshot,
)

SERIES = "IFS_CHINA_GOLD_RESERVE_CHANGE"
ENTITY = "CHINA_MAINLAND"
UNIT = "tonnes"
SOURCE = "IMF_IFS"
LOOKUP = "China, P.R.: Mainland"


def build_china_vintages(ifs_dir: Path, lm_csv: Path) -> tuple[list[dict], list[dict]]:
    """Duyệt 52 vintage files → (rows contract China, needs_manual).

    Với mỗi file M (publication = Last-Modified): với MỌI observation T trong
    file, tạo một vintage row (obs=T, pub=LM(file), value=entity_value China).
    File thiếu Last-Modified -> NEEDS_MANUAL. Không network.
    """
    lm = _parse_lm_csv(lm_csv)
    rows: list[dict] = []
    manual: list[dict] = []
    for p in sorted(ifs_dir.glob("Changes_latest_as_of_*_IFS.xlsx")):
        pub = lm.get(p.name)
        if not pub:
            manual.append({"file": p.name, "reason": "missing Last-Modified"})
            continue
        try:
            snap = parse_snapshot(p)
        except ValueError as exc:
            manual.append({"file": p.name, "reason": str(exc)})
            continue
        if not any(LOOKUP in name for name, _ in snap["entities"]):
            manual.append({"file": p.name, "reason": f"missing entity '{LOOKUP}'"})
            continue
        for obs in sorted(snap["col_obs"].values()):
            val = entity_value(snap, obs, LOOKUP)
            if val is None:
                continue
            if obs > pub:
                manual.append({"file": p.name, "reason": f"obs {obs} > pub {pub}"})
                continue
            rows.append(
                {
                    "series": SERIES,
                    "entity": ENTITY,
                    "observation_date": obs,
                    "publication_date": pub,
                    "value": val,
                    "unit": UNIT,
                    "source": SOURCE,
                    "provenance": (
                        f"IMF IFS Changes file {p.name}; publication=Last-Modified {pub}; "
                        f"data_to={snap['data_to']}; lookup={LOOKUP!r} (per-country, "
                        f"KHÔNG merge GLOBAL); entity={ENTITY}"
                    ),
                    "metadata": f"{{'file': {p.name!r}, 'data_to': {snap['data_to']!r}, 'lookup': {LOOKUP!r}}}",
                }
            )
    return rows, manual


def entity_value(snap: dict, obs_ymd: str, lookup: str) -> float | None:
    """Giá trị entity China tại observation (re-export từ adapter gốc)."""
    from src.research.ifs_gold_reserve_adapter import entity_value as _ev

    return _ev(snap, obs_ymd, lookup)


def ingest(rows: list[dict], db_path: str | None, dry_run: bool) -> None:
    import sqlite3

    from src.research.gold_h2_series import init_table, insert_vintage

    if db_path is None:
        db_path = str(DATA_DIR / "gold_h2.db")
    conn = sqlite3.connect(db_path)
    init_table(conn)
    n = 0
    for r in rows:
        if dry_run:
            print(f"  [DRY] {r['observation_date']}  {r['value']:+.1f}t  pub={r['publication_date']}")
            continue
        insert_vintage(
            conn,
            series=r["series"],
            entity=r["entity"],
            observation_date=r["observation_date"],
            publication_date=r["publication_date"],
            value=r["value"],
            unit=r["unit"],
            source=r["source"],
            provenance=r["provenance"],
            metadata=r["metadata"],
        )
        n += 1
    conn.close()
    if not dry_run:
        print(f"  [OK] ingest {n} dòng vào {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="IMF IFS China per-country vintage → gold_h2_series")
    parser.add_argument("--ingest", action="store_true", help="ingest từ cache xlsx")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--dry-run", action="store_true", help="chỉ in, không ghi")
    parser.add_argument("--ifs-dir", default=str(IFS_DIR))
    parser.add_argument("--lm-csv", default=str(LM_CSV))
    parser.add_argument("--report", default=str(REPORT_DIR / "ifs_china_gold_reserve_needs_manual.csv"))
    args = parser.parse_args()

    if not args.ingest:
        parser.print_help()
        return

    rows, manual = build_china_vintages(Path(args.ifs_dir), Path(args.lm_csv))
    print(f"\n  parse: {len(rows)} dòng vintage ingest được, {len(manual)} NEEDS_MANUAL\n")
    if manual:
        print("  NEEDS_MANUAL (thiếu Last-Modified / parse lỗi / PIT vi phạm / thiếu entity):")
        for m in manual:
            print(f"    - {m['file']}: {m['reason']}")
        import csv

        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["file", "reason"])
            w.writeheader()
            for m in manual:
                w.writerow(m)
        print(f"\n  [OK] NEEDS_MANUAL report -> {args.report}")

    if rows:
        ingest(rows, args.db, args.dry_run)


if __name__ == "__main__":
    main()
