"""test_tic_china_treasury_adapter.py — Test adapter TIC MFH China (LANE B).

Adversarial cases (execution contract B2):
  - parse_mfh_history: block nhiều năm, 12 cột (2012+) và 13 cột (2011-, tháng
    trùng 'Jun' → giữ cột ĐẦU khớp FRED), dòng trống/separator bỏ qua.
  - synthetic release: pub = obs + ~45 ngày (day-15 month+2), luôn >= obs.
  - metadata: lane=B, not_strict_pit=True, synthetic_release_lag=True.
  - provenance: ghi rõ current-vintage, NOT strict PIT (không gọi là PIT evidence).
  - MIN_OBS filter: pre-2012 survey-era bị loại.
  - series/entity riêng (không đụng GLOBAL).

Không network — build input text inline.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.tic_china_treasury_adapter import (
    ENTITY,
    LOOKUP,
    MIN_OBS,
    SERIES,
    SOURCE,
    build_tic_rows,
    parse_mfh_history,
    synthetic_release_date,
)

_TXT = """\t\t\t\tMAJOR FOREIGN HOLDERS OF TREASURY SECURITIES
\t\t\t\t    (in billions of dollars)
\tDec\tNov\tOct\tSep\tAug\tJul\tJun\tMay\tApr\tMar\tFeb\tJan\t
Country\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t2025\t
\t------\t------\t------\t------\t------\t------\t------\t------\t------\t------\t------\t------\t
\t\t\t\t\t\t\t\t\t\t\t\t\t\t
Japan\t1185.5\t1202.7\t1200\t1189.3\t1183.9\t1155.4\t1154.8\t1142.8\t1140.9\t1135.1\t1130.3\t1079.3\t\t
"China, Mainland"\t684.4\t683.9\t687.7\t699.2\t699.7\t695.6\t731.4\t732.7\t743.6\t765.4\t784.3\t760.8\t
\tDec\tNov\tOct\tSep\tAug\tJul\tJun\tMay\tApr\tMar\tFeb\tJan\t
Country\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t2024\t
"China, Mainland"\t759\t768.6\t760.1\t772\t774.6\t776.5\t780.2\t768.3\t770.7\t767.4\t775\t797.7\t\t
\tDec\tNov\tOct\tSep\tAug\tJul\tJun\tJun\tMay\tApr\tMar\tFeb\tJan\t
Country\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t2011\t
"China, Mainland"\t1151.9\t1254.6\t1256.0\t1270.2\t1278.5\t1314.9\t1307.0\t1165.5\t1159.8\t1152.5\t1144.9\t1154.1\t1154.7\t
\tDec\tNov\tOct\tSep\tAug\tJul\tJun\tJun\tMay\tApr\tMar\tFeb\tJan\t
Country\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t2003\t
"China, Mainland"\t159.0\t154.2\t151.5\t147.1\t148.8\t151.3\t147.1\t137.2\t135.8\t134.0\t133.2\t121.8\t120.7\t
"""


def test_parse_multiple_year_blocks():
    hist = parse_mfh_history(_TXT)
    assert hist["2025-12-31"] == 684.4
    assert hist["2024-12-31"] == 759.0
    assert len(hist) == 48  # 12×4 năm (2025, 2024, 2011, 2003)


def test_parse_13col_duplicate_jun_first_col():
    """2011 block có 13 cột, 'Jun' lặp → giữ cột ĐẦU (1307.0, khớp FRED)."""
    hist = parse_mfh_history(_TXT)
    assert hist["2011-06-30"] == 1307.0  # cột Jun đầu, không phải 1165.5
    assert hist["2011-12-31"] == 1151.9
    assert hist["2011-01-31"] == 1154.7


def test_parse_skips_separator_and_blank():
    """Dòng '------' và dòng trống giữa block không gây break."""
    hist = parse_mfh_history(_TXT)
    assert hist["2025-06-30"] == 731.4  # giữa block có separator + blank


def test_synthetic_release_lag():
    assert synthetic_release_date("2025-12-31") == "2026-02-15"
    assert synthetic_release_date("2025-01-31") == "2025-03-15"
    assert synthetic_release_date("2024-11-30") == "2025-01-15"
    # luôn >= observation
    assert synthetic_release_date("2025-12-31") >= "2025-12-31"


def test_build_tic_rows_contract():
    rows = build_tic_rows(_TXT)
    assert rows
    for r in rows:
        assert r["series"] == SERIES
        assert r["entity"] == ENTITY
        assert r["source"] == SOURCE
        assert r["unit"] == "usd_billions"
        assert r["publication_date"] >= r["observation_date"]


def test_build_tic_rows_lane_b_metadata():
    rows = build_tic_rows(_TXT)
    for r in rows:
        assert "lane" in r["metadata"] and "'B'" in r["metadata"]
        assert "not_strict_pit" in r["metadata"] and "true" in r["metadata"]
        assert "synthetic_release_lag" in r["metadata"] and "true" in r["metadata"]
        assert "NOT strict PIT" in r["provenance"]


def test_build_tic_rows_min_obs_filter():
    """Pre-2012 (survey-era) bị loại — chỉ giữ obs >= MIN_OBS."""
    rows = build_tic_rows(_TXT)
    obs_all = {r["observation_date"] for r in rows}
    assert all(o >= MIN_OBS for o in obs_all)
    assert not any(o.startswith("2011") for o in obs_all)
    # nhưng 2011 block vẫn parse được nội bộ (parse không filter)
    assert "2011-06-30" in parse_mfh_history(_TXT)


def test_build_tic_rows_series_distinct():
    """Series/entity riêng — không đụng IFS/GLOBAL/WGC."""
    rows = build_tic_rows(_TXT)
    assert SERIES != "IFS_GOLD_RESERVE_CHANGE"
    assert SERIES != "CB_GOLD_PURCHASES"
    assert ENTITY != "GLOBAL"
    assert all(r["entity"] == ENTITY for r in rows)


def test_parse_no_country_no_crash():
    assert parse_mfh_history("no data here") == {}


def test_lookup_matches_china_row():
    """LOOKUP khớp đúng dòng '"China, Mainland"' — không đụng Japan/UK."""
    hist = parse_mfh_history(_TXT)
    assert LOOKUP in _TXT
    assert hist["2025-05-31"] == 732.7  # giá trị China May 2025 (không phải Japan 1142.8)
