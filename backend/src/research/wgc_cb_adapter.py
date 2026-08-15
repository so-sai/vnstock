"""wgc_cb_adapter.py — Adapter ingest WGC "Central bank gold statistics" → gold_h2_series.

Đọc cache HTML (từ wgc_cb_audit.py), parse observation month + net CB purchases
figure (global, tonnes), ingest theo contract H2 (publication_date = ngày publish
post; observation_date = cuối tháng observation).

Nguồn figure (zero-hallucination):
    - Ưu tiên BODY text (câu summary đầu bài) — nội dung chính thức của post.
    - Fallback meta description (một số post infographic-only).
    - KHÔNG suy diễn: post không parse được figure / observation month → bỏ qua
      và đưa vào report NEEDS_MANUAL (con người bổ sung từ PDF/IFS).

Sign: net selling / sold = ÂM; net buying / net purchases / bought = DƯƠNG.

Ràng buộc observation year (KHÔNG đoán):
    1. Data cutoff ("Data to 30 November 2025") -> month + year.
    2. Title có "June 2026" (tháng + năm) -> dùng luôn.
    3. Không có -> NEEDS_MANUAL (không suy diễn từ publish date).

Usage (từ project root):
  python -X utf8 backend/src/research/wgc_cb_adapter.py --ingest --db backend/data/gold_h2.db
  python -X utf8 backend/src/research/wgc_cb_adapter.py --ingest --db x.db --dry-run
"""

from __future__ import annotations

import argparse
import calendar
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup


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
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
CACHE_DIR = DATA_DIR / "cache" / "wgc_cb"
REPORT_DIR = DATA_DIR / "reports"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

SERIES = "CB_GOLD_PURCHASES"
ENTITY = "GLOBAL"
UNIT = "tonnes"
SOURCE = "WGC_CB_BLOG"

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

