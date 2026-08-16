"""ifs_gold_reserve_adapter.py — Adapter ingest IMF IFS "Changes in World Official
Gold Reserves" vintage ladder → gold_h2_series.

KHÁC WGC blog (một con số net purchases/tháng): IFS file `as_of_<M>` là một
SNAPSHOT public mỗi tháng (publication = HTTP Last-Modified của file), chứa toàn
bộ chuỗi monthly "changes" (tonnes) của ~160 entity từ 2001-12 tới M-2 (lag 2-5
tháng). Mỗi snapshot là một VINTAGE — observation T có nhiều vintage (mỗi file
công bố sau T là một bản revision).

Vintage semantics (đã xác nhận qua discovery 2026-08):
    - file `as_of_M` -> data_to = M-2 (tháng cao nhất có giá trị).
    - observation T lần đầu xuất hiện trong file `as_of_(T+2)`.
    - publication_date (thực) = HTTP Last-Modified của file (lấy từ
      backend/data/cache/wgc_cb/ifs_last_modified.csv).
    - KHÔNG suy diễn publication: file không có trong CSV (chưa crawl
      Last-Modified) -> bỏ qua, đưa vào NEEDS_MANUAL.

Entity rule (verdict user 2026-08, ghi rõ trong provenance):
    - LOẠI  : "Euro Area" (aggregate chứa các nước eurozone đã có riêng),
              "Turkey*" (lookup "Türkiye, Republic of" trùng Türkiye),
              "Netherlands Antilles" (tan rã 2010, kế thừa Curacao & St. Maarten).
    - GIỮ   : "Turkey", "SOFAZ", "Curacao & St. Maarten", toàn bộ còn lại.

INVARIANT PIT (giống gold_h2_series): feature usable at t <=> publication_date <= t.
Chuỗi IFS là series RIÊNG (source=IMF_IFS) — KHÔNG merge với WGC_CB_BLOG.
Source Consistency Gate: PASS cho tách series, FAIL cho merge (verdict user).

Usage (từ project root):
  python -X utf8 backend/src/research/ifs_gold_reserve_adapter.py --ingest --db backend/data/gold_h2.db
  python -X utf8 backend/src/research/ifs_gold_reserve_adapter.py --ingest --db x.db --dry-run
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import sys
from pathlib import Path

import openpyxl


# ── Sentinel v2.1 (Anchor Fix) ──────────────────────────────────────────────
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

SERIES = "IFS_GOLD_RESERVE_CHANGE"
ENTITY = "GLOBAL"
UNIT = "tonnes"
SOURCE = "IMF_IFS"

# Entity rule chuẩn (verdict user). Không được đổi nếu không có lý do có
# provenance — mọi đổi thay phải cập nhật provenance + audit.
EXCLUDE_ENTITIES = ("Euro Area", "Turkey*", "Netherlands Antilles")
KEEP_EXPLICIT = ("Turkey", "State Oil Fund of the Republic of Azerbaijan (SOFAZ)", "Curacao & St. Maarten")

# Dòng date luôn ở index 4 (SOFAZ month column row — đã scan toàn bộ 52 file:
# hdr=5/6/7 dịch chuyển, nhưng index 4 là date row tháng-end ổn định).
DATE_ROW_INDEX = 4
_HEADER_MARKER = "Country Lookup Column"


def _month_end_from_dt(dt: datetime.datetime) -> str:
    """Chuẩn hóa bất kỳ datetime → CUỐI THÁNG (YYYY-MM-DD).

    File IFS không đồng nhất: file 2020 ghi day-1 (2001-12-01), file mới ghi
    month-end (2026-06-30). Observation là MONTHLY → mọi key phải là month-end
    để vintage ladder của cùng obs khớp nhau giữa các file.
    """
    return f"{dt.year:04d}-{dt.month:02d}-{calendar.monthrange(dt.year, dt.month)[1]:02d}"


def parse_snapshot(path: Path) -> dict:
    """Parse một file IFS snapshot → dict.

    Trả về:
      {
        'path': Path,
        'col_obs': {col_index: 'YYYY-MM-DD' (month-end)},
        'entities': [(name, row_tuple)],  # row có data bắt đầu từ col 3
        'data_to': 'YYYY-MM-DD' | None,   # observation tháng cao nhất
        'min_obs': 'YYYY-MM-DD' | None,
      }

    Cấu trúc đã xác nhận: date row index 4 (month-end dates); header row chứa
    'Country Lookup Column' tại col 0 (index 5/6/7 tuỳ file — detect động);
    dữ liệu entity bắt đầu ngay sau header row. KHÔNG hardcode index data row.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Monthly"]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
        # openpyxl read_only không đóng zipfile trên Windows → giải phóng để
        # tránh file lock (PermissionError khi xoá cache).
        archive = getattr(wb, "_archive", None)
        if archive is not None and hasattr(archive, "close"):
            archive.close()

    if len(rows) <= DATE_ROW_INDEX + 1:
        raise ValueError(f"file {path.name}: quá ít dòng ({len(rows)})")

    drow = rows[DATE_ROW_INDEX]
    col_obs: dict[int, str] = {}
    for c, v in enumerate(drow):
        if isinstance(v, datetime.datetime):
            col_obs[c] = _month_end_from_dt(v)
    if not col_obs:
        raise ValueError(f"file {path.name}: không tìm thấy date row tại index {DATE_ROW_INDEX}")

    # CHUẨN HÓA: observation luôn là CUỐI THÁNG. Một số file (2020) ghi day-1;
    # Dec2024/Jan/Feb2025 có nhiều cột cho cùng tháng (2024-10-01/02/03) —
    # dữ liệu cập nhật dần trong snapshot. Nếu nhiều cột map về cùng month-end:
    # giữ cột LỚN NHẤT (bản mới nhất trong snapshot), ghi warning.
    best_col: dict[str, int] = {}
    for c, o in sorted(col_obs.items()):
        best_col[o] = c  # sorted → c lớn nhất ghi đè
    col_obs = {c: o for o, c in best_col.items()}
    raw_n = sum(1 for v in drow if isinstance(v, datetime.datetime))
    if len(col_obs) < raw_n:
        print(f"  [WARN] {path.name}: {raw_n - len(col_obs)} cột trùng observation month -> giữ cột mới nhất")

    hdr_i = next(
        (i for i, r in enumerate(rows) if r and str(r[0]).strip() == _HEADER_MARKER),
        None,
    )
    if hdr_i is None:
        raise ValueError(f"file {path.name}: không tìm thấy header '{_HEADER_MARKER}'")

    entities = []
    for r in rows[hdr_i + 1 :]:
        if not r or r[1] is None or str(r[1]).strip() == "Country":
            continue
        entities.append((str(r[1]).strip(), r))

    obs_values = list(col_obs.values())
    return {
        "path": path,
        "col_obs": col_obs,
        "entities": entities,
        "min_obs": min(obs_values),
        "data_to": max(obs_values),
    }


