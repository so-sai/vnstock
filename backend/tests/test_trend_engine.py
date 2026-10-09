"""Test 4 module Trend Engine — đối chiếu từng câu chữ của spec §4, §5, §6, §10.

Trọng tâm của bộ test này KHÔNG phải "chạy không lỗi" mà là bắt ba lớp lỗi im lặng:
  1. Sai THỨ TỰ vector organ (lọc nhầm organ mà không báo gì).
  2. Breakout vô tình bao gồm phiên hiện tại (điều kiện trở thành luôn đúng).
  3. Kill switch thi hành trigger YẾU hơn khi trigger MẠNH cũng đang bật.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

from src.engine import trend_circuit_breaker as cb  # noqa: E402
from src.engine import trend_engine as te  # noqa: E402
from src.engine import trend_sizing as ts  # noqa: E402
from src.engine.trend_organ_filter import (  # noqa: E402
    ORGAN_VECTOR_ORDER,
    OrganVerdict,
    evaluate_state,
    organ_dict,
)


# ══════════════════════════════════════════════════════════════════════
# 1. ORGAN FILTER — pin thứ tự vector
# ══════════════════════════════════════════════════════════════════════
class _Organs:
    def __init__(self, vector):
        self.vector = vector


class _State:
    def __init__(self, vector, archetype="HIGH_QUALITY_COMPOUNDER", conf=0.9):
        self.organs = _Organs(vector)
        self.archetype = archetype
        self.archetype_confidence = conf


def test_thu_tu_vector_organ_duoc_pin():
    """[Profit, Cash, BS, Eff, Moat] — nếu ai đổi thứ tự, test này phải đỏ.

    Đây là lỗi IM LẶNG nguy hiểm nhất: hoán vị BS↔Eff vẫn chạy êm, chỉ lọc sai mã.
    """
    assert ORGAN_VECTOR_ORDER == ("Profit", "Cash", "BS", "Eff", "Moat")
    d = organ_dict([0.9, 0.8, 0.31, 0.61, 0.51])
    assert d["Profit"] == 0.9
    assert d["Cash"] == 0.8
    assert d["BS"] == 0.31
    assert d["Eff"] == 0.61
    assert d["Moat"] == 0.51


def test_vector_sai_kich_thuoc_thi_raise():
    with pytest.raises(ValueError) as e:
        organ_dict([0.9, 0.8, 0.31])
    assert "schema organ đã đổi" in str(e.value)
    with pytest.raises(ValueError):
        organ_dict(None)


def test_khong_co_du_lieu_organ_thi_loai_kem_ly_do():
    v = evaluate_state(None)
    assert v.passed is False
    assert "missing > fake" in v.reasons[0]


def test_distressed_bi_loai():
    v = evaluate_state(_State([0.9, 0.9, 0.9, 0.9, 0.9], archetype="DISTRESSED", conf=0.77))
    assert not v.passed
    assert any("DISTRESSED" in r for r in v.reasons)


def test_bien_organ_dung_nguong_duoc_chap_nhan():
    """BS = 0.30 CHÍNH XÁC phải ĐẠT (spec ghi ≥ 0.30), không phải trượt."""
    v = evaluate_state(_State([0.5, 0.5, 0.30, 0.60, 0.50]))
    assert v.passed, f"biên đúng ngưỡng phải đạt, nhận {v.reasons}"


def test_tung_organ_duoi_nguong_deu_bi_bat():
    for idx, organ in ((2, "BS"), (3, "Eff"), (4, "Moat")):
        vec = [0.9, 0.9, 0.9, 0.9, 0.9]
        vec[idx] = 0.01
        v = evaluate_state(_State(vec))
        assert not v.passed, f"{organ} dưới ngưỡng mà vẫn đạt"
        assert any(organ in r for r in v.reasons), f"thiếu lý do cho {organ}: {v.reasons}"


# ══════════════════════════════════════════════════════════════════════
# 2. ENTRY RULES (§4)
# ══════════════════════════════════════════════════════════════════════
GOOD_ORGAN = OrganVerdict(passed=True, organs={"BS": 0.4, "Eff": 0.7, "Moat": 0.6})


def _feat(
    symbol: str = "AAA",
    date: str = "2021-06-01",
    close: float = 100.0,
    high_20: float = 95.0,
    ma20: float = 90.0,
    volume: float = 2_000_000.0,
    adv20: float = 1_000_000.0,  # 2× > 1.3× ⇒ đạt
    rs: float = 95.0,
    block_source: str = "NONE",
    organ: OrganVerdict | None = GOOD_ORGAN,
) -> te.TrendFeatures:
    return te.TrendFeatures(
        symbol, date, close, high_20, ma20, volume, adv20, rs, block_source, organ
    )


def test_du_9_dieu_kien_thi_dat():
    assert te.entry_verdict(_feat()).passed


@pytest.mark.parametrize(
    "over,needle",
    [
        (dict(rs=89.9), "RS"),
        (dict(adv20=499_999.0), "ADV20"),
        (dict(close=95.0), "breakout"),  # bằng đỉnh, không vượt
        (dict(close=89.0), "MA20"),
        (dict(volume=1_299_999.0), "volume"),
        (dict(block_source="INTERNAL"), "nội tại"),
        (dict(organ=None), "missing > fake"),
        (dict(organ=OrganVerdict(passed=False, reasons=["BS 0.10 < 0.3"])), "BS 0.10"),
    ],
)
def test_thieu_bat_ky_dieu_kien_nao_thi_truot(over, needle):
    g = te.entry_verdict(_feat(**over))
    assert not g.passed
    assert any(needle in r for r in g.reasons), f"thiếu lý do chứa {needle!r}: {g.reasons}"


def test_bien_rs_va_volume_dung_nguong():
    assert te.entry_verdict(_feat(rs=90.0)).passed, "RS = 90 phải ĐẠT (spec ≥)"
    assert te.entry_verdict(_feat(volume=1_300_000.0)).passed, "volume = 1.3×ADV20 phải ĐẠT"
    assert not te.entry_verdict(_feat(volume=1_299_999.0)).passed


def test_breakout_KHONG_bao_gom_phien_hien_tai():
    """close == high_20 ⇒ KHÔNG breakout. Nếu high_20 gồm phiên hiện tại,
    điều kiện thành `close > close` và luôn Sai, hoặc bị nới thành luôn Đúng."""
    assert te.is_breakout(close=100.0, high_20=100.0, ma20=90.0) is False
    assert te.is_breakout(close=100.01, high_20=100.0, ma20=90.0) is True
    assert te.BREAKOUT_LOOKBACK == 20


def test_block_source_world_macro_duoc_phep():
    for src in ("WORLD", "MACRO", "NONE"):
        assert te.entry_verdict(_feat(block_source=src)).passed, f"{src} phải được phép"


# ══════════════════════════════════════════════════════════════════════
# 3. KILL SWITCH (§5 + §10)
# ══════════════════════════════════════════════════════════════════════
def _pos(peak=100.0, now=100.0, rs=95.0, rs_prev=95.0):
    return cb.PositionView("AAA", peak, now, rs, rs_prev)


def _mkt(regime="NEUTRAL", vn3=0.0, atr=1.0, atr_ma=1.0):
    return cb.MarketView(regime, vn3, atr, atr_ma)


def test_tu_thu_tu_uu_tien_khop_bang_10():
    assert cb.PRIORITY[0] == "K3_VNINDEX_MINUS5"
    assert cb.PRIORITY[-1] == "VOLATILITY_SPIKE"
    assert len(cb.PRIORITY) == 6
    assert cb.ACTION["K3_VNINDEX_MINUS5"] == cb.FULL_EXIT
    assert cb.ACTION["TRAILING_MINUS5"] == cb.PARTIAL_50


@pytest.mark.parametrize(
    "pos,mkt,expect",
    [
        (_pos(), _mkt(vn3=-5.0), "K3_VNINDEX_MINUS5"),
        (_pos(), _mkt(regime="CRISIS"), "REGIME_CRISIS"),
        (_pos(now=91.9), _mkt(), "TRAILING_MINUS8"),
        (_pos(rs=60.0, rs_prev=60.0), _mkt(), "RS_BELOW_70_TWO_SESSIONS"),
        (_pos(now=94.9), _mkt(), "TRAILING_MINUS5"),
        (_pos(), _mkt(atr=2.5, atr_ma=1.0), "VOLATILITY_SPIKE"),
    ],
)
def test_tung_trigger_bat_dung(pos, mkt, expect):
    fired = cb.evaluate(pos, mkt)
    assert expect in fired, f"{expect} không bật: {fired}"


def test_khong_co_trigger_thi_giu_nguyen():
    assert cb.evaluate(_pos(), _mkt()) == []
    assert cb.resolve([]) is None


def test_log_TAT_CA_trigger_va_thi_hanh_dung_uu_tien():
    """VN-Index −5% + CRISIS + trailing −8% cùng bật ⇒ phải log cả 3, thi hành K3."""
    fired = cb.evaluate(_pos(now=90.0), _mkt(regime="CRISIS", vn3=-6.0))
    assert set(fired) == {"K3_VNINDEX_MINUS5", "REGIME_CRISIS", "TRAILING_MINUS8"}
    assert fired[0] == "K3_VNINDEX_MINUS5", "phải sắp theo thứ tự ưu tiên §10"
    assert cb.resolve(fired) == cb.FULL_EXIT


def test_trailing_8pct_khong_them_trigger_5pct():
    """Đã chạm −8% thì trigger −5% bị bao trùm; thêm vào chỉ làm nhiễu log."""
    assert "TRAILING_MINUS5" not in cb.evaluate(_pos(now=90.0), _mkt())


def test_rs_phai_truot_HAI_phien_lien_tiep():
    assert cb.evaluate(_pos(rs=60.0, rs_prev=95.0), _mkt()) == []
    assert cb.evaluate(_pos(rs=60.0, rs_prev=69.9), _mkt()) == ["RS_BELOW_70_TWO_SESSIONS"]


def test_vol_spike_can_atr_vuot_2x_va_mau_duong():
    assert cb.evaluate(_pos(), _mkt(atr=2.01, atr_ma=1.0)) == ["VOLATILITY_SPIKE"]
    assert cb.evaluate(_pos(), _mkt(atr=1.99, atr_ma=1.0)) == []
    assert cb.evaluate(_pos(), _mkt(atr=5.0, atr_ma=0.0)) == [], "mẫu 0 ⇒ không kết luận"


def test_chong_whipsaw_5_phien():
    assert cb.reentry_allowed(4) is False
    assert cb.reentry_allowed(5) is True


# ══════════════════════════════════════════════════════════════════════
# 4. SIZING (§6)
# ══════════════════════════════════════════════════════════════════════
def test_base_size_5pct_nav():
    d = ts.position_size(nav=1e9, in_trend_pct=0.0, open_positions=0)
    assert d.target_pct == 5.0


def test_toi_da_4_vi_the():
    d = ts.position_size(nav=1e9, in_trend_pct=20.0, open_positions=4)
    assert d.target_pct == 0.0
    assert "4 vị thế" in d.reasons[0]


def test_tran_trend_20pct_cat_bot():
    d = ts.position_size(nav=1e9, in_trend_pct=18.0, open_positions=0)
    assert d.target_pct == 2.0
    assert any("trần Trend" in r for r in d.reasons)


def test_tran_trend_cham_thi_khong_vao():
    d = ts.position_size(nav=1e9, in_trend_pct=20.0, open_positions=1)
    assert d.target_pct == 0.0
    assert any("chạm trần" in r for r in d.reasons)


def test_discount_multiplier_polarity_va_leveraged():
    assert ts.discount_multiplier()[0] == 1.0
    assert ts.discount_multiplier(polarity_mismatch=True)[0] == 0.5
    assert ts.discount_multiplier(archetype="LEVERAGED_GROWTH")[0] == 0.8
    d, notes = ts.discount_multiplier(polarity_mismatch=True, archetype="LEVERAGED_GROWTH")
    assert d == pytest.approx(0.4), "nhân dồn khi cả hai điều kiện đúng"
    assert len(notes) == 2


def test_size_ap_dung_discount():
    d = ts.position_size(nav=1e9, in_trend_pct=0.0, open_positions=0, polarity_mismatch=True)
    assert d.target_pct == 2.5
    d2 = ts.position_size(nav=1e9, in_trend_pct=0.0, open_positions=0, archetype="LEVERAGED_GROWTH")
    assert d2.target_pct == 4.0


def test_cap_beta_weighted_25pct():
    """beta = 2.0 và đã dùng 22% ⇒ chỉ còn (25−22)/2 = 1.5% NAV."""
    d = ts.position_size(nav=1e9, in_trend_pct=0.0, open_positions=0, beta=2.0, beta_weighted_pct=22.0)
    assert d.target_pct == pytest.approx(1.5)
    assert any("cap beta" in r for r in d.reasons)


def test_cham_cap_beta_thi_khong_vao():
    d = ts.position_size(nav=1e9, in_trend_pct=0.0, open_positions=0, beta=1.0, beta_weighted_pct=25.0)
    assert d.target_pct == 0.0


def test_nav_khong_hop_le_thi_raise():
    with pytest.raises(ValueError):
        ts.position_size(nav=0.0, in_trend_pct=0.0, open_positions=0)


def test_nguong_sizing_khop_spec_6():
    assert (ts.BASE_SIZE_PCT, ts.MAX_POSITIONS, ts.MAX_TREND_PCT, ts.BETA_CAP_PCT) == (5.0, 4, 20.0, 25.0)
