"""Property-based tests (Hypothesis) — Tự động hóa sinh kịch bản biên cho hạ tầng toán học.

Phân định vai trò (2026-08-05): Property-based testing KHÔNG thay thế unit tests nghiệp vụ
mà tự động hóa việc phát sinh các case biên (boundary/edge) mà người viết test thủ công
dễ bỏ sót. Thay vì hard-code vài giá trị, ta khai báo INVARIANT (bất biến) và để
Hypothesis quét toàn không gian đầu vào hợp lệ.

4 quy tắc bất biến được kiểm định:
  1. _decay_factor:      đơn điệu giảm, kẹp chặt [0.0, 1.0], tuyến tính trong window,
                         == 0.0 khi days_remaining <= 0, == 1.0 khi >= window.
  2. _transmission_factor: đơn điệu tăng theo elapsed_days, bị chặn [0.0, 1.0),
                         == 0.0 khi elapsed <= 0, == 0.5 khi elapsed == half_life.
  3. _vn20_gate:         phân nhánh an toàn (bank CAPITAL_RATIO / non-bank D/E)
                         so với reference oracle cho mọi NIM_q / CAPITAL_RATIO ngẫu nhiên.
  4. policy_cap_boost:   đầu ra luôn trong [0.0, 0.15] với mọi ldr_relief_bps.

Run: python -m pytest backend/tests/test_policy_properties.py -v
"""

import sqlite3
from itertools import count
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.governor.policy_impact_engine import (
    PolicyEvent,
    PolicyImpactEngine,
)

# ── Các bất biến của PolicyImpactEngine ────────────────────────────────────


class TestDecayFactorProperties:
    """Bất biến của _decay_factor (Policy Cliff Audit)."""

    @given(st.integers(min_value=-365, max_value=1000), st.integers(min_value=1, max_value=365))
    @settings(max_examples=200)
    def test_clamped_01(self, days_remaining, window):
        f = PolicyImpactEngine._decay_factor(days_remaining, window)
        assert 0.0 <= f <= 1.0

    @given(st.integers(min_value=-365, max_value=0), st.integers(min_value=1, max_value=365))
    @settings(max_examples=100)
    def test_zero_when_expired(self, days_remaining, window):
        assert PolicyImpactEngine._decay_factor(days_remaining, window) == 0.0

    @given(st.integers(min_value=1, max_value=365), st.integers(min_value=1, max_value=365))
    @settings(max_examples=100)
    def test_full_in_plateau(self, days_remaining, window):
        """days_remaining >= window -> plateau 1.0."""
        if days_remaining < window:
            return
        assert PolicyImpactEngine._decay_factor(days_remaining, window) == 1.0

    @given(st.integers(min_value=1, max_value=364), st.integers(min_value=2, max_value=365))
    @settings(max_examples=200)
    def test_linear_in_window(self, days_remaining, window):
        """0 < days_remaining < window -> tuyến tính days/window."""
        if days_remaining >= window:
            return
        assert PolicyImpactEngine._decay_factor(days_remaining, window) == days_remaining / window

    @given(
        st.integers(min_value=0, max_value=1000),
        st.integers(min_value=1, max_value=365),
        st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=200)
    def test_monotonic_decreasing(self, d1, window, d2):
        """days_remaining tăng (càng xa expiry) thì f không giảm."""
        f1 = PolicyImpactEngine._decay_factor(d1, window)
        f2 = PolicyImpactEngine._decay_factor(d2, window)
        if d1 <= d2:
            assert f1 <= f2
        else:
            assert f1 >= f2


