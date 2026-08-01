"""Tests — Sensor Validation Layer (sensor_validation.py).

Covers:
1. record_signal / load_records — upsert + persistence qua screener_cache.db.
2. compute_profile — P(Crisis|Signal), false alarm rate, lead time stats.
3. detect_signal_events — DXY LEVEL + KOSPI/SOX DROP_5D signal detection.
4. label_outcomes — crisis_flag + lead_days gán đúng theo horizon.
5. format_profile / CLI wiring.

WHY: Đây là lớp chống Rule-based Trap — cảm biến thế giới chỉ là Upstream
Evidence với hồ sơ xác suất, không phải luật giao dịch cứng. Test phải đảm bảo
P(Crisis|Signal) và lead time tính đúng từ dữ liệu thực.
"""

import sys
from pathlib import Path

# ── Path Setup ──────────────────────────────────────────────────
PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import pytest

from src.sensors.sensor_validation import (
    SensorSignal,
    compute_profile,
    detect_signal_events,
    format_profile,
    ingest_from_macro_history,
    label_outcomes,
    list_sensors,
    load_records,
    record_signal,
)

TEST_SENSOR = "__TESTKOSPI__"


@pytest.fixture(autouse=True)
def _clean_sensor_data():
    """Dọn dữ liệu test trước/sau — tránh làm bẩn hồ sơ thật."""
    from src.database.db_core import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM sensor_validation WHERE sensor=?", (TEST_SENSOR,))
        conn.commit()
    yield
    with get_connection() as conn:
        conn.execute("DELETE FROM sensor_validation WHERE sensor=?", (TEST_SENSOR,))
        conn.commit()


# ===================================================================
# 1. record_signal / load_records
# ===================================================================

class TestPersistence:
    def test_record_and_load_roundtrip(self):
        row = SensorSignal(
            sensor=TEST_SENSOR,
            signal_date="2026-05-01",
            signal_type="DROP_5D",
            signal_value=-0.08,
            crisis_flag=1,
            lead_days=5,
            horizon_days=20,
        )
        record_signal(row)

        loaded = load_records(TEST_SENSOR)
        assert len(loaded) == 1
        r = loaded[0]
        assert r.sensor == TEST_SENSOR
        assert r.signal_date == "2026-05-01"
        assert r.signal_type == "DROP_5D"
        assert r.crisis_flag == 1
        assert r.lead_days == 5
        assert r.horizon_days == 20

    def test_upsert_same_primary_key(self):
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-01",
            signal_type="DROP_5D", signal_value=-0.08,
            crisis_flag=1, lead_days=5,
        ))
        # Ghi lại cùng (sensor, date, type) với outcome khác → phải ghi đè
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-01",
            signal_type="DROP_5D", signal_value=-0.08,
            crisis_flag=0, lead_days=None,
        ))
        loaded = load_records(TEST_SENSOR)
        assert len(loaded) == 1
        assert loaded[0].crisis_flag == 0

    def test_list_sensors_includes_test_sensor(self):
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-01",
            signal_type="DROP_5D", signal_value=-0.08,
        ))
        sensors = list_sensors()
        assert TEST_SENSOR in sensors

    def test_load_records_filter_by_signal_type(self):
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-01",
            signal_type="DROP_5D", signal_value=-0.08,
        ))
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-02",
            signal_type="STRESS_LEVEL", signal_value=107.0,
        ))
        drop = load_records(TEST_SENSOR, signal_type="DROP_5D")
        assert len(drop) == 1
        assert drop[0].signal_type == "DROP_5D"


# ===================================================================
# 2. compute_profile
# ===================================================================

class TestProfile:
    def _seed(self, records):
        for r in records:
            record_signal(r)

    def test_profile_empty(self):
        prof = compute_profile(TEST_SENSOR)
        assert prof.n == 0
        assert prof.p_crisis == 0.0
        assert prof.false_alarm_rate == 1.0

    def test_profile_p_crisis_and_lead(self):
        # 4 tín hiệu: 3 có crisis (lead 3, 7, 14), 1 không
        self._seed([
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-01-01",
                         signal_type="DROP_5D", signal_value=-0.06,
                         crisis_flag=1, lead_days=3),
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-02-01",
                         signal_type="DROP_5D", signal_value=-0.06,
                         crisis_flag=1, lead_days=7),
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-03-01",
                         signal_type="DROP_5D", signal_value=-0.06,
                         crisis_flag=1, lead_days=14),
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-04-01",
                         signal_type="DROP_5D", signal_value=-0.06,
                         crisis_flag=0, lead_days=None),
        ])
        prof = compute_profile(TEST_SENSOR)
        assert prof.n == 4
        assert prof.n_crisis == 3
        assert prof.p_crisis == pytest.approx(0.75)
        assert prof.false_alarm_rate == pytest.approx(0.25)
        assert prof.lead_days_mean == pytest.approx(8.0)
        assert prof.lead_days_median == pytest.approx(7.0)
        assert prof.lead_days_min == 3
        assert prof.lead_days_max == 14

    def test_profile_by_type(self):
        self._seed([
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-01-01",
                         signal_type="STRESS_LEVEL", signal_value=107.0,
                         crisis_flag=1, lead_days=2),
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-02-01",
                         signal_type="STRESS_LEVEL", signal_value=107.5,
                         crisis_flag=0, lead_days=None),
            SensorSignal(sensor=TEST_SENSOR, signal_date="2026-03-01",
                         signal_type="DROP_5D", signal_value=-0.07,
                         crisis_flag=1, lead_days=5),
        ])
        prof = compute_profile(TEST_SENSOR)
        assert prof.by_type["STRESS_LEVEL"]["n"] == 2
        assert prof.by_type["STRESS_LEVEL"]["p_crisis"] == pytest.approx(0.5)
        assert prof.by_type["DROP_5D"]["n"] == 1
        assert prof.by_type["DROP_5D"]["p_crisis"] == pytest.approx(1.0)

    def test_format_profile_no_data(self):
        prof = compute_profile(TEST_SENSOR)
        text = format_profile(prof)
        assert "Chưa có dữ liệu hồ sơ" in text


