"""test_backfill_engine.py — TDD: _fetch_lich_su source_info contract.

Bug 06/08/2026: khi VCI TRẢ DỮ LIỆU thành công (VD DMX), `source_info`
thiếu key 'vci_silent_throttle' (chỉ được set khi VCI empty + bluechip)
→ KeyError ở backfill() → retry 3 lần → fail dù có dữ liệu.
Fix: init đầy đủ 4 keys trong `_fetch_lich_su`.
"""

import pandas as pd
import pytest
from src.engine.backfill_engine import _fetch_lich_su


class _FakeProvider:
    """Giả lập VnstockProvider theo source để không gọi network."""

    def __init__(self, source=None, *args, **kwargs):
        self.source = source

    def history(self, symbol, start=None, end=None, pause=0):
        if symbol == "DMX" and self.source == "vci":
            return pd.DataFrame(
                {
                    "time": ["2026-08-06"],
                    "open": [82.0],
                    "high": [83.0],
                    "low": [80.3],
                    "close": [82.0],
                    "volume": [1914000],
                }
            )
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def _fake_provider(monkeypatch):
    monkeypatch.setattr("src.providers.vnstock_provider.VnstockProvider", _FakeProvider)


class TestSourceInfoContract:
    def test_vci_success_co_du_4_keys(self):
        """VCI trả dữ liệu (case DMX) → source_info phải có vci_silent_throttle."""
        df, info = _fetch_lich_su("DMX", "2026-08-01", "2026-08-06")
        assert not df.empty
        assert info["source"] == "vci"
        assert info["vci_silent_throttle"] is False
        assert info["vci_empty"] is False
        assert info["vci_timeout"] is False

    def test_moi_truong_hop_co_du_4_keys(self):
        """Mọi đường đi đều trả source_info đủ 4 keys (không KeyError)."""
        _, info = _fetch_lich_su("KHONG_TON_TAI", "2026-08-01", "2026-08-06")
        for key in ("source", "vci_timeout", "vci_empty", "vci_silent_throttle"):
            assert key in info

    def test_data_dung_scale(self):
        """Dữ liệu giữ nguyên giá trị gốc từ provider (normalize ở read-time)."""
        df, _ = _fetch_lich_su("DMX", "2026-08-01", "2026-08-06")
        assert float(df.iloc[0]["close"]) == 82.0
