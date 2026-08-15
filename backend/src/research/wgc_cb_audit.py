"""wgc_cb_audit.py — Publication-lag audit cho WGC "Central bank gold statistics" blog series.

Trả lời câu hỏi PIT cốt lõi cho series CB monthly net purchases:
    "Tại ngày T, observation nào đã được thị trường biết?"

Đầu ra:
    backend/data/reports/wgc_cb_blog_audit.csv — danh sách post + publication date
    + data cutoff + revision flag + pdf. Đây là bước "publication-lag audit" trong
    Gold Gate 1.1, TRƯỚC khi viết adapter ingest.

Nguồn:
    - Listing:  https://www.gold.org/goldhub/gold-focus?topics%5B0%5D=Central%20banks&page=N
    - Post:     https://www.gold.org/goldhub/gold-focus/YYYY/MM/<slug>

Design (testable):
    - Các hàm parse thuần (nhận HTML string, không network) -> test với fixture.
    - `fetch_*` chỉ làm HTTP GET + trả HTML; logic parse tách rời.
    - Cache HTML thô vào backend/data/cache/wgc_cb/ để repro/resume + không spam
      upstream khi chạy lại audit.
    - Zero-hallucination: nếu không parse được publication_date / cutoff -> để trống
      (KHÔNG suy diễn), post đó bị đánh dấu PIT-unsafe.

Usage (từ project root):
  python -X utf8 backend/src/research/wgc_cb_audit.py --crawl --max-pages 40
  python -X utf8 backend/src/research/wgc_cb_audit.py --report --out backend/data/reports/wgc_cb_blog_audit.csv
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests
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

HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36")}

LISTING_URL = "https://www.gold.org/goldhub/gold-focus"
POST_BASE = "https://www.gold.org"

# Pattern nhận diện bài "Central bank gold statistics" trong listing
CB_POST_RE = re.compile(r"/goldhub/gold-focus/(20\d\d)/(\d\d)/.*central-bank-gold-statistics")

# Dùng cho publication date trong post (dạng "4 August, 2026")
_MONTHS = {
    m: i
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


def parse_listing_posts(html: str) -> list[dict]:
    """Trích từ listing page các bài CB stats: {url, title, month, year}."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[href^="/goldhub/gold-focus/"]'):
        href = a.get("href", "")
        m = CB_POST_RE.search(href)
        if not m:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = " ".join(a.get_text(" ", strip=True).split())
        out.append(
            {
                "url": POST_BASE + href,
                "title": title,
                "year": m.group(1),
                "month": m.group(2),
            }
        )
    return out


def _parse_date_str(raw: str) -> str | None:
    """Parse '4 August, 2026' -> '2026-08-04'. Không suy diễn; None nếu lạ."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", raw)
    if not m:
        return None
    day, mon, yr = m.group(1), m.group(2), m.group(3)
    if mon not in _MONTHS:
        return None
    return f"{yr}-{_MONTHS[mon]:02d}-{int(day):02d}"


def parse_post_metadata(html: str, url: str) -> dict:
    """Parse metadata một bài CB stats post.

    Trả về dict có các key cố định. publication_date / data_cutoff có thể None
    (KHÔNG suy diễn) -> post đó PIT-unsafe.
    """
    soup = BeautifulSoup(html, "lxml")

    title = soup.select_one("h1")
    title_text = " ".join(title.get_text(" ", strip=True).split()) if title else ""

    # publication date: nằm trong `.o-page-header` (dạng "4 August, 2026")
    pub_raw = ""
    header = soup.select_one(".o-page-header")
    if header:
        p = header.find("p")
        if p:
            pub_raw = p.get_text(strip=True)
    published_date = _parse_date_str(pub_raw)

    # Toàn bộ text bài để tìm data cutoff / revision / figure
    body_text = " ".join(soup.select_one("article").get_text(" ", strip=True).split())

    # Data cutoff: figure caption dạng "*Data to June 2026 where available" /
    # "*Data to 29 May 2026, where available" (thường trong figcaption)
    cutoff_text = ""
    for cap in soup.select("figcaption"):
        t = " ".join(cap.get_text(" ", strip=True).split())
        if re.search(r"data to", t, re.IGNORECASE):
            cutoff_text = t
            break
    if not cutoff_text:
        m = re.search(r"\*?Data to ([^.]*?) where available", body_text, re.IGNORECASE)
        if m:
            cutoff_text = "Data to " + m.group(1).strip() + " where available"

    # Revision note: "This blog post was updated on ... to correct ..."
    revision_note = ""
    m = re.search(r"(This blog post was updated on [^.]*\.)", body_text, re.IGNORECASE)
    if m:
        revision_note = m.group(1).strip()

    # PDF infographic download (nếu có)
    pdf_url = ""
    pdf_link = soup.select_one('a[href*="/download/file/"]')
    if pdf_link:
        href = pdf_link.get("href", "")
        if href.lower().endswith(".pdf"):
            pdf_url = POST_BASE + href

    # Ghi chú phương pháp quan trọng: "consist solely of publicly reported changes"
    # hoặc "estimate for unreported buying" — phân biệt declared vs estimated.
    estimate_note = ""
    if re.search(r"estimate for unreported buying", body_text, re.IGNORECASE):
        estimate_note = "GDT_statistics_include_unreported_estimate"
    if re.search(r"consist solely of publicly reported changes", body_text, re.IGNORECASE):
        estimate_note = "publicly_reported_only"

    return {
        "url": url,
        "title": title_text,
        "published_date": published_date,
        "pub_raw": pub_raw,
        "cutoff_text": cutoff_text,
        "revision_note": revision_note,
        "pdf_url": pdf_url,
        "estimate_note": estimate_note,
    }


def fetch_html(url: str, timeout: int = 30) -> str | None:
    """HTTP GET -> HTML. None khi fail (lỗi mạng / status != 200)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.text