# ===================================================================
# 3. detect_signal_events
# ===================================================================

class TestSignalDetection:
    def test_dxy_level_signals(self):
        dates = [f"2026-01-{i:02d}" for i in range(1, 8)]
        # values: dưới stress, trong stress, trên crisis
        values = [105.0, 105.5, 106.5, 108.5, 107.0, 105.0, 104.0]
        events = detect_signal_events("DXY", dates, values)
        types = [e.signal_type for e in events]
        assert "STRESS_LEVEL" in types
        assert "CRISIS_LEVEL" in types
        for e in events:
            assert e.crisis_flag == 0  # chưa label
            assert e.lead_days is None

    def test_kospi_drop_5d(self):
        dates = [f"2026-01-{i:02d}" for i in range(1, 10)]
        values = [3000.0, 3010.0, 2990.0, 2950.0, 2900.0,
                  2850.0, 2800.0, 2790.0, 2780.0]
        # Ngày 6 (index 5): 2850 vs 3000 (5 phiên trước) = -5% → signal
        events = detect_signal_events("KOSPI", dates, values)
        assert len(events) >= 1
        assert all(e.signal_type == "DROP_5D" for e in events)
        assert events[0].signal_date == "2026-01-06"

    def test_unknown_sensor_no_events(self):
        events = detect_signal_events("NOPE", ["2026-01-01"], [1.0])
        assert events == []

    def test_insufficient_data_no_events(self):
        dates = ["2026-01-01", "2026-01-02"]
        events = detect_signal_events("KOSPI", dates, [100.0, 99.0])
        assert events == []


# ===================================================================
# 4. label_outcomes
# ===================================================================

class TestLabelOutcomes:
    def test_crisis_within_horizon_gets_lead(self):
        events = [SensorSignal(
            sensor="DXY", signal_date="2026-05-01",
            signal_type="STRESS_LEVEL", signal_value=107.0,
            horizon_days=20,
        )]
        crisis_dates = ["2026-05-05", "2026-05-10"]
        labeled = label_outcomes(events, crisis_dates)
        assert labeled[0].crisis_flag == 1
        assert labeled[0].lead_days == 4  # 05-01 -> 05-05

    def test_no_crisis_in_horizon(self):
        events = [SensorSignal(
            sensor="DXY", signal_date="2026-05-01",
            signal_type="STRESS_LEVEL", signal_value=107.0,
            horizon_days=20,
        )]
        labeled = label_outcomes(events, ["2026-07-01"])
        assert labeled[0].crisis_flag == 0
        assert labeled[0].lead_days is None

    def test_crisis_before_signal_ignored(self):
        events = [SensorSignal(
            sensor="DXY", signal_date="2026-05-10",
            signal_type="STRESS_LEVEL", signal_value=107.0,
            horizon_days=20,
        )]
        labeled = label_outcomes(events, ["2026-05-01"])
        assert labeled[0].crisis_flag == 0

    def test_crisis_beyond_horizon_ignored(self):
        events = [SensorSignal(
            sensor="DXY", signal_date="2026-05-01",
            signal_type="STRESS_LEVEL", signal_value=107.0,
            horizon_days=20,
        )]
        # Crisis cách 40 phiên (> horizon 20)
        labeled = label_outcomes(events, ["2026-06-10"])
        assert labeled[0].crisis_flag == 0


# ===================================================================
# 5. ingest_from_macro_history
# ===================================================================

