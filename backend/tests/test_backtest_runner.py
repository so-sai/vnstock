"""test_backtest_runner.py — hợp đồng của runner Trend Engine (spec v1.1).

Trọng tâm: runner **KHÔNG ĐƯỢC BỊA KẾT QUẢ**. Một skeleton trả về Sharpe=0 / 0 lệnh /
MaxDD=0 sẽ trông y hệt một backtest "chạy được nhưng chiến lược kém", trong khi sự thật
là engine chưa tồn tại. Đó là dạng sai nguy hiểm nhất — người đọc không phân biệt được.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

RUNNER = PROJECT_ROOT / "backend" / "src" / "backtest" / "backtest_trend_2021_2025.py"


@pytest.fixture(scope="module")
def bt():
    spec = importlib.util.spec_from_file_location("bt_trend", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bt_trend"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── FAIL-CLOSED: không được trả về kết quả rỗng ───────────────────────
def test_runner_tu_choi_chay_khi_thieu_module(bt):
    """Phải RAISE, tuyệt đối không trả về dict Sharpe=0."""
    with pytest.raises(RuntimeError) as e:
        bt.run_backtest()
    msg = str(e.value)
    assert "KHÔNG ĐƯỢC CHẠY" in msg
    assert "trend_engine" in msg
    assert "Sharpe=0" in msg, "thông báo phải giải thích vì sao không bịa số"


def test_force_khong_duoc_ho_tro(bt):
    """Không có cửa sau để 'cứ chạy đại'."""
    with pytest.raises(RuntimeError):
        bt.run_backtest(force=True)


def test_khong_co_trade_nao_dieu_kien_khong_du(bt):
    """Không được để lại dấu vết của một lần chạy giả."""
    with pytest.raises(RuntimeError):
        bt.run_backtest()
    assert not (PROJECT_ROOT / "backend" / "reports" / "backtest" / "backtest_summary.json").exists(), (
        "runner không được ghi file kết quả khi chưa chạy được"
    )


# ── Portfolio: MTM + chi phí (đã chốt theo spec §11) ──────────────────
def test_mtm_va_chi_phi_vao_lenh(bt):
    pf = bt.Portfolio(initial_nav=100.0)
    assert pf.enter("AAA", price=10.0, date="2021-01-04", target_notional=50.0) is True
    # đã trả 50 notional + phí 0.25% của 50 = 0.125
    assert pf.cash == pytest.approx(100.0 - 50.0 - 0.125)
    # NAV tại giá vào lệnh = 100 - phí (chưa lãi lỗ)
    assert pf.nav({"AAA": 10.0}) == pytest.approx(100.0 - 0.125)


def test_chi_phi_round_trip_dung_0_5_pct(bt):
    """Spec §11: 0.15% phí + 0.10% trượt giá mỗi chiều ⇒ 0.50% round-trip."""
    pf = bt.Portfolio(initial_nav=100.0)
    pf.enter("AAA", price=10.0, date="2021-01-04", target_notional=50.0)
    pf.exit("AAA", price=10.0, date="2021-01-05", reason="test")
    loss = 100.0 - pf.cash
    assert loss / 50.0 == pytest.approx(0.005, rel=1e-6), "round-trip phải đúng 0.50% notional"


def test_nav_mtm_theo_gia_close(bt):
    pf = bt.Portfolio(initial_nav=100.0)
    pf.enter("AAA", price=10.0, date="d1", target_notional=50.0)
    qty = pf.positions["AAA"].qty
    pf.mark("d1", {"AAA": 10.0})
    pf.mark("d2", {"AAA": 12.0})  # +20%
    nav_d2 = pf.nav_history[-1][1]
    assert nav_d2 == pytest.approx(100.0 - 0.125 + qty * 2.0), "lãi chưa chốt PHẢI vào NAV"


def test_toi_da_4_vi_the(bt):
    pf = bt.Portfolio(initial_nav=100.0)
    for i in range(5):
        pf.enter(f"S{i}", price=10.0, date="d1", target_notional=10.0)
    assert len(pf.positions) == bt.MAX_POSITIONS == 4


def test_khong_mua_trung_ma(bt):
    pf = bt.Portfolio(initial_nav=100.0)
    assert pf.enter("AAA", price=10.0, date="d1", target_notional=10.0) is True
    assert pf.enter("AAA", price=10.0, date="d1", target_notional=10.0) is False


# ── Metrics (hàm thuần) ───────────────────────────────────────────────
def test_sharpe_nav_khong_doi_bang_0(bt):
    assert bt.sharpe([100.0] * 50) == 0.0


def test_max_drawdown_dung_so(bt):
    # 100 → 120 → 90 : đỉnh 120, đáy 90 ⇒ DD = 25%
    assert bt.max_drawdown([100.0, 120.0, 90.0]) == pytest.approx(25.0)


def test_max_drawdown_khong_co_sut_giam(bt):
    assert bt.max_drawdown([100.0, 101.0, 102.0]) == 0.0


def test_win_rate_va_profit_factor(bt):
    trades = [
        bt.Trade("A", "d1", "d2", 10, 12, 1, +20.0, 1, "x"),
        bt.Trade("B", "d1", "d2", 10, 9, 1, -10.0, 1, "x"),
    ]
    assert bt.win_rate(trades) == pytest.approx(50.0)
    assert bt.profit_factor(trades) == pytest.approx(2.0)
    assert bt.win_rate([]) == 0.0


def test_export_khong_bia_so(bt, tmp_path, monkeypatch):
    """export_results chỉ dùng đúng dữ liệu truyền vào."""
    monkeypatch.setattr(bt, "OUT_DIR", tmp_path)
    pf = bt.Portfolio(initial_nav=100.0)
    pf.enter("AAA", price=10.0, date="2021-01-04", target_notional=50.0)
    pf.mark("2021-01-04", {"AAA": 10.0})
    pf.mark("2021-01-05", {"AAA": 11.0})
    info = {"preregistration": {"spec": "x.md", "sha256": "deadbeef"}, "effective_universe": 7}
    s = bt.export_results(pf, ["2021-01-04", "2021-01-05"], info)
    assert s["total_trades"] == 0
    assert s["effective_universe"] == 7
    assert s["cost_per_side_pct"] == 0.25
    assert (tmp_path / "backtest_summary.json").exists()
    assert (tmp_path / "backtest_trades.csv").exists()


# ── Preflight: mẫu số universe phải khớp spec §3.1 ────────────────────
def test_preflight_mau_so_theo_nam_khop_thuc_te(bt):
    """Số ĐO ĐƯỢC (không phải số ước tính trong spec) cho universe theo năm."""
    pf = bt.preflight()
    y21 = pf["universe_per_year"]["2021"]
    assert 850 <= y21["price"] <= 960, f"universe giá 2021 lệch xa kỳ vọng: {y21}"
    assert 700 <= y21["facts"] <= 780, f"phủ cơ bản 2021 lệch xa kỳ vọng: {y21}"
    assert 500 <= y21["intersection"] <= 650, f"giao điểm 2021 lệch xa thực tế: {y21}"


def test_giao_diem_thuc_te_NHO_HON_uoc_tinh_trong_spec(bt):
    """📌 Ghi lại sự thật để không ai đọc §3.1 rồi tin con số ~740.

    Spec v1.1 §3.1 ghi universe ≈ 740, suy từ giả định "phủ cơ bản ⊆ phủ giá".
    Đo thật: giao điểm chỉ ~575/năm. Chênh lệch đến từ HAI phía:
      · ~328 mã có giá (≥200 phiên/năm) nhưng KHÔNG có dữ liệu tài chính
      · ~163 mã có dữ liệu tài chính nhưng KHÔNG đủ 200 phiên giá trong năm
    Đây là mẫu số ĐÚNG để báo cáo kết quả backtest.

    Test này KHÔNG sửa spec (spec đã khóa cứng). Nó chỉ đảm bảo sai lệch được ghi nhận
    ở nơi có thể nhìn thấy, thay vì âm thầm dùng 740.
    """
    pf = bt.preflight()
    y21 = pf["universe_per_year"]["2021"]
    assert y21["intersection"] < 700, (
        f"giao điểm thực tế {y21['intersection']} — nếu vượt 700 thì §3.1 (~740) đã đúng trở lại, "
        "hãy kiểm tra lại dữ liệu thay vì sửa test"
    )
    price_only = y21["price"] - y21["intersection"]
    facts_only = y21["facts"] - y21["intersection"]
    assert price_only > 250, f"phải có nhiều mã giá-không-cơ-bản, được {price_only}"
    assert facts_only > 100, f"phải có mã cơ-bản-không-đủ-giá, được {facts_only}"


def test_preflight_bao_discrepancy_chi_phi(bt):
    """Runner phải NÊU mâu thuẫn 0.50% (spec) vs itemize 0.60%, không tự chọn thay."""
    notes = " ".join(bt.preflight()["notes"])
    assert "0.60%" in notes and "0.50%" in notes and "variant B" in notes


def test_preflight_nhan_dien_thieu_module(bt):
    pf = bt.preflight()
    assert pf["ready"] is False
    assert any("trend_engine" in m for m in pf["missing_modules"])
    assert pf["preregistration"]["ok"] is True, "spec v1.1 phải khớp anchor"


def test_runner_dung_chung_nguon_voi_test_hash(bt):
    """Một nguồn sự thật cho việc băm — không cài lại logic LF-normalize lần thứ ba."""
    from src.backtest.preregistration import sha256_lf_normalized

    assert bt.sha256_lf_normalized is sha256_lf_normalized
