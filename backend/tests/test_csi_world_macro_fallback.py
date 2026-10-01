"""test_csi_world_macro_fallback.py — `_get_world` fallback sang macro_history.

WHY (audit 2026-10-01, cụm 2):
  - fed_policy_cache.json TTL 1h: cache quá hạn từng trả None → status()
    trả DEFAULT 0.0 → _world_nodes render "N/A" cho DXY_USD/US10Y_YIELD.
  - aad3790 đã sửa sensor (stale cache được dùng lại) → cache CÒN FILE thì
    không còn N/A.
  - Lỗ remaining: file cache THIẾU/HỎI (clone mới, xóa data, lỗi JSON) →
    status() trả defaults 0.0 → N/A trở lại, dù screener_cache.db
    (macro_history) có đủ DXY/US10Y làm nguồn EOD canonical.
  - Fix: `_get_world` backfill field falsy từ macro_history (giữ nguyên giá
    trị cache thật — không bao giờ ghi đè nguồn có sẵn).

Run:  python -m pytest tests/test_csi_world_macro_fallback.py -q   (từ backend/)
"""

import sqlite3

import pytest

from src.governor.csi_explain import DATA_DIR, CSIExplainEngine


def _macro_latest(variable: str) -> float | None:
    """Đọc trực tiếp macro_history — nguồn sự thật cho kỳ vọng test."""
    con = sqlite3.connect(str(DATA_DIR / "screener_cache.db"))
    try:
        row = con.execute(
            "SELECT value FROM macro_history WHERE variable = ? AND value IS NOT NULL ORDER BY date DESC LIMIT 1",
            (variable,),
        ).fetchone()
    finally:
        con.close()
    return float(row[0]) if row else None


def _world_with(**overrides) -> dict:
    base = {
        "fed_target_rate": 5.5,
        "fomc_dissent": 0,
        "qt_balance_tr": 0.0,
        "reserves_tr": 0.0,
        "us10y_yield": 0.0,
        "usd_index": 0.0,
        "brent_oil": 0.0,
        "implied_hike_prob": 0.0,
        "next_meeting": "unknown",
        "fed_uncertainty": 0.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def world_status_defaults(monkeypatch):
    """WorldSensor.status() trả defaults (cache file thiếu/hỏi)."""
    from src.sensors.world_sensor import WorldSensor

    def _status(self, *args, **kwargs):
        return _world_with()

    monkeypatch.setattr(WorldSensor, "status", _status)


class TestMacroHistoryFallback:
    def test_fallback_fills_dxy_us10y_when_cache_missing(self, world_status_defaults):
        """Cache defaults 0.0 → backfill từ macro_history (không còn N/A)."""
        w = CSIExplainEngine()._get_world()
        assert w["usd_index"] == pytest.approx(_macro_latest("DXY"))
        assert w["us10y_yield"] == pytest.approx(_macro_latest("US10Y"))
        assert w["usd_index"] > 0
        assert w["us10y_yield"] > 0

    def test_world_nodes_display_not_na(self, world_status_defaults):
        """_world_nodes render giá trị, không render 'N/A' cho DXY/US10Y."""
        nodes = {n["node"]: n for n in CSIExplainEngine()._world_nodes()}
        assert nodes["DXY_USD"]["display"] != "N/A"
        assert nodes["US10Y_YIELD"]["display"] != "N/A"
        assert nodes["DXY_USD"]["value"] == pytest.approx(_macro_latest("DXY"))
        assert nodes["US10Y_YIELD"]["value"] == pytest.approx(_macro_latest("US10Y"))

    def test_real_cache_values_win_over_history(self, monkeypatch):
        """Giá trị cache THẬT (truthy) không bao giờ bị macro_history ghi đè.

        Phân biệt được: cache 101.38 khác history 101.378998 (dấu thập phân).
        """
        from src.sensors.world_sensor import WorldSensor

        def _status(self, *args, **kwargs):
            return _world_with(usd_index=101.38, us10y_yield=5.24)

        monkeypatch.setattr(WorldSensor, "status", _status)
        w = CSIExplainEngine()._get_world()
        assert w["usd_index"] == 101.38
        assert w["us10y_yield"] == 5.24

    def test_history_db_unavailable_keeps_defaults(self, monkeypatch, tmp_path):
        """macro_history DB không mở được → giữ defaults, không ném lỗi."""
        import src.governor.csi_explain as ce
        from src.sensors.world_sensor import WorldSensor

        def _status(self, *args, **kwargs):
            return _world_with()

        monkeypatch.setattr(WorldSensor, "status", _status)
        monkeypatch.setattr(ce, "DATA_DIR", tmp_path)  # screener_cache.db không tồn tại
        w = CSIExplainEngine()._get_world()
        assert w["usd_index"] == 0.0
        assert w["us10y_yield"] == 0.0
