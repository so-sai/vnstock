"""test_ifs_china_gold_reserve_adapter.py — Test adapter IFS China per-country.

Adversarial cases (per execution contract B1):
  - PIT visibility: value tại t chỉ từ vintage có publication_date <= t.
  - Vintage revision: cùng obs có NHIỀU publication_date (bảo toàn revision).
  - Coverage: China có data ở mọi vintage (0 NEEDS_MANUAL trên fixture đủ LM).
  - Không overwrite GLOBAL: series/entity khác adapter gốc, lookup đúng.
  - NO-LM: file xlsx không có Last-Modified -> NEEDS_MANUAL (không giả định pub).
  - Không match Macao/Taiwan: lookup 'China, P.R.: Mainland' không cộng Macao.

Dùng fixture XLSX tạo runtime bằng openpyxl (KHÔNG network).
"""

import datetime
import gc
import sys
import tempfile
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.ifs_china_gold_reserve_adapter import (
    ENTITY,
    SERIES,
    SOURCE,
    build_china_vintages,
    entity_value,
)
from src.research.ifs_gold_reserve_adapter import parse_snapshot

_HTTP_LM = "Thu, 04 Aug 2026 08:00:00 GMT"


def _mk_xlsx(path: Path, obs: list[str]) -> None:
    wb = openpyxl.Workbook()
    try:
        ws = wb.active
        ws.title = "Monthly"
        ws.cell(1, 3, "Year =>")
        ws.cell(2, 3, "Last month Col =>")
        ws.cell(3, 3, "This month Col =>")
        ws.cell(4, 3, "Turkey month column =>")
        for i, s in enumerate(obs):
            ws.cell(5, 4 + i, datetime.datetime.strptime(s, "%Y-%m-%d"))
        ws.cell(7, 1, "Country Lookup Column")
        ws.cell(7, 2, "Country")
        ws.cell(7, 3, "Comments")
        for i, s in enumerate(obs):
            ws.cell(7, 4 + i, datetime.datetime.strptime(s, "%Y-%m-%d"))
        wb.save(path)
    finally:
        wb.close()
        archive = getattr(wb, "_archive", None)
        if archive is not None and hasattr(archive, "close"):
            archive.close()


def _add_entity(path: Path, name: str, values: list) -> None:
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb["Monthly"]
        hdr = next((i for i in range(1, 20) if ws.cell(i, 1).value == "Country Lookup Column"), None)
        r = hdr + 1
        while ws.cell(r, 2).value is not None:
            r += 1
        ws.cell(r, 2, name)
        for j, v in enumerate(values):
            ws.cell(r, 4 + j, v)
        wb.save(path)
    finally:
        wb.close()
        archive = getattr(wb, "_archive", None)
        if archive is not None and hasattr(archive, "close"):
            archive.close()


@pytest.fixture
def fixture_dir() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        p = d / "Changes_latest_as_of_Aug2026_IFS.xlsx"
        _mk_xlsx(p, ["2026-04-30", "2026-05-31", "2026-06-30"])
        _add_entity(p, "China, P.R.: Mainland", [20.0, 25.0, 30.0])
        _add_entity(p, "China, P.R.: Macao", [50.0, 50.0, 50.0])
        _add_entity(p, "Taiwan Province of China", [7.0, 7.0, 7.0])
        p2 = d / "Changes_latest_as_of_Jun2026_IFS.xlsx"
        _mk_xlsx(p2, ["2026-04-30", "2026-05-31"])
        _add_entity(p2, "China, P.R.: Mainland", [20.0, 22.0])
        p3 = d / "Changes_latest_as_of_Jul2026_IFS.xlsx"
        _mk_xlsx(p3, ["2026-04-30", "2026-05-31", "2026-06-30"])
        _add_entity(p3, "China, P.R.: Mainland", [20.0, 25.0, 28.0])
        # CSV Last-Modified — Aug file KHÔNG có LM (test NO-LM)
        (d / "ifs_last_modified.csv").write_text(
            "folder,filename,last_modified\n"
            "2026-07,Changes_latest_as_of_Jul2026_IFS.xlsx,\"Tue, 07 Jul 2026 09:15:00 GMT\"\n"
            "2026-06,Changes_latest_as_of_Jun2026_IFS.xlsx,\"Wed, 03 Jun 2026 07:30:00 GMT\"\n",
            encoding="utf-8",
        )
        gc.collect()
        yield d