def sum_changes(snap: dict, obs_ymd: str) -> float | None:
    """Tổng monthly changes (tonnes) tại observation obs_ymd theo entity rule.

    Chỉ cộng các entity có giá trị numeric tại cột đó. Entity loại trừ
    (EXCLUDE_ENTITIES) bị bỏ. Entity không report tại cột đó (None/'') bị bỏ
    (không tính 0 — chưa công bố ≠ 0 tấn).
    """
    col = next((c for c, o in snap["col_obs"].items() if o == obs_ymd), None)
    if col is None:
        return None
    total = 0.0
    any_numeric = False
    for name, r in snap["entities"]:
        if any(e in name for e in EXCLUDE_ENTITIES):
            continue
        if col >= len(r):
            continue
        v = r[col]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += float(v)
            any_numeric = True
    return round(total, 4) if any_numeric else None


def _parse_lm_csv(csv_path: Path) -> dict[str, str]:
    """ifs_last_modified.csv → {filename: 'YYYY-MM-DD'} (từ HTTP date).

    Parse 'Thu, 02 Apr 2020 07:55:50 GMT' bằng email.utils. Nếu không parse
    được → bỏ (NEEDS_MANUAL ở mức cao hơn). KHÔNG suy diễn publication.
    """
    import csv
    import email.utils

    out: dict[str, str] = {}
    if not csv_path.exists():
        return out
    with open(csv_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            fn = (r.get("filename") or "").strip()
            lm = (r.get("last_modified") or "").strip()
            if not fn or not lm:
                continue
            dt = email.utils.parsedate_to_datetime(lm)
            if dt is None:
                continue
            out[fn] = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
    return out


def build_vintages(ifs_dir: Path, lm_csv: Path) -> tuple[list[dict], list[dict]]:
    """Duyệt toàn bộ vintage files → (rows contract, needs_manual).

    Mỗi file M (publication = Last-Modified): với MỌI observation T trong file,
    tạo một vintage row (obs=T, pub=LM(file), value=sum theo entity rule).
    File thiếu Last-Modified → NEEDS_MANUAL. Không network.
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
        for obs in sorted(snap["col_obs"].values()):
            val = sum_changes(snap, obs)
            if val is None:
                continue
            # PIT invariant: observation luôn <= publication (data_to <= M-2
            # và pub là đầu tháng M). Chặn phòng hờ.
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
                        f"data_to={snap['data_to']}; rule=EXCLUDE{EXCLUDE_ENTITIES} "
                        f"KEEP{KEEP_EXPLICIT}; n_entities={len(snap['entities'])}"
                    ),
                    "metadata": f"{{'file': {p.name!r}, 'data_to': {snap['data_to']!r}}}",
                }
            )
    return rows, manual


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
    parser = argparse.ArgumentParser(description="IMF IFS vintage ladder → gold_h2_series adapter")
    parser.add_argument("--ingest", action="store_true", help="ingest từ cache xlsx")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--dry-run", action="store_true", help="chỉ in, không ghi")
    parser.add_argument("--ifs-dir", default=str(IFS_DIR))
    parser.add_argument("--lm-csv", default=str(LM_CSV))
    parser.add_argument("--report", default=str(REPORT_DIR / "ifs_gold_reserve_needs_manual.csv"))
    args = parser.parse_args()

    if not args.ingest:
        parser.print_help()
        return

    rows, manual = build_vintages(Path(args.ifs_dir), Path(args.lm_csv))
    print(f"\n  parse: {len(rows)} dòng vintage ingest được, {len(manual)} NEEDS_MANUAL\n")
    if manual:
        print("  NEEDS_MANUAL (thiếu Last-Modified / parse lỗi / PIT vi phạm):")
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
