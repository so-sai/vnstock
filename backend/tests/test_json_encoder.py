"""test_json_encoder.py — NumpyEncoder / safe_json_dumps (Fault Tolerance).

Bản vá khẩn cấp cho JSON Serialization: hệ sinh thái Quant (numpy/pandas) sinh
ra np.bool_/np.integer/np.floating/np.ndarray mà json.dumps mặc định KHÔNG
serialize được → ném TypeError, có thể làm SẬP chu trình EOD tự động.

Kiểm chứng lớp encoder tập trung tại db_core (nguồn sự thật duy nhất):
  - Mọi kiểu numpy scalar/array được ép về JSON thuần.
  - NaN/Inf (kể cả trong np.ndarray & np.float64 subclass của float) → null
    hợp lệ (RFC 8259), không làm hỏng file log.
  - Roundtrip JSON hợp lệ strict (từ chối token NaN/Infinity).
  - healing_illusion (np.bool_) — điểm nghẽn gốc — serialize thành công.

Run: python -m pytest backend/tests/test_json_encoder.py -v
"""
import json

import numpy as np
import pytest

from src.database.db_core import NumpyEncoder, safe_json_dumps, safe_json_dump


def _strict_loads(s):
    """Parse JSON, TỪ CHỐI NaN/Infinity (RFC 8259 strict)."""
    def _reject(tok):
        raise ValueError(f"Non-strict JSON token: {tok}")
    return json.loads(s, parse_constant=_reject)


# ==================================================== NUMPY SCALARS
class TestNumpyScalars:
    def test_np_bool(self):
        assert _strict_loads(safe_json_dumps({"x": np.bool_(True)}))["x"] is True
        assert _strict_loads(safe_json_dumps({"x": np.bool_(False)}))["x"] is False

    def test_np_integer_widths(self):
        for t in (np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint32):
            out = _strict_loads(safe_json_dumps({"x": t(7)}))
            assert out["x"] == 7
            assert isinstance(out["x"], int)

    def test_np_floating(self):
        out = _strict_loads(safe_json_dumps({"x": np.float64(1.25), "y": np.float32(2.5)}))
        assert out["x"] == pytest.approx(1.25)
        assert out["y"] == pytest.approx(2.5)

    def test_np_datetime64(self):
        out = _strict_loads(safe_json_dumps({"d": np.datetime64("2099-01-04")}))
        assert "2099-01-04" in out["d"]


# ==================================================== NAN / INF (RFC 8259)
class TestNanInf:
    def test_np_float_nan_becomes_null(self):
        out = _strict_loads(safe_json_dumps({"x": np.float64("nan")}))
        assert out["x"] is None

    def test_python_float_nan_becomes_null(self):
        out = _strict_loads(safe_json_dumps({"x": float("nan")}))
        assert out["x"] is None

    def test_inf_becomes_null(self):
        out = _strict_loads(safe_json_dumps({"a": float("inf"), "b": float("-inf")}))
        assert out["a"] is None and out["b"] is None

    def test_nan_inside_ndarray_becomes_null(self):
        out = _strict_loads(safe_json_dumps({"arr": np.array([1.0, np.nan, np.inf, 3.0])}))
        assert out["arr"] == [1.0, None, None, 3.0]


# ==================================================== NDARRAY
class TestNdarray:
    def test_1d_array(self):
        out = _strict_loads(safe_json_dumps({"a": np.array([1, 2, 3])}))
        assert out["a"] == [1, 2, 3]

    def test_2d_array(self):
        out = _strict_loads(safe_json_dumps({"m": np.array([[1, 2], [3, 4]])}))
        assert out["m"] == [[1, 2], [3, 4]]

    def test_float_array(self):
        out = _strict_loads(safe_json_dumps({"a": np.array([1.5, 2.5])}))
        assert out["a"] == [1.5, 2.5]


# ==================================================== NESTED / MIXED
class TestNested:
    def test_deeply_nested_mixed_types(self):
        payload = {
            "flag": np.bool_(True),
            "n": np.int64(42),
            "level1": {
                "arr": np.array([np.nan, 1.0]),
                "level2": [{"v": np.float32(3.14)}, {"b": np.bool_(False)}],
            },
        }
        out = _strict_loads(safe_json_dumps(payload))
        assert out["flag"] is True
        assert out["n"] == 42
        assert out["level1"]["arr"] == [None, 1.0]
        assert out["level1"]["level2"][0]["v"] == pytest.approx(3.14, rel=1e-4)
        assert out["level1"]["level2"][1]["b"] is False


# ==================================================== ROOT CAUSE
class TestHealingIllusionRootCause:
    def test_raw_np_bool_crashes_default_json(self):
        """Tái hiện điểm nghẽn: np.bool_ làm json.dumps mặc định NÉM TypeError."""
        heal = np.float64(0.5) > 0.0 and np.float64(0.4) > 0.3
        assert isinstance(heal, np.bool_)
        with pytest.raises(TypeError):
            json.dumps({"healing_illusion": heal})

    def test_safe_dumps_handles_healing_illusion(self):
        """safe_json_dumps serialize thành công np.bool_ healing_illusion."""
        heal = np.float64(0.5) > 0.0 and np.float64(0.4) > 0.3
        out = _strict_loads(safe_json_dumps({"healing_illusion": heal}))
        assert out["healing_illusion"] is True

    def test_delta_divergence_output_is_json_safe(self):
        """Bản vá cục bộ: healing_illusion là bool THUẦN (không np.bool_)."""
        # Mô phỏng đúng phép so sánh tại delta_divergence.py (đã bọc bool()):
        #   is_healing_illusion = bool(delta_sa > 0.0 and dS_dt > 0.3)
        delta_sa = np.float64(0.8) - np.float64(0.2) * np.float64(1.5)
        dS_dt = np.float64(0.5)
        is_healing_illusion = bool(delta_sa > 0.0 and dS_dt > 0.3)
        assert isinstance(is_healing_illusion, bool)
        assert not isinstance(is_healing_illusion, np.bool_)
        result = {"healing_illusion": is_healing_illusion,
                  "delta_sa": round(float(delta_sa), 4)}
        _strict_loads(safe_json_dumps(result))  # không ném TypeError


# ==================================================== FILE DUMP
class TestFileDump:
    def test_safe_json_dump_to_file(self, tmp_path):
        p = tmp_path / "report.json"
        payload = {"w1": np.float64(1.23), "flag": np.bool_(True),
                   "arr": np.array([np.nan, 2.0])}
        with open(p, "w", encoding="utf-8") as f:
            safe_json_dump(payload, f, indent=2)
        loaded = _strict_loads(p.read_text(encoding="utf-8"))
        assert loaded["w1"] == pytest.approx(1.23)
        assert loaded["flag"] is True
        assert loaded["arr"] == [None, 2.0]


# ==================================================== ENCODER CLASS DIRECT
class TestEncoderClass:
    def test_cls_usage_matches_helper(self):
        payload = {"x": np.int64(9), "y": np.bool_(True)}
        via_cls = json.dumps(payload, cls=NumpyEncoder)
        assert json.loads(via_cls) == {"x": 9, "y": True}
