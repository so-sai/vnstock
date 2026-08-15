"""test_wgc_cb_adapter.py — Test adapter WGC CB blog → gold_h2_series.

Dùng fixture HTML (không network) để kiểm tra:
  - observation month/year: từ title "Month YYYY" | title "in Month" + cutoff năm
    | meta/body "in Month" khi title không có tháng.
  - figure: body ưu tiên; meta fallback (infographic-only); sign âm cho net selling/sold;
    META_DIFFERS ghi chú khi body khác meta.
  - PIT invariant: observation_date <= publication_date.
  - NEEDS_MANUAL: thiếu figure/observation month → None.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.wgc_cb_adapter import (
    extract_figure,
    extract_observation_month,
    month_end,
    parse_post,
)

# ── Fixture HTML ────────────────────────────────────────────────────────────


def _post_html(
    title: str,
    pub_raw: str,
    body: str = "",
    meta: str = "",
    figcaption: str = "",
    include_article: bool = True,
) -> str:
    art = ""
    if include_article:
        art = f"<article>{body}</article>"
    cap = f"<figcaption>{figcaption}</figcaption>" if figcaption else ""
    return (
        "<html><head>"
        f'<meta name="description" content="{meta}"/>'
        f"</head><body>"
        f'<h1>{title}</h1>'
        f'<div class="o-page-header"><p>{pub_raw}</p></div>'
        f"{cap}{art}</body></html>"
    )


BODY_DEC23 = (
    "<div class=\"wgc-text\"><p>Reported global central bank gold reserves, via the IMF "
    "and publicly available sources, rose by a net 28t in December. Higher gross sales "
    "(12t) were outweighed by gross purchases (41t), highlighting the continued "
    "strength of buying.*</p></div>"
)
META_DEC23 = (
    "Reported global central bank gold reserves rose by a net 39t in December. "
    "Gross sales (2t) were again dwarfed by gross purchases (41t).*"
)

BODY_APR26 = (
    "<div class=\"wgc-text\"><p>Central banks resumed net gold purchases in April, "
    "having bought 19t. This was a rebound from the sizeable net sales reported in "
    "March.</p></div>"
)
META_APR26 = (
    "Central banks resumed net gold purchases in April, having bought 17t. "
    "Poland remained the top buyer in the month (14t)."
)

BODY_INFOG_ONLY = "<div class=\"wgc-text\"></div>"


# ── observation month ───────────────────────────────────────────────────────


def test_month_title_has_year():
    assert extract_observation_month("Central bank gold statistics: December 2025", "") == "2025-12"


def test_month_title_only_uses_cutoff_year():
    # title có "in April" (không năm) → năm lấy từ cutoff text.
    got = extract_observation_month(
        "Central banks resume net buying in April", "*Data to 29 May 2026, where available"
    )
    assert got == "2026-04"


def test_month_from_meta_when_title_has_no_month():
    # post "remain committed to gold" — title không tháng; meta nói "in May".
    got = extract_observation_month(
        "Central bank gold statistics: Central banks remain committed to gold",
        "*Data to 30 June 2026, where available",
        meta_text="Central banks were back in buying mode in May.",
    )
    assert got == "2026-05"


def test_month_missing_returns_none():
    assert extract_observation_month("Central bank gold statistics: Remain committed", "") is None


def test_cutoff_caption_not_used_as_month():
    # post April có cutoff "Data to 29 May 2026" → KHÔNG được lấy observation = May.
    got = extract_observation_month(
        "Central banks resume net buying in April", "*Data to 29 May 2026, where available"
    )
    assert got == "2026-04"


def test_month_end():
    assert month_end("2026-06") == "2026-06-30"
    assert month_end("2024-02") == "2024-02-29"  # leap year
    assert month_end("2026-02") == "2026-02-28"


# ── figure ──────────────────────────────────────────────────────────────────


def test_figure_body_preferred_with_meta_differs_note():
    got = extract_figure(BODY_DEC23, META_DEC23)
    assert got is not None
    val, raw, src = got
    assert val == 28.0
    assert src == "body"
    assert "META_DIFFERS" in raw
    assert "39t" in raw


def test_figure_body_alone():
    got = extract_figure("<p>Central banks reported 12t of net buying in June.</p>", "")
    assert got == (12.0, "reported 12t of net buying", "body")


def test_figure_meta_fallback_for_infographic():
    got = extract_figure(BODY_INFOG_ONLY, "Central banks reported 22t of net purchases in June.")
    assert got == (22.0, "reported 22t of net purchases", "meta")


def test_figure_net_selling_negative():
    got = extract_figure("Central banks reported 3t of net selling in December.", "")
    assert got is not None
    assert got[0] == -3.0


def test_figure_sold_negative():
    got = extract_figure("Central banks sold 30t of gold in March.", "")
    assert got is not None
    assert got[0] == -30.0


def test_figure_net_buying_positive():
    got = extract_figure("Central banks reported 40t of net buying in September.", "")
    assert got[0] == 40.0


def test_figure_totalling():
    got = extract_figure("Central bank demand for gold remained robust in October, totalling 53t.", "")
    assert got == (53.0, "totalling 53t", "body")


def test_figure_none_when_missing():
    assert extract_figure(BODY_INFOG_ONLY, "Central banks continue momentum of gold purchases.") is None


# ── parse_post ──────────────────────────────────────────────────────────────


def test_parse_post_full():
    html = _post_html(
        "Central Bank Gold Statistics - December 2023",
        "2 February, 2024",
        body=BODY_DEC23,
        meta=META_DEC23,
    )
    r = parse_post(html, "https://www.gold.org/goldhub/gold-focus/dec-2023")
    assert r is not None
    assert r["series"] == "CB_GOLD_PURCHASES"
    assert r["entity"] == "GLOBAL"
    assert r["observation_date"] == "2023-12-31"
    assert r["publication_date"] == "2024-02-02"
    assert r["value"] == 28.0
    assert r["unit"] == "tonnes"
    assert r["source"] == "WGC_CB_BLOG"
    assert "META_DIFFERS" in r["provenance"]


def test_parse_post_title_month_only_with_cutoff_year():
    html = _post_html(
        "Central banks resume net buying in April",
        "3 June, 2026",
        body=BODY_APR26,
        meta=META_APR26,
        figcaption="*Data to 29 May 2026, where available.",
    )
    r = parse_post(html, "u")
    assert r is not None
    assert r["observation_date"] == "2026-04-30"
    assert r["value"] == 19.0
    assert "META_DIFFERS" in r["provenance"]


def test_parse_post_meta_month_when_title_has_none():
    html = _post_html(
        "Central bank gold statistics: Central banks remain committed to gold",
        "2 July, 2026",
        body="<div class=\"wgc-text\"><p>Based on the latest reported data, official gold "
        "reserves increased by a net 41t during the month.</p></div>",
        meta="Central banks were back in buying mode in May.",
        figcaption="*Data to 30 June 2026, where available.",
    )
    r = parse_post(html, "u")
    assert r is not None
    assert r["observation_date"] == "2026-05-31"
    assert r["value"] == 41.0


def test_parse_post_infographic_only_meta_figure():
    html = _post_html(
        "Central bank gold statistics: June 2025",
        "5 August, 2025",
        body=BODY_INFOG_ONLY,
        meta="Central banks reported 22t of net purchases in June via the IMF.",
    )
    r = parse_post(html, "u")
    assert r is not None
    assert r["observation_date"] == "2025-06-30"
    assert r["value"] == 22.0


def test_parse_post_negative_sold():
    html = _post_html(
        "Central bank gold statistics: March 2026",
        "5 May, 2026",
        body="<div class=\"wgc-text\"><p>Central banks sold 30t of gold in March.</p></div>",
    )
    r = parse_post(html, "u")
    assert r is not None
    assert r["value"] == -30.0


def test_parse_post_needs_manual_no_figure():
    html = _post_html(
        "Central bank gold statistics March 2025",
        "2 May, 2025",
        body=BODY_INFOG_ONLY,
        meta="Central banks continue momentum of gold purchases into March 2025.",
    )
    assert parse_post(html, "u") is None


def test_parse_post_no_publication_date():
    html = _post_html(
        "Central bank gold statistics: December 2025",
        "no date here",
        body="<p>Central banks bought 19t of gold.</p>",
    )
    assert parse_post(html, "u") is None


def test_parse_post_pit_observation_after_publication_rejected():
    # obs > pub (không hợp lệ PIT) → None
    html = _post_html(
        "Central bank gold statistics: June 2026",
        "2 May, 2026",
        body="<div class=\"wgc-text\"><p>Central banks bought 51t of gold in June.</p></div>",
    )
    assert parse_post(html, "u") is None
