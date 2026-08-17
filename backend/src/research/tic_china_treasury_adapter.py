"""tic_china_treasury_adapter.py — Adapter ingest US Treasury TIC "Major Foreign
Holders" (MFH) holdings của China, Mainland → gold_h2_series.

LANE B (diagnostic) — KHÔNG PHẢI STRICT PIT:
  - Nguồn: https://www.treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.txt
    (full-history current-vintage, 2000→Dec 2025, đơn vị billions USD, holdings
    tại cuối kỳ). Đây là CURRENT VINTAGE: giá trị đã qua các revision hàng quý
    (Jan/Apr/Jul/Oct revise past year; các release khác prior 3 months).
  - Vì không có vintage ladder hồi tố (Wayback CDX IP-block 429; ALFRED chỉ lưu
    TIC từ 2026-05), publication_date là SYNTHETIC: obs + ~45 ngày theo release
    schedule TIC đã xác minh (data tháng T công bố ngày 15–18 của tháng T+2).
  - metadata gắn cứng: lane=B, not_strict_pit=True, synthetic_release_lag=True.
  - KHÔNG được gọi đây là historical PIT evidence. Chỉ trả lời câu hỏi hẹp:
    feature có latent signal đáng tiếp tục thu thập vintage hay không.

Series riêng, KHÔNG merge với GLOBAL / không đổi WGC/IFS:
  - series = TIC_CHINA_TREASURY_HOLDINGS, entity = CHINA_MAINLAND,
    unit = usd_billions, source = US_TREASURY_TIC.

Format mfhhis01.txt (đã xác minh qua parse):
  - Nhiều block năm. Mỗi block: month-label row (Dec..Jan) + year row
    ('Country\tYYYY×12') + country rows.
  - 2012+ : 12 cột (Dec..Jan cùng năm). 2011- : 13 cột (có tháng trùng, vd
    'Jun' xuất hiện 2 lần — bản revised; giống IFS, giữ cột index LỚN NHẤT).
  - China row: '"China, Mainland"\t<values>'. Đơn vị billions USD.

Usage (từ project root):
  python -X utf8 backend/src/research/tic_china_treasury_adapter.py --ingest --db backend/data/gold_h2.db
  python -X utf8 backend/src/research/tic_china_treasury_adapter.py --fetch --db ... --ingest
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import hashlib
import sys
from pathlib import Path


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
TIC_DIR = DATA_DIR / "cache" / "tic"
MFH_FILE = TIC_DIR / "mfhhis01.txt"
SOURCE_URL = "https://www.treasury.gov/resource-center/data-chart-center/tic/Documents/mfhhis01.txt"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

SERIES = "TIC_CHINA_TREASURY_HOLDINGS"
ENTITY = "CHINA_MAINLAND"
UNIT = "usd_billions"
SOURCE = "US_TREASURY_TIC"
LOOKUP = '"China, Mainland"'

# Chỉ ingest từ 2012-01: mfhhis01 trước 2012 dùng methodology survey-era (khác
# SLT, cross-check FRED FORTREASPOS41408 lệch tới 235B). 2012+ SLT-based khớp
# FRED (maxdiff ≤0.05). IS 2022-2024 nằm trong vùng SLT-consistent.
MIN_OBS = "2012-01-31"

# Synthetic release rule (LANE B): data tháng T công bố ~ngày 15-18 tháng T+2.
SYNTHETIC_RELEASE_DAY = 15
SYNTHETIC_RELEASE_MONTH_OFFSET = 2
METADATA_LANE_B = (
    "{'lane': 'B', 'not_strict_pit': true, 'synthetic_release_lag': true, "
    "'release_rule': 'day-15 month+2 (TIC monthly schedule)', 'vintage': 'current'}"
)

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_num(m: str) -> int:
    return _MONTHS.index(m) + 1


def synthetic_release_date(obs_ymd: str) -> str:
    """Synthetic publication = ngày 15 của (tháng obs + 2).

    LANE B duy nhất — KHÔNG phải publication thực (vintage unavailable).
    Ví dụ obs 2025-12-31 -> 2026-02-15 (khớp release schedule TIC).
    """
    dt = datetime.date.fromisoformat(obs_ymd)
    y, m = dt.year, dt.month + SYNTHETIC_RELEASE_MONTH_OFFSET
    if m > 12:
        y += 1
        m -= 12
    return f"{y:04d}-{m:02d}-{SYNTHETIC_RELEASE_DAY:02d}"


def parse_mfh_history(text: str) -> dict[str, float]:
    """Parse mfhhis01.txt → {observation_date (YYYY-MM-DD): holdings (billions)}.

    Duyệt từng block: label row (tháng theo cột) + year row ('Country\tYYYY×n').
    Country row China Mainland → map (year, month, col) → month-end observation.
    Cột trùng tháng (2011- trước, vd 'Jun' xuất hiện 2 lần) → giữ cột ĐẦU
    (index nhỏ) — xác nhận khớp FRED FORTREASPOS41408 (2011-06=1307.0=col6,
    2010-06=1112.1=col6, 2009-06=915.8=col6). Dòng trống / separator bỏ qua.
    Không hardcode số cột — dựa vào label row.
    """
    lines = text.splitlines()
    out: dict[str, float] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line.startswith("Country"):
            i += 1
            continue
        year_toks = [t.strip() for t in line.split("\t")]
        year = next((t for t in year_toks if t.isdigit() and len(t) == 4), None)
        if year is None:
            i += 1
            continue
        # label row ngay trên year row: tháng theo cột
        label_toks = [t.strip() for t in lines[i - 1].split("\t") if t.strip()]
        # month -> col (giữ cột ĐẦU nếu trùng — khớp FRED pre-2012 format)
        month_by_col: dict[int, str] = {}
        for ci, tok in enumerate(label_toks):
            if tok in _MONTHS and tok not in month_by_col.values():
                month_by_col[ci] = tok
        # duyệt country rows: dừng ở năm block kế tiếp (dòng bắt đầu 'Country').
        # Dòng trống / separator '----' bỏ qua (không break).
        j = i + 1
        while j < n:
            l2 = lines[j].strip()
            if l2.startswith("Country"):
                break
            if l2.startswith(LOOKUP):
                toks = [t.strip() for t in lines[j].split("\t")]
                for ci, mo in month_by_col.items():
                    if ci + 1 < len(toks):
                        raw = toks[ci + 1]
                        try:
                            val = float(raw)
                        except ValueError:
                            continue
                        obs_ymd = f"{year}-{_month_num(mo):02d}-{calendar.monthrange(int(year), _month_num(mo))[1]:02d}"
                        out[obs_ymd] = val
            j += 1
        i = j
    return out


def build_tic_rows(text: str) -> list[dict]:
    """mfhhis01.txt text → contract rows (LANE B synthetic publication).

    value = holdings (billions USD) tại observation month-end.
    publication_date = synthetic (day-15 month+2). Luôn >= observation.
    """
    hist = parse_mfh_history(text)
    rows = []
    for obs in sorted(hist):
        if obs < MIN_OBS:  # pre-2012 survey-era methodology — loại (không PIT-consistent với SLT)
            continue
        pub = synthetic_release_date(obs)
        if pub < obs:  # phòng hờ (không thể xảy ra với rule +2 tháng)
            continue
        rows.append(
            {
                "series": SERIES,
                "entity": ENTITY,
                "observation_date": obs,
                "publication_date": pub,
                "value": hist[obs],
                "unit": UNIT,
                "source": SOURCE,
                "provenance": (
                    f"US Treasury TIC MFH mfhhis01.txt (current vintage, URL {SOURCE_URL}); "
                    f"holdings end-of-period billions USD; SLT-era obs >= {MIN_OBS} only; "
                    f"LANE B synthetic pub=obs+~45d (day-15 month+2); "
                    f"NOT strict PIT (revision-contaminated vintage)"
                ),
                "metadata": METADATA_LANE_B,
            }
        )
    return rows


def fetch_mfh(dest: Path) -> str:
    """Tải mfhhis01.txt về cache, trả về nội dung + in sha256."""
    import urllib.request

    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    print(f"  [FETCH] {dest} {len(raw)} bytes sha256={sha}")
    return raw.decode("utf-8", errors="replace")


def _read_mfh(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"không có {path} — chạy --fetch trước")
    return path.read_text(encoding="utf-8", errors="replace")


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
            print(f"  [DRY] {r['observation_date']}  {r['value']:.1f}B  pub={r['publication_date']} (synthetic)")
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
    parser = argparse.ArgumentParser(description="US Treasury TIC MFH China holdings (LANE B) → gold_h2_series")
    parser.add_argument("--ingest", action="store_true", help="ingest từ file cache")
    parser.add_argument("--fetch", action="store_true", help="tải mfhhis01.txt mới về cache")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--dry-run", action="store_true", help="chỉ in, không ghi")
    parser.add_argument("--file", default=str(MFH_FILE))
    args = parser.parse_args()

    text = fetch_mfh(Path(args.file)) if args.fetch else _read_mfh(Path(args.file))
    rows = build_tic_rows(text)
    print(f"\n  parse: {len(rows)} observation (LANE B, synthetic pub) từ {Path(args.file).name}")
    print(f"  range: {rows[0]['observation_date']} .. {rows[-1]['observation_date']}" if rows else "  (empty)")
    if args.ingest:
        ingest(rows, args.db, args.dry_run)


if __name__ == "__main__":
    main()
