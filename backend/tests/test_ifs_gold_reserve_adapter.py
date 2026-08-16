"""test_ifs_gold_reserve_adapter.py — Test adapter IFS vintage ladder.

Dùng fixture XLSX tạo tại runtime bằng openpyxl (KHÔNG network, KHÔNG phụ thuộc
cache thật) để kiểm tra:
  - parse_snapshot: date row index 4 (tháng-end); header row detect động (5/6/7).
  - sum_changes: entity rule (loại Euro Area / Turkey* / Netherlands Antilles;
    giữ Turkey / SOFAZ); entity chưa report tại cột → không tính.
  - build_vintages: publication = Last-Modified từ CSV; PIT invariant obs <= pub;
    file thiếu Last-Modified → NEEDS_MANUAL.
  - contract rows: series/entity/source đúng; provenance ghi rule.
"""

import datetime
import email.utils
import gc
import sys
import tempfile
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.ifs_gold_reserve_adapter import (
    DATE_ROW_INDEX,
    KEEP_EXPLICIT,
    SERIES,
    SOURCE,
    build_vintages,
    parse_snapshot,
    sum_changes,
)

_HTTP_LM = "Thu, 04 Aug 2026 08:00:00 GMT"


def _mk_xlsx(path: Path, header_row: int = 6, obs: list[str] | None = None) -> None:
    """Tạo file IFS snapshot chuẩn với header tại header_row (6 hoặc 7).

    row0  : Year
    row1-3: marker cột
    row4  : date row tháng-end (DATE_ROW_INDEX)
    row5/6: month-start reference / marker
    header: 'Country Lookup Column' ... + dates
    data  : entity rows
    """
    if obs is None:
        obs = ["2026-04-30", "2026-05-31", "2026-06-30"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monthly"

    def _dt(s: str) -> datetime.datetime:
        return datetime.datetime.strptime(s, "%Y-%m-%d")

    # meta rows
    ws.cell(0 + 1, 3, "Year =>")
    ws.cell(1 + 1, 3, "Last month Col =>")
    ws.cell(2 + 1, 3, "This month Col =>")
    ws.cell(3 + 1, 3, "Turkey month column =>")
    # date row month-end tại index 4 (1-based col 4 = data col đầu)
    for i, s in enumerate(obs):
        ws.cell(DATE_ROW_INDEX + 1, 4 + i, _dt(s))
    # nếu header_row = 7, chèn thêm một marker row (như file Aug2026 có
    # 'Jordan month column') — header dịch xuống 1 dòng.
    if header_row == 7:
        ws.cell(5 + 1, 3, "Jordan month column =>")
        ws.cell(5 + 1, 4, 33)
        ws.cell(5 + 1, 5, 34)
        ws.cell(5 + 1, 6, 35)
    # header row tại header_row: cột dates tháng-start
    ws.cell(header_row + 1, 1, "Country Lookup Column")
    ws.cell(header_row + 1, 2, "Country")
    ws.cell(header_row + 1, 3, "Comments")
    for i, s in enumerate(obs):
        ws.cell(header_row + 1, 4 + i, _dt(s))
    try:
        wb.save(path)
    finally:
        wb.close()
        archive = getattr(wb, "_archive", None)
        if archive is not None and hasattr(archive, "close"):
            archive.close()


def _add_entity(path: Path, lookup: str, name: str, values: list) -> None:
    """Thêm một entity row ngay sau header (tìm dòng đầu tiên sau header trống)."""
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb["Monthly"]
        # tìm dòng header 'Country Lookup Column' → entity bắt đầu ngay sau
        hdr = next((i for i in range(1, 20) if ws.cell(i, 1).value == "Country Lookup Column"), None)
        r = hdr + 1
        while ws.cell(r, 2).value is not None:
            r += 1
        ws.cell(r, 1, lookup)
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
        # file Aug2026 chuẩn header_row=7 + 3 entity
        p = d / "Changes_latest_as_of_Aug2026_IFS.xlsx"
        _mk_xlsx(p, header_row=7)
        _add_entity(p, "Poland, Republic of", "Poland", [10.0, 12.0, 14.0])
        _add_entity(p, "Turkey", "Turkey", [-5.0, 2.0, 0.5])
        _add_entity(p, "Türkiye, Republic of", "Turkey*", [-50.0, -30.0, 3.0])
        _add_entity(p, "Euro Area (EA)", "Euro Area", [100.0, 100.0, 100.0])
        _add_entity(p, "Netherlands Antilles", "Netherlands Antilles", [3.0, 3.0, 3.0])
        _add_entity(p, "Curaçao and Sint Maarten", "Curacao & St. Maarten", [1.0, 1.0, 1.0])
        # file Jun2026 (header_row=7) + 1 entity
        p2 = d / "Changes_latest_as_of_Jun2026_IFS.xlsx"
        _mk_xlsx(p2, header_row=7, obs=["2026-04-30", "2026-05-31"])
        _add_entity(p2, "Poland, Republic of", "Poland", [10.0, 12.0])
        _add_entity(p2, "Turkey", "Turkey", [-5.0, 2.0])
        _add_entity(p2, "Türkiye, Republic of", "Turkey*", [-50.0, -30.0])
        # file Jul2026 (header_row=6, không marker Jordan)
        p3 = d / "Changes_latest_as_of_Jul2026_IFS.xlsx"
        _mk_xlsx(p3, header_row=6, obs=["2026-04-30", "2026-05-31", "2026-06-30"])
        _add_entity(p3, "Poland, Republic of", "Poland", [10.0, 12.0, 14.0])
        _add_entity(p3, "Turkey", "Turkey", [-5.0, 2.0, 0.5])
        _add_entity(p3, "Türkiye, Republic of", "Turkey*", [-50.0, -30.0, 3.0])
        _add_entity(p3, "Euro Area (EA)", "Euro Area", [100.0, 100.0, 100.0])
        _add_entity(p3, "Netherlands Antilles", "Netherlands Antilles", [3.0, 3.0, 3.0])
        _add_entity(p3, "Curaçao and Sint Maarten", "Curacao & St. Maarten", [1.0, 1.0, 1.0])
        # CSV Last-Modified
        (d / "ifs_last_modified.csv").write_text(
            "folder,filename,last_modified\n"
            "2026-08,Changes_latest_as_of_Aug2026_IFS.xlsx,\"Thu, 04 Aug 2026 08:00:00 GMT\"\n"
            "2026-07,Changes_latest_as_of_Jul2026_IFS.xlsx,\"Tue, 07 Jul 2026 09:15:00 GMT\"\n"
            "2026-06,Changes_latest_as_of_Jun2026_IFS.xlsx,\"Wed, 03 Jun 2026 07:30:00 GMT\"\n",
            encoding="utf-8",
        )
        gc.collect()
        yield d


# ── parse_snapshot ──────────────────────────────────────────────────────────


def test_parse_snapshot_date_row_index4(fixture_dir):
    snap = parse_snapshot(fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx")
    assert snap["min_obs"] == "2026-04-30"
    assert snap["data_to"] == "2026-06-30"
    assert len(snap["col_obs"]) == 3


def test_parse_snapshot_header_row_dynamic(fixture_dir):
    """Header detect động: file header_row=6 (Jul2026) và =7 (Aug2026) đều parse."""
    a = parse_snapshot(fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx")
    b = parse_snapshot(fixture_dir / "Changes_latest_as_of_Jul2026_IFS.xlsx")
    assert a["data_to"] == "2026-06-30"
    assert b["data_to"] == "2026-06-30"
    names_a = {n for n, _ in a["entities"]}
    names_b = {n for n, _ in b["entities"]}
    assert "Poland" in names_a and "Poland" in names_b
    assert "Euro Area" in names_a and "Euro Area" in names_b


# ── sum_changes / entity rule ───────────────────────────────────────────────


def test_sum_excludes_aggregate_and_trkstar_and_netlant(fixture_dir):
    snap = parse_snapshot(fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx")
    # Poland 14 + Turkey 0.5 + Curacao 1 = 15.5 (loại Euro Area 100, Turkey* 3, NethAnt 3)
    assert sum_changes(snap, "2026-06-30") == 15.5


def test_sum_keeps_sofaz(fixture_dir):
    """SOFAZ giữ lại (không loại). Thêm SOFAZ entity → tổng tăng đúng."""
    p = fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx"
    _add_entity(p, "State Oil Fund of the Republic of Azerbaijan (SOFAZ)", KEEP_EXPLICIT[1], [2.0, 2.0, 2.0])
    gc.collect()
    snap = parse_snapshot(p)
    assert sum_changes(snap, "2026-06-30") == 17.5  # 15.5 + 2


def test_sum_none_when_obs_missing(fixture_dir):
    snap = parse_snapshot(fixture_dir / "Changes_latest_as_of_Jun2026_IFS.xlsx")
    assert sum_changes(snap, "2026-06-30") is None  # data_to = 2026-05-31


def test_sum_skips_missing_cell(fixture_dir):
    """Entity chưa report tại cột (None) → không tính (không phải 0)."""
    p = fixture_dir / "Changes_latest_as_of_Aug2026_IFS.xlsx"
    # thêm China không report tháng cuối (None)
    _add_entity(p, "China, P.R.: Mainland", "China, P.R.: Mainland", [20.0, None, None])
    gc.collect()
    snap = parse_snapshot(p)
    # obs 2026-04: Poland 10 + Turkey -5 + Curacao 1 + China 20 = 26
    assert sum_changes(snap, "2026-04-30") == 26.0
    # obs 2026-06: China None → không tính: 15.5
    assert sum_changes(snap, "2026-06-30") == 15.5


# ── build_vintages ──────────────────────────────────────────────────────────


def test_build_vintages_publication_from_lm(fixture_dir):
    rows, manual = build_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    assert manual == []
    pubs = {r["publication_date"] for r in rows}
    assert pubs == {"2026-06-03", "2026-07-07", "2026-08-04"}
    for r in rows:
        assert r["series"] == SERIES
        assert r["entity"] == "GLOBAL"
        assert r["source"] == SOURCE
        assert r["unit"] == "tonnes"
        assert r["observation_date"] <= r["publication_date"]  # PIT invariant


def test_build_vintages_revision_preserved(fixture_dir):
    """Cùng obs 2026-04 có nhiều vintage (Jun/Jul/Aug) — revision bảo toàn."""
    rows, _ = build_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    obs_apr = [r for r in rows if r["observation_date"] == "2026-04-30"]
    pubs = sorted(r["publication_date"] for r in obs_apr)
    assert pubs == ["2026-06-03", "2026-07-07", "2026-08-04"]
    # giá trị obs 2026-04 không đổi qua vintage (fixture cố định) — nhưng kiểm
    # tra mỗi vintage là dòng riêng (3 dòng).
    assert len(obs_apr) == 3


def test_build_vintages_needs_manual_missing_lm(fixture_dir):
    """File xlsx có nhưng không có Last-Modified → NEEDS_MANUAL."""
    p = fixture_dir / "Changes_latest_as_of_May2026_IFS.xlsx"
    _mk_xlsx(p, header_row=7, obs=["2026-03-31", "2026-04-30"])
    rows, manual = build_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    assert any(m["file"] == p.name for m in manual)
    assert any("Last-Modified" in m["reason"] for m in manual)


def test_build_vintages_provenance_has_rule(fixture_dir):
    rows, _ = build_vintages(fixture_dir, fixture_dir / "ifs_last_modified.csv")
    for r in rows[:3]:
        assert "EXCLUDE" in r["provenance"]
        assert "n_entities=" in r["provenance"]
        assert r["metadata"]


# ── parse lỗi ───────────────────────────────────────────────────────────────


def test_parse_snapshot_missing_header(fixture_dir):
    import openpyxl

    p = fixture_dir / "bad.xlsx"
    wb = openpyxl.Workbook()
    try:
        ws = wb.active
        ws.title = "Monthly"
        ws.cell(5, 3, "sofa")
        wb.save(p)
    finally:
        wb.close()
    with pytest.raises(ValueError):
        parse_snapshot(p)


def test_email_utils_parses_lm():
    dt = email.utils.parsedate_to_datetime(_HTTP_LM)
    assert dt is not None
    assert f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}" == "2026-08-04"