class TestIngest:
    def _seed_macro(self, variable, points):
        """Bom dữ liệu macro_history cho một biến test."""
        from src.database.db_core import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM macro_history WHERE variable=?", (variable,))
            for d, v in points:
                conn.execute(
                    "INSERT OR REPLACE INTO macro_history (variable, date, value) "
                    "VALUES (?,?,?)", (variable, d, v),
                )
            conn.commit()

    def _seed_regime(self, crisis_dates):
        """Bom regime_history với status CRISIS cho các ngày cho trước."""
        from src.database.db_core import get_connection
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM regime_history WHERE status IN ('CRISIS','CRISIS_WARNING') "
                "AND date LIKE '2999-%'"
            )
            for d in crisis_dates:
                conn.execute(
                    "INSERT OR REPLACE INTO regime_history (date, status) VALUES (?,?)",
                    (d, "CRISIS"),
                )
            conn.commit()

    def _cleanup(self):
        from src.database.db_core import get_connection
        with get_connection() as conn:
            conn.execute("DELETE FROM sensor_validation WHERE sensor=?", (TEST_SENSOR,))
            conn.execute("DELETE FROM macro_history WHERE variable=?", (TEST_SENSOR,))
            conn.execute(
                "DELETE FROM regime_history WHERE status='CRISIS' "
                "AND date LIKE '2999-%'"
            )
            conn.commit()

    def test_ingest_pipeline_and_profile(self, monkeypatch):
        # KOSPI-like DROP_5D: giá giảm >5% trong 5 phiên tại nhiều mốc
        var = TEST_SENSOR
        import src.sensors.sensor_validation as sv
        monkeypatch.setitem(
            sv.SENSOR_SIGNAL_DEFS, TEST_SENSOR,
            {"type": "DROP_5D", "threshold_pct": 0.05},
        )
        self._seed_macro(var, [
            ("2999-01-01", 3000.0),
            ("2999-01-02", 3010.0),
            ("2999-01-03", 2990.0),
            ("2999-01-04", 2950.0),
            ("2999-01-05", 2900.0),   # -3.3% so 5 phiên trước? index 5/0
            ("2999-01-06", 2850.0),   # (2850-3000)/3000 = -5% → signal
            ("2999-01-07", 2800.0),
            ("2999-01-08", 2790.0),
            ("2999-01-09", 2780.0),   # (2780-2990)/2990 = -7% → signal
        ])
        # Crisis ngay sau tín hiệu đầu (1 phiên) → lead=1; không crisis sau tín hiệu 2
        self._seed_regime([
            "2999-01-07",   # crisis 1 phiên sau signal #1 (01-06)
        ])
        try:
            n = ingest_from_macro_history(
                sensor=TEST_SENSOR, variable=var, horizon_days=20,
                require_full_horizon=False,
            )
            # 4 tín hiệu DROP_5D: 01-06, 01-07, 01-08, 01-09
            # crisis 01-07 → signal 01-06 (lead=1), 01-07 (lead=0) flag=1;
            # 01-08/01-09 có crisis 01-07 ở TRƯỚC → flag=0
            assert n == 4
            prof = compute_profile(TEST_SENSOR, horizon_days=20)
            assert prof.n == 4
            assert prof.n_crisis == 2
            assert prof.p_crisis == pytest.approx(0.5)
        finally:
            self._cleanup()

    def test_ingest_requires_full_horizon_drops_recent(self, monkeypatch):
        # Tín hiệu cuối gần max_date của crisis → require_full_horizon loại bỏ
        var = TEST_SENSOR
        import src.sensors.sensor_validation as sv
        monkeypatch.setitem(
            sv.SENSOR_SIGNAL_DEFS, TEST_SENSOR,
            {"type": "DROP_5D", "threshold_pct": 0.05},
        )
        self._seed_macro(var, [
            ("2999-02-01", 3000.0),
            ("2999-02-02", 3010.0),
            ("2999-02-03", 2990.0),
            ("2999-02-04", 2950.0),
            ("2999-02-05", 2900.0),
            ("2999-02-06", 2850.0),   # -5% → signal
        ])
        # max_known = 2999-02-08; signal 02-06 + 20 phiên > 02-08 → drop
        self._seed_regime(["2999-02-07"])
        try:
            n = ingest_from_macro_history(
                sensor=TEST_SENSOR, variable=var, horizon_days=20,
                require_full_horizon=True,
            )
            assert n == 0, "Tín hiệu chưa đóng cửa sổ phải bị loại (chống bias)"

            # Không full-horizon → vẫn record được
            n2 = ingest_from_macro_history(
                sensor=TEST_SENSOR, variable=var, horizon_days=20,
                require_full_horizon=False,
            )
            assert n2 == 1
        finally:
            self._cleanup()

    def test_ingest_unknown_sensor_returns_zero(self):
        assert ingest_from_macro_history(sensor="NOT_REAL", horizon_days=20) == 0


# ===================================================================
# 6. CLI wiring (smoke)
# ===================================================================

class TestCliWiring:
    def test_cmd_sensor_profile_smoke(self, capsys):
        """ptck.py sensor-profile --sensor <test> không crash khi rỗng."""
        record_signal(SensorSignal(
            sensor=TEST_SENSOR, signal_date="2026-05-01",
            signal_type="DROP_5D", signal_value=-0.08,
            crisis_flag=1, lead_days=4,
        ))
        import subprocess
        import sys as _sys
        proc = subprocess.run(
            [_sys.executable, "ptck.py", "sensor-profile",
             "--sensor", TEST_SENSOR],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "SENSOR PROFILE" in out
        assert TEST_SENSOR in out