# ── entity_value ────────────────────────────────────────────────────────────


def test_entity_value_china_only(fixture_dir):
    snap = parse_snapshot(fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx")
    assert entity_value(snap, "2026-06-30", "China, P.R.: Mainland") == 30.0


def test_entity_value_not_macao_taiwan(fixture_dir):
    """Lookup China Mainland KHÔNG match Macao / Taiwan — chỉ lấy dòng đúng."""
    snap = parse_snapshot(fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx")
    val = entity_value(snap, "2026-06-30", "China, P.R.: Mainland")
    assert val == 30.0  # không cộng Macao 50 + Taiwan 7


def test_entity_value_none_when_missing_cell(fixture_dir):
    """Entity chưa report tại cột (None) -> None (không suy diễn 0)."""
    p = fixture_dir / "China_none.xlsx"
    _mk_xlsx(p, ["2026-04-30", "2026-05-31", "2026-06-30"])
    _add_entity(p, "China, P.R.: Mainland", [20.0, None, None])
    gc.collect()
    snap = parse_snapshot(p)
    assert entity_value(snap, "2026-04-30", "China, P.R.: Mainland") == 20.0
    assert entity_value(snap, "2026-06-30", "China, P.R.: Mainland") is None


# ── build_china_vintages ────────────────────────────────────────────────────


def test_build_china_series_entity(fixture_dir):
    rows, manual = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    assert rows
    for r in rows:
        assert r["series"] == SERIES
        assert r["entity"] == ENTITY
        assert r["source"] == SOURCE
        assert r["unit"] == "tonnes"
        assert r["observation_date"] <= r["publication_date"]  # PIT invariant


def test_build_china_not_overwrite_global(fixture_dir):
    """Series/entity KHÁC adapter gốc (IFS_GOLD_RESERVE_CHANGE/GLOBAL)."""
    rows, _ = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    assert SERIES != "IFS_GOLD_RESERVE_CHANGE"
    assert ENTITY != "GLOBAL"
    assert all(r["series"] == SERIES for r in rows)


def test_build_china_vintage_revision_preserved(fixture_dir):
    rows, _ = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    obs_apr = [r for r in rows if r["observation_date"] == "2026-04-30"]
    pubs = sorted(r["publication_date"] for r in obs_apr)
    assert pubs == ["2026-06-03", "2026-07-07"]  # Aug file thiếu LM -> bỏ
    assert len(obs_apr) == 2
    # revision bảo toàn: giá trị qua 2 vintage là dòng riêng
    vals = {r["publication_date"]: r["value"] for r in obs_apr}
    assert vals["2026-06-03"] == 20.0
    assert vals["2026-07-07"] == 20.0


def test_build_china_pit_visibility(fixture_dir):
    """PIT: obs 2026-05-31 tại 2026-06-03 chỉ thấy vintage Jun; tại 07-07 Jun+Jul."""
    rows, _ = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    obs_may = [r for r in rows if r["observation_date"] == "2026-05-31"]
    # Jun file (pub 06-03, value 22); Jul file (pub 07-07, value 25)
    visible_jun03 = [r for r in obs_may if r["publication_date"] <= "2026-06-03"]
    visible_jul07 = [r for r in obs_may if r["publication_date"] <= "2026-07-07"]
    assert len(visible_jun03) == 1 and visible_jun03[0]["value"] == 22.0
    assert len(visible_jul07) == 2
    # giá trị mới nhất tại mỗi thời điểm
    assert max(visible_jun03, key=lambda r: r["publication_date"])["value"] == 22.0
    assert max(visible_jul07, key=lambda r: r["publication_date"])["value"] == 25.0


def test_build_china_needs_manual_missing_lm(fixture_dir):
    """Aug2026 file có xlsx nhưng không có Last-Modified -> NEEDS_MANUAL."""
    rows, manual = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    assert any("Aug2026" in m["file"] and "Last-Modified" in m["reason"] for m in manual)


def test_build_china_provenance_has_lookup(fixture_dir):
    rows, _ = build_china_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    for r in rows[:3]:
        assert "China, P.R.: Mainland" in r["provenance"]
        assert "per-country" in r["provenance"]
        assert r["metadata"]


def test_email_utils_parses_lm():
    import email.utils

    dt = email.utils.parsedate_to_datetime(_HTTP_LM)
    assert dt is not None
    assert f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}" == "2026-08-04"
