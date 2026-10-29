"""Test cho lớp SSOT cổ tức dùng bởi các runner audit thực nghiệm (Pha 1–5).

[WHY] Ba lỗ hổng kỷ luật đã lặp lại y hệt ở runner Pha 1/2/3 (đo 28–29/10/2026:
inline_events=7 (VEA) / 13 (SMB), is_certified_checks=0, non_decisional=0,
registry=0). Mỗi bản sao event trong runner là một cơ hội trôi lệch với
`cash_event_registry` — đúng bẫy đã bị Hội đồng loại bỏ ở cổng provenance.
Test dưới đây khoá: (a) tập event audit SUY RA từ registry, (b) metadata
(pay_date/action_id) thiếu ⇒ DỪNG, (c) metadata mồ côi ⇒ DỪNG.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.audit_provenance import (
        AUDIT_WINDOW_START,
        MissingDividendMetadataError,
        build_vsdc_dividends,
        dividend_cash_total,
    )
    from src.research.cash_event_registry import CASH_EVENTS
    from src.research.raw_price_reconstruction import CashEvent
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.audit_provenance import (
        AUDIT_WINDOW_START,
        MissingDividendMetadataError,
        build_vsdc_dividends,
        dividend_cash_total,
    )
    from backend.src.research.cash_event_registry import CASH_EVENTS
    from backend.src.research.raw_price_reconstruction import CashEvent


def test_vea_dividends_derived_from_registry_exactly():
    """[WHY] VEA: 7 event trong kỳ audit, tổng 29.079,46 đ/cp — con số này đang
    được in cứng trong báo cáo; nếu registry bị sửa mà runner vẫn dùng bản sao
    thì báo cáo nói dối về chính nguồn của nó."""
    divs = build_vsdc_dividends("VEA", AUDIT_WINDOW_START)
    assert len(divs) == len(CASH_EVENTS["VEA"]) == 7
    assert [(d.ex_date, d.cash_per_share) for d in divs] == [
        (e.ex_date, e.cash) for e in CASH_EVENTS["VEA"]
    ]
    assert dividend_cash_total(divs) == pytest.approx(29_079.46, abs=1e-6)


def test_smb_dividends_exclude_warmup_and_match_audit_total():
    """[WHY] SMB registry có 13 event (gồm 2020-12-22 chỉ dùng phục nguyên/warm-up)
    nhưng kỳ audit 2021-2026 chỉ có 12 event, tổng 20.500 đ/cp. Ranh giới này phải
    do cửa sổ audit quyết định, không do một danh sách chép tay thứ hai."""
    divs = build_vsdc_dividends("SMB", AUDIT_WINDOW_START)
    assert len(divs) == 12
    assert date(2020, 12, 22) not in {d.ex_date for d in divs}
    assert dividend_cash_total(divs) == pytest.approx(20_500.0, abs=1e-6)


def test_foc_dividends_derived_from_registry_exactly():
    """[WHY] FOC: 6 event, tổng 55.000 đ/cp (báo cáo E15 in cứng con số này). Ba mã
    phải cùng một cơ chế — nếu FOC vẫn giữ danh sách riêng thì quy tắc 'cấm khai
    báo inline' trong CLI_INDEX là lời nói suông."""
    divs = build_vsdc_dividends("FOC", AUDIT_WINDOW_START)
    assert len(divs) == len(CASH_EVENTS["FOC"]) == 6
    assert [(d.ex_date, d.cash_per_share) for d in divs] == [
        (e.ex_date, e.cash) for e in CASH_EVENTS["FOC"]
    ]
    assert dividend_cash_total(divs) == pytest.approx(55_000.0, abs=1e-6)


def test_new_registry_event_without_metadata_fails_closed(monkeypatch):
    """[WHY] Thêm event vào registry mà quên pay_date/action_id ⇒ phải DỪNG ngay
    (thiếu provenance thanh toán = không có bằng chứng độc lập), cấm chạy audit
    với event "vô danh"."""
    import src.research.audit_provenance as ap

    patched = dict(ap.CASH_EVENTS)
    patched["SMB"] = patched["SMB"] + (CashEvent(ex_date=date(2030, 1, 1), cash=1_000.0),)
    monkeypatch.setattr(ap, "CASH_EVENTS", patched)
    with pytest.raises(MissingDividendMetadataError):
        build_vsdc_dividends("SMB", AUDIT_WINDOW_START)

    # Mã hoàn toàn không có metadata (vd mã mới thêm vào registry) cũng phải DỪNG.
    no_meta = {k: v for k, v in ap._DIVIDEND_METADATA.items() if k != "FOC"}
    monkeypatch.setattr(ap, "_DIVIDEND_METADATA", no_meta)
    with pytest.raises(MissingDividendMetadataError):
        build_vsdc_dividends("FOC", AUDIT_WINDOW_START)