def cache_path(url: str) -> Path:
    """Slug an toàn cho Windows: thay ký tự không hợp lệ trong tên file."""
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^A-Za-z0-9_\-]", "_", slug)
    if len(slug) > 120:
        slug = slug[:60] + "_" + slug[-40:]
    return CACHE_DIR / f"{slug}.html"


def get_html_cached(url: str, use_cache: bool = True) -> str | None:
    """Fetch với cache theo slug. Cache giúp repro + không spam upstream."""
    if use_cache:
        cp = cache_path(url)
        if cp.exists():
            return cp.read_text(encoding="utf-8")
    html = fetch_html(url)
    if html is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path(url).write_text(html, encoding="utf-8")
    return html


def crawl_listing(max_pages: int, use_cache: bool = True, sleep_s: float = 0.3) -> list[dict]:
    """Duyệt N trang listing, gom các post CB stats.

    KHÔNG break sớm khi một trang trống (listing không đảm bảo post CB stats
    phân bố đều theo trang) — duyệt đủ max_pages để xác định coverage thật.
    """
    posts: dict[str, dict] = {}
    for page in range(max_pages):
        url = f"{LISTING_URL}?topics%5B0%5D=Central%20banks&page={page}"
        html = get_html_cached(url, use_cache=use_cache)
        if html is None:
            print(f"  [warn] page {page} fetch fail — bỏ qua (tiếp tục)")
            continue
        found = parse_listing_posts(html)
        for p in found:
            posts[p["url"]] = p
        print(f"  page {page:2d}: +{len(found)} post (tổng {len(posts)})")
        if sleep_s:
            time.sleep(sleep_s)
    return list(posts.values())


def audit_posts(posts: list[dict], use_cache: bool = True, sleep_s: float = 0.3) -> list[dict]:
    """Fetch từng post + parse metadata. Thêm cột PIT-safe."""
    rows: list[dict] = []
    for p in posts:
        html = get_html_cached(p["url"], use_cache=use_cache)
        if html is None:
            rows.append(
                {
                    **p,
                    "published_date": None,
                    "cutoff_text": "",
                    "revision_note": "FETCH_FAIL",
                    "pdf_url": "",
                    "estimate_note": "",
                }
            )
            continue
        meta = parse_post_metadata(html, p["url"])
        rows.append({**p, **meta})
        if sleep_s:
            time.sleep(sleep_s)
    # PIT-safe: có publication_date thực (không suy diễn) là điều kiện cần
    for r in rows:
        r["pit_safe"] = bool(r.get("published_date"))
    return rows


def to_csv(rows: list[dict], out_path: Path) -> None:
    cols = [
        "url",
        "title",
        "published_date",
        "pub_raw",
        "cutoff_text",
        "revision_note",
        "pdf_url",
        "estimate_note",
        "pit_safe",
        "year",
        "month",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        import csv

        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\n  [OK] audit CSV -> {out_path} ({len(rows)} rows)")


def summarize(rows: list[dict]) -> None:
    safe = [r for r in rows if r.get("pit_safe")]
    unsafe = [r for r in rows if not r.get("pit_safe")]
    with_rev = [r for r in safe if r.get("revision_note")]
    with_cutoff = [r for r in safe if r.get("cutoff_text")]
    print("\n  SUMMARY — WGC CB blog audit")
    print(f"    tổng post CB stats : {len(rows)}")
    print(f"    PIT-safe (có pub)  : {len(safe)}")
    print(f"    PIT-unsafe         : {len(unsafe)}")
    print(f"    có data cutoff     : {len(with_cutoff)}")
    print(f"    có revision note   : {len(with_rev)}")
    if unsafe:
        print("\n    PIT-unsafe posts:")
        for r in unsafe:
            print(f"      - {r['url']}")
    if with_rev:
        print("\n    Posts bị sửa sau phát hành (revision):")
        for r in with_rev:
            print(f"      - {r['published_date']}  {r['title'][:60]}")
            print(f"          {r['revision_note']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="WGC CB blog publication-lag audit")
    parser.add_argument("--crawl", action="store_true", help="crawl listing + audit posts")
    parser.add_argument("--report", action="store_true", help="chỉ đọc cache hiện có -> CSV")
    parser.add_argument("--max-pages", type=int, default=40, help="số trang listing (mặc định 40)")
    parser.add_argument("--no-cache", action="store_true", help="bỏ qua cache, fetch mới")
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--out", default=str(REPORT_DIR / "wgc_cb_blog_audit.csv"))
    args = parser.parse_args()

    use_cache = not args.no_cache
    if args.report and not args.crawl:
        # Rebuild từ cache HTML hiện có
        cached = sorted(CACHE_DIR.glob("*.html")) if CACHE_DIR.exists() else []
        posts = []
        for cp in cached:
            url = None
            for p in parse_listing_posts(cp.read_text(encoding="utf-8")):
                url = p["url"]
            if url:
                posts.append({"url": url, "title": "", "year": "", "month": ""})
        rows = audit_posts(posts, use_cache=True, sleep_s=0)
        to_csv(rows, Path(args.out))
        summarize(rows)
        return

    if args.crawl:
        posts = crawl_listing(args.max_pages, use_cache=use_cache, sleep_s=args.sleep)
        if not posts:
            print("  [FAIL] không lấy được post nào từ listing")
            sys.exit(1)
        rows = audit_posts(posts, use_cache=use_cache, sleep_s=args.sleep)
        to_csv(rows, Path(args.out))
        summarize(rows)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
