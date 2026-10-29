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


def test_new_registry_event_without_metadata_fails_closed(monkeypatch):
    """[WHY] Thêm event vào registry mà quên pay_date/action_id ⇒ phải DỪNG ngay
    (thiếu provenance thanh toán = không có bằng chứng độc lập), cấm chạy audit
    với event "vô danh". `FOC` không có metadata ⇒ cũng phải DỪNG."""
    import src.research.audit_provenance as ap

    patched = dict(ap.CASH_EVENTS)
    patched["SMB"] = patched["SMB"] + (CashEvent(ex_date=date(2030, 1, 1), cash=1_000.0),)
    monkeypatch.setattr(ap, "CASH_EVENTS", patched)
    with pytest.raises(MissingDividendMetadataError):
        build_vsdc_dividends("SMB", AUDIT_WINDOW_START)
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
    for sym in ("VEA", "SMB"):
        divs = build_vsdc_dividends(sym, AUDIT_WINDOW_START)
        ids = [d.action_id for d in divs]
        assert len(set(ids)) == len(ids)
        assert all(i.startswith(f"VSDC/{sym}/") for i in ids)