# Pattern figure (body trước, meta fallback). Ordered: specific → generic.
# Mỗi pattern: (regex, sign) với sign = 1 dương, -1 âm.
FIGURE_PATTERNS = [
    (re.compile(r"reported\s+([\d.]+)\s*t\.?\s*of\s+net\s+selling", re.I), -1),
    (re.compile(r"increased by a net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"rose by a net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"added a net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"bought a net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"bought net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"having bought a net\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"having bought\s+([\d.]+)\s*t", re.I), 1),
    (
        re.compile(
            r"net\s+(?:purchases|buying)\s+(?:for the month of\s+)?"
            r"(?:came in at|total(?:l)?ed|were|was)\s+([\d.]+)\s*t",
            re.I,
        ),
        1,
    ),
    (re.compile(r"net\s+purchases?\s+total(?:l)?ed\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"total(?:l)?ing\s+([\d.]+)\s*t", re.I), 1),
    (re.compile(r"reported\s+([\d.]+)\s*t\.?\s*of\s+net\s+(?:purchases|buying)", re.I), 1),
    (re.compile(r"reported\s+([\d.]+)\s*t\.?\s*of\s+net\s+(?:sales|selling)", re.I), -1),
    (re.compile(r"sold\s+([\d.]+)\s*t\.?\s*of\s+gold", re.I), -1),
    (re.compile(r"sold\s+([\d.]+)\s*t\b", re.I), -1),
    (re.compile(r"bought\s+([\d.]+)\s*t\.?\s*of\s+gold", re.I), 1),
]

# Observation month trong title: "June 2026" | "...December 2024" | "...in April"
_TITLE_MONTH_YEAR = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")
_TITLE_MONTH_ONLY = re.compile(r"\b(?:in|into|for|of)\s+([A-Z][a-z]+)\b")
_CUTOFF_YEAR = re.compile(r"\b(\d{4})\b")
# meta/body: "in May", "during the month of April"
_TEXT_MONTH_ONLY = re.compile(r"\b(?:in|during)\s+(?:the month of\s+)?([A-Z][a-z]+)\b")


def _month_num(name: str) -> int | None:
    return _MONTHS.get(name.lower())


def month_end(ym: str) -> str:
    """'2026-06' -> '2026-06-30'."""
    y, m = (int(x) for x in ym.split("-"))
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def _title_month_year(title: str) -> str | None:
    """title chứa 'Month YYYY' -> 'YYYY-MM'."""
    m = _TITLE_MONTH_YEAR.search(title)
    if not m:
        return None
    mon = _month_num(m.group(1))
    if not mon or not (2000 <= int(m.group(2)) <= 2100):
        return None
    return f"{m.group(2)}-{mon:02d}"


def _title_month_only(title: str) -> str | None:
    """title chứa 'in/into/for/of Month' (không năm) -> month name."""
    m = _TITLE_MONTH_ONLY.search(title)
    return m.group(1) if m and _month_num(m.group(1)) else None


def _text_month(meta_or_body: str) -> str | None:
    """meta/body: 'in May' -> month name (fallback khi title không có tháng)."""
    m = _TEXT_MONTH_ONLY.search(meta_or_body)
    return m.group(1) if m and _month_num(m.group(1)) else None


def extract_observation_month(title: str, cutoff_text: str, meta_text: str = "", body_text: str = "") -> str | None:
    """Trả về 'YYYY-MM' observation. KHÔNG suy diễn năm.

    Tháng lấy từ TITLE (nguồn chính); nếu title không có tháng -> meta/body.
    Năm lấy từ title ('Month YYYY') nếu có, ngược lại từ cutoff_text
    ('Data to ... YYYY'). Caption cutoff KHÔNG dùng làm tháng (post về tháng X
    thường có caption 'Data to' trễ hơn — vd post May 2026 có caption June 2026).
    Không có tháng hoặc không có năm -> None (NEEDS_MANUAL).
    """
    my = _title_month_year(title)
    if my:
        return my

    month_name = _title_month_only(title) or _text_month(meta_text) or _text_month(body_text)
    if not month_name:
        return None
    mon = _month_num(month_name)

    y = None
    m = _CUTOFF_YEAR.search(cutoff_text)
    if m:
        y = m.group(1)
    if not y or not (2000 <= int(y) <= 2100):
        return None
    return f"{y}-{mon:02d}"


def _first_figure(text: str) -> tuple[float, str] | None:
    """Tìm pattern figure đầu tiên trong text. Trả (value, matched_raw)."""
    for rx, sign in FIGURE_PATTERNS:
        m = rx.search(text)
        if m:
            try:
                val = float(m.group(1)) * sign
            except ValueError, IndexError:
                continue
            return val, m.group(0)
    return None


def extract_figure(body_text: str, meta_desc: str) -> tuple[float, str, str] | None:
    """Figure global net CB purchases. Ưu tiên body; fallback meta.

    Trả (value, figure_raw, source_tag). None nếu không parse được.

    Nếu cả body và meta đều parse được nhưng KHÁC NHAU -> vẫn dùng body
    (nguồn chính), ghi chú discrepancy trong figure_raw để provenance thấy.
    """
    b_hit = _first_figure(body_text)
    m_hit = _first_figure(meta_desc)
    if b_hit:
        if m_hit and m_hit[0] != b_hit[0]:
            raw = f"{b_hit[1]} [META_DIFFERS:{m_hit[1]}]"
            return b_hit[0], raw, "body"
        return b_hit[0], b_hit[1], "body"
    if m_hit:
        return m_hit[0], m_hit[1], "meta"
    return None


def parse_post(html: str, url: str) -> dict | None:
    """Parse một post → dòng contract H2 hoặc None (thiếu dữ liệu PIT).

    Nếu thiếu observation month / figure / publication date -> None (bị loại,
    sẽ được báo cáo NEEDS_MANUAL ở mức cao hơn).
    """
    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one("h1")
    title = " ".join(title_el.get_text(" ", strip=True).split()) if title_el else ""

    pub_raw = ""
    header = soup.select_one(".o-page-header")
    if header and header.find("p"):
        pub_raw = header.find("p").get_text(strip=True)
    m_pub = re.search(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", pub_raw)
    if not m_pub or _month_num(m_pub.group(2)) is None:
        return None  # thiếu publication date -> không PIT
    published_date = f"{m_pub.group(3)}-{_month_num(m_pub.group(2)):02d}-{int(m_pub.group(1)):02d}"

    article = soup.select_one("article")
    body_text = " ".join(article.get_text(" ", strip=True).split()) if article else ""

    cutoff_text = ""
    for cap in soup.select("figcaption"):
        t = " ".join(cap.get_text(" ", strip=True).split())
        if re.search(r"data to", t, re.I):
            cutoff_text = t
            break
    if not cutoff_text:
        m = re.search(r"\*?Data to ([^.]*?) where available", body_text, re.I)
        if m:
            cutoff_text = "Data to " + m.group(1).strip() + " where available"

    md = soup.find("meta", attrs={"name": "description"})
    meta_desc = md.get("content", "") if md else ""

    obs_ym = extract_observation_month(title, cutoff_text, meta_desc, body_text)
    if not obs_ym:
        return None
    observation_date = month_end(obs_ym)

    # PIT invariant: observation không thể sau publication.
    if observation_date > published_date:
        return None

    fig = extract_figure(body_text, meta_desc)
    if not fig:
        return None
    value, figure_raw, fig_src = fig

    return {
        "series": SERIES,
        "entity": ENTITY,
        "observation_date": observation_date,
        "publication_date": published_date,
        "value": value,
        "unit": UNIT,
        "source": SOURCE,
        "provenance": (
            f"WGC gold-focus post {url}; figure_from={fig_src}; "
            f"raw='{figure_raw}'; cutoff='{cutoff_text}'; "
            f"pub_raw='{pub_raw}'"
        ),
        "metadata": f"{{'title': {title!r}}}",
    }


def parse_all_from_cache(cache_dir: Path) -> tuple[list[dict], list[dict]]:
    """Duyệt cache HTML -> (rows contract, needs_manual). Không network."""
    rows: list[dict] = []
    manual: list[dict] = []
    for cp in sorted(cache_dir.glob("*.html")):
        if "central-bank-gold-statistics" not in cp.name:
            continue
        url = "https://www.gold.org/goldhub/gold-focus/" + cp.stem
        html = cp.read_text(encoding="utf-8")
        row = parse_post(html, url)
        if row:
            rows.append(row)
        else:
            manual.append({"url": url, "file": cp.name})
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
    parser = argparse.ArgumentParser(description="WGC CB blog → gold_h2_series adapter")
    parser.add_argument("--ingest", action="store_true", help="ingest từ cache HTML")
    parser.add_argument("--db", default=str(DATA_DIR / "gold_h2.db"))
    parser.add_argument("--dry-run", action="store_true", help="chỉ in, không ghi")
    parser.add_argument("--cache", default=str(CACHE_DIR))
    parser.add_argument("--report", default=str(REPORT_DIR / "wgc_cb_adapter_needs_manual.csv"))
    args = parser.parse_args()

    if not args.ingest:
        parser.print_help()
        return

    cache_dir = Path(args.cache)
    rows, manual = parse_all_from_cache(cache_dir)
    print(f"\n  parse: {len(rows)} dòng ingest được, {len(manual)} NEEDS_MANUAL\n")
    if manual:
        print("  NEEDS_MANUAL (thiếu figure/observation month/pub date):")
        for m in manual:
            print(f"    - {m['url']}")
        import csv

        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["url", "file"])
            w.writeheader()
            for m in manual:
                w.writerow(m)
        print(f"\n  [OK] NEEDS_MANUAL report -> {args.report}")

    if rows:
        ingest(rows, args.db, args.dry_run)


if __name__ == "__main__":
    main()