def test_orphan_metadata_fails_closed(monkeypatch):
    """[WHY] Chiều ngược lại: metadata còn nhưng event đã bị xoá khỏi registry ⇒
    runner đang dùng một sự kiện không còn trong SSOT ⇒ DỪNG, không im lặng bỏ qua."""
    import src.research.audit_provenance as ap

    patched = {k: dict(v) for k, v in ap._DIVIDEND_METADATA.items()}
    patched["SMB"][date(2031, 3, 3)] = (date(2031, 4, 4), "VSDC/SMB/20310303/CASH_1000")
    monkeypatch.setattr(ap, "_DIVIDEND_METADATA", patched)
    with pytest.raises(MissingDividendMetadataError):
        build_vsdc_dividends("SMB", AUDIT_WINDOW_START)


def test_action_ids_are_unique_and_namespaced():
    """[WHY] `independent_provenance_set` của engine đếm bằng action_id; trùng id
    ⇒ engine tưởng có ít nguồn độc lập hơn thực tế và provenance bị đánh giá sai."""
    for sym in ("VEA", "SMB", "PAT", "DSN"):
        divs = build_vsdc_dividends(sym, AUDIT_WINDOW_START)
        ids = [d.action_id for d in divs]
        assert len(set(ids)) == len(ids)
        assert all(i.startswith(f"VSDC/{sym}/") for i in ids)


def test_pat_dividends_cover_whole_series_with_9_events():
    """[WHY] PAT niêm yết 06/2022 nhưng lịch sử DIV có 11 đợt: 2 đợt TRƯỚC bar
    đầu (2021-12-06, 2022-04-19) không được đưa vào registry vì
    `reconstruct_raw_prices` tra `idx_of[ex_date]` và sẽ KeyError; đồng thời
    không được đưa vào metadata (sẽ thành metadata mồ côi). Tổng 63.055 đ/cp là
    con số báo cáo Pha 4 phải in ra từ dữ liệu, không hardcode."""
    divs = build_vsdc_dividends("PAT", date(2022, 6, 17))
    assert len(divs) == len(CASH_EVENTS["PAT"]) == 9
    assert divs[0].ex_date == date(2022, 8, 4)  # đợt đầu TIÊN trong chuỗi bar
    assert dividend_cash_total(divs) == pytest.approx(63_055.0, abs=1e-6)


def test_dsn_dividends_total_matches_declared_cash_flow():
    """[WHY] DSN: 10 đợt, tổng đúng 20.000 đ/cp trong kỳ 2021-2026 — con số đối
    chiếu với dòng tiền cổ tức doanh nghiệp công bố. Lệch một đợt (vd quên
    1.600 đ kỳ 2026-05-18) sẽ làm cả báo cáo Pha 5 sai mà không ai thấy."""
    divs = build_vsdc_dividends("DSN", AUDIT_WINDOW_START)
    assert len(divs) == len(CASH_EVENTS["DSN"]) == 10
    assert dividend_cash_total(divs) == pytest.approx(20_000.0, abs=1e-6)
    # pay_date phải SAU ex_date (fail-closed nếu ai đó đảo cột khi chép tay).
    assert all(d.pay_date > d.ex_date for d in divs)


def test_all_registry_symbols_have_metadata_and_tick():
    """[WHY] Chốt chặn toàn cục: thêm mã vào registry mà quên tick/lưới/sàn ⇒
    cổng fail-closed ở chế độ `--reconstructed` chỉ khi người ta nhớ chạy đúng
    mã đó. Test này quét MỌI mã trong registry để lỗ hổng lộ ngay ở CI."""
    from src.research.cash_event_registry import DAILY_LIMIT_PCT, TICK_SIZES

    for sym in CASH_EVENTS:
        assert sym in TICK_SIZES, f"{sym} thiếu lưới tick ⇒ solve factor vô nghĩa"
        assert sym in DAILY_LIMIT_PCT, f"{sym} thiếu biên trần/sàn theo sàn"
        assert build_vsdc_dividends(sym, AUDIT_WINDOW_START), f"{sym} không có event trong kỳ audit"

