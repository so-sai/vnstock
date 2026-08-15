"""test_wgc_cb_audit.py — Unit tests cho publication-lag audit parser.

Chỉ test các hàm PARSE THUẦN với fixture HTML (không network, không cache).
Trọng tâm (bài học look-ahead):
  - publication_date phải được lấy THẬT từ HTML, không suy diễn.
  - post thiếu publication_date -> PIT-unsafe.
  - data cutoff / revision note được bắt đúng.
"""

from src.research.wgc_cb_audit import (
    _parse_date_str,
    parse_listing_posts,
    parse_post_metadata,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

LISTING_HTML = """
<html><body>
<a href="/goldhub/gold-focus/2026/08/central-bank-gold-statistics-june-2026">
  Central bank gold statistics: June 2026
</a>
<a href="/goldhub/gold-focus/2026/07/weekly-markets-monitor-x">Weekly Monitor</a>
<a href="/goldhub/gold-focus/2026/06/central-bank-gold-statistics-resume-buying-april">
  Central bank gold statistics: resume buying
</a>
<a href="/goldhub/gold-focus/2021/06/central-bank-gold-buying-gathers-steam">not cb stats</a>
</body></html>
"""

POST_HTML = """
<html><body>
<article>
  <section class="o-page-header">
    <div class="l-col-12"><h1>Central bank gold statistics: June 2026</h1></div>
    <div class="l-col-sm-7"><p>4 August, 2026</p></div>
  </section>
  <p>Central banks bought 51t of gold in June with Poland and China continuing to lead.</p>
  <figure>
    <img src="/x.png"/>
    <figcaption>*Data to June 2026 where available. Source: IMF IFS, respective central banks,
    World Gold Council</figcaption>
  </figure>
  <p>This blog post was updated on 12 June 2026 to correct an error in Chart 3.</p>
  <a href="/download/file/21016/CB_Infographic_June_2026.pdf">PDF</a>
</article>
</body></html>
"""

POST_HTML_NO_PUBDATE = """
<html><body>
<article>
  <section class="o-page-header">
    <div class="l-col-12"><h1>Central bank gold statistics: old post</h1></div>
  </section>
  <p>Some text without a date.</p>
</article>
</body></html>
"""


# ── 1. Listing parser ─────────────────────────────────────────────────────


def test_listing_extracts_only_cb_stats():
    posts = parse_listing_posts(LISTING_HTML)
    urls = [p["url"] for p in posts]
    assert len(posts) == 2
    assert any("/2026/08/central-bank-gold-statistics-june-2026" in u for u in urls)
    assert any("/2026/06/central-bank-gold-statistics-resume-buying-april" in u for u in urls)
    # không lấy weekly monitor / non-cb-stats
    assert not any("weekly-markets-monitor" in u for u in urls)
    assert not any("gold-buying-gathers-steam" in u for u in urls)


def test_listing_year_month_parsed():
    posts = parse_listing_posts(LISTING_HTML)
    june = next(p for p in posts if "june-2026" in p["url"])
    assert june["year"] == "2026"
    assert june["month"] == "08"


# ── 2. Date parser ────────────────────────────────────────────────────────


def test_parse_date_variants():
    assert _parse_date_str("4 August, 2026") == "2026-08-04"
    assert _parse_date_str("10 June, 2021") == "2021-06-10"
    assert _parse_date_str("30 November 2024") == "2024-11-30"
    assert _parse_date_str("1 January 2026") == "2026-01-01"


def test_parse_date_unknown_month_returns_none():
    # KHÔNG suy diễn: tháng lạ -> None
    assert _parse_date_str("4 Foop, 2026") is None


def test_parse_date_missing_year_returns_none():
    assert _parse_date_str("4 August") is None
    assert _parse_date_str("") is None


# ── 3. Post metadata parser ───────────────────────────────────────────────


def test_post_metadata_extracts_pub_date():
    meta = parse_post_metadata(POST_HTML, "https://x.test/post")
    assert meta["published_date"] == "2026-08-04"
    assert "June 2026" in meta["title"]


def test_post_metadata_extracts_cutoff():
    meta = parse_post_metadata(POST_HTML, "https://x.test/post")
    assert "Data to June 2026" in meta["cutoff_text"]


def test_post_metadata_extracts_revision_note():
    meta = parse_post_metadata(POST_HTML, "https://x.test/post")
    assert "updated on 12 June 2026" in meta["revision_note"]


def test_post_metadata_extracts_pdf():
    meta = parse_post_metadata(POST_HTML, "https://x.test/post")
    assert meta["pdf_url"].endswith("CB_Infographic_June_2026.pdf")


def test_post_without_pubdate_is_pit_unsafe_candidate():
    """post thiếu publication_date -> published_date None (KHÔNG suy diễn)."""
    meta = parse_post_metadata(POST_HTML_NO_PUBDATE, "https://x.test/post2")
    assert meta["published_date"] is None
    assert meta["cutoff_text"] == ""