class TestTransmissionFactorProperties:
    """Bất biến của _transmission_factor (LAW-009)."""

    @given(
        st.integers(min_value=-365, max_value=1000),
        st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_bounded_01(self, elapsed, half_life):
        f = PolicyImpactEngine._transmission_factor(elapsed, half_life)
        # Chặn trên tiệm cận 1.0; float làm tròn 1-0.5^huge -> 1.0, nên khẳng định <= 1.0
        assert 0.0 <= f <= 1.0

    @given(
        st.integers(min_value=-365, max_value=0),
        st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_zero_before_effective(self, elapsed, half_life):
        assert PolicyImpactEngine._transmission_factor(elapsed, half_life) == 0.0

    @given(
        st.integers(min_value=1, max_value=365),
        st.floats(min_value=1.0, max_value=365.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_half_life_reaches_half(self, elapsed, half_life):
        """elapsed == half_life -> 0.5 (đã truyền nửa tín hiệu)."""
        if elapsed != half_life:
            return
        assert PolicyImpactEngine._transmission_factor(elapsed, half_life) == 0.5

    @given(
        st.integers(min_value=-365, max_value=1000),
        st.integers(min_value=-365, max_value=1000),
        st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_monotonic_increasing(self, e1, e2, half_life):
        """elapsed tăng -> f không giảm."""
        f1 = PolicyImpactEngine._transmission_factor(e1, half_life)
        f2 = PolicyImpactEngine._transmission_factor(e2, half_life)
        if e1 <= e2:
            assert f1 <= f2
        else:
            assert f1 >= f2


class TestVn20GateBranchingProperties:
    """Bất biến phân nhánh của _vn20_gate — reference oracle đối chiếu.

    Với MỌI bộ giá trị (ROE_q, CAPITAL_RATIO, NIM_q, NPL, D/E, volume) ngẫu nhiên
    trong lưới ngưỡng, kết quả gate phải khớp chính xác reference oracle:
      - Bank (có CAPITAL_RATIO): cap>0.05 AND (NIM None OR NIM*4>0.018)
                                  AND (NPL None OR NPL<0.030)
      - Non-bank có D/E:          de<2.0
      - Không dữ liệu leverage:   bypass
      - Chung: ROE_q*4 > 0.10 AND volume > 50000
    """

    def setup_method(self):
        from src.backtest.unified_system_replay import _date_to_period, _vn20_gate

        self._gate = _vn20_gate
        self._period = _date_to_period
        self._case = count()
        # SQLite in-memory — loại bỏ hoàn toàn I/O đĩa (Windows file-lock deadlock).
        # ATTACH ':memory:' tạo DB in-memory thứ 2 gắn alias 'fin' trên cùng connection.
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        self.conn.execute("ATTACH DATABASE ':memory:' AS fin")
        self.conn.execute("CREATE TABLE fin.health_ratios (symbol TEXT, ratio_name TEXT, ratio_value REAL, period TEXT)")

    def teardown_method(self):
        self.conn.close()

    @staticmethod
    def _reference_oracle(roe_q, cap, nim_q, npl, de, volume):
        """Spec chuẩn — nguồn chân lý để gate phải khớp."""
        roe_annual = roe_q * 4
        if cap is not None:
            cap_ok = cap > 0.05
            nim_ann = nim_q * 4 if nim_q is not None else None
            nim_ok = nim_ann > 0.018 if nim_ann is not None else True
            npl_ok = npl < 0.030 if npl is not None else True
            leverage_ok = cap_ok and nim_ok and npl_ok
        elif de is not None:
            leverage_ok = de < 2.0
        else:
            leverage_ok = True
        return roe_annual > 0.10 and leverage_ok and (volume or 0) > 50000

    @given(
        st.sampled_from([0.02, 0.03, 0.04]),  # ROE quarterly (ann: 8/12/16%)
        st.one_of(st.none(), st.sampled_from([0.04, 0.06, 0.08])),  # CAPITAL_RATIO
        st.one_of(st.none(), st.sampled_from([0.004, 0.005, 0.0111])),  # NIM quarterly
        st.one_of(st.none(), st.sampled_from([0.02, 0.035])),  # NPL
        st.one_of(st.none(), st.sampled_from([1.5, 3.0])),  # D/E
        st.sampled_from([40000, 100000]),  # volume
    )
    @settings(max_examples=25, deadline=None)
    def test_gate_matches_oracle(self, roe_q, cap, nim_q, npl, de, volume):
        symbol = f"PROP_{next(self._case)}"
        self.conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?, ?, 100, 110, 90, 100, ?)",
            (symbol, "2026-08-05", volume),
        )
        for name, val in [("ROE", roe_q), ("CAPITAL_RATIO", cap), ("NIM", nim_q), ("NPL_RATIO", npl), ("DEBT_TO_EQUITY", de)]:
            if val is None:
                continue
            period = self._period("2026-08-05")
            self.conn.execute(
                "INSERT INTO fin.health_ratios VALUES (?, ?, ?, ?)",
                (symbol, name, val, period),
            )
        self.conn.commit()
        expected = self._reference_oracle(roe_q, cap, nim_q, npl, de, volume)
        assert self._gate(self.conn, symbol, "2026-08-05") is expected


class TestPolicyCapBoostProperties:
    """Bất biến đầu ra của policy_cap_boost: luôn nằm trong [0.0, 0.15]."""

    @given(st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cap_boost_bounded(self, tmp_path, monkeypatch, ldr_bps):
        from src.engine import decision_guard
        from src.governor import policy_impact_engine as pie_mod

        evt = PolicyEvent(
            id="PROP_BOOST",
            title="Property cap boost bound",
            effective_date="2026-01-01",
            expiry_date="2028-12-31",
            event_type="KBNN_LDR_ADJUSTMENT",
            half_life_days=15.0,
            decay_window_days=90,
            clusters={"SOCB_BIG3": 1.0},
            delta_params={"ldr_relief_bps": ldr_bps},
        )
        engine = PolicyImpactEngine(events_path=Path(tmp_path) / "prop_events.json")
        engine._events[evt.id] = evt
        monkeypatch.setattr(pie_mod, "PolicyImpactEngine", lambda *a, **k: engine)

        result = decision_guard.compute_policy_cap_boost("2026-06-01")
        assert 0.0 <= result["cap_boost"] <= 0.15
        for e in result["active_events"]:
            assert 0.0 <= e["cap_boost"] <= 0.15
