"""test_verdict_localization.py — ĐỨNG NGOÀI (STAND ASIDE) display fix.

Mục tiêu: token thô "DUNG NGOAI" / "QUAN SAT" / "THAM GIA" phải được hiển thị
chuẩn tiếng Việt có dấu trên CLI final-decision, không còn "DUNG NGOAI" trần.
"""

from src.core.canonical_output_adapter import _EXTRA_MAP, CLI_LABEL_MAP, localize_label


# ============================================================
# Verdict family — canonical vi (EN) display strings
# ============================================================
class TestVerdictLabelMap:
    """Bảng verdict family phải chứa đủ 6 trạng thái phán quyết."""

    def test_verdict_family_keys_present(self):
        keys = [
            "THAM GIA FULL",
            "THAM GIA",
            "THAM GIA DO",
            "QUAN SAT",
            "GIAM RUI RO",
            "DUNG NGOAI",
        ]
        for k in keys:
            assert k in CLI_LABEL_MAP, f"CLI_LABEL_MAP thiếu verdict: {k}"
            assert k in _EXTRA_MAP, f"_EXTRA_MAP thiếu verdict: {k}"

    def test_dung_ngoai_display(self):
        """DUNG NGOAI phải hiển thị chuẩn: ĐỨNG NGOÀI (STAND ASIDE - 100% CASH)."""
        assert localize_label("DUNG NGOAI", "full") == "ĐỨNG NGOÀI (STAND ASIDE - 100% CASH)"

    def test_quan_sat_display(self):
        assert localize_label("QUAN SAT", "full") == "QUAN SÁT (WATCH / OBSERVE)"

    def test_tham_gia_display(self):
        assert localize_label("THAM GIA", "full") == "THAM GIA (PARTICIPATE)"

    def test_giam_rui_ro_display(self):
        assert localize_label("GIAM RUI RO", "full") == "GIẢM RỦI RO (REDUCE RISK)"

    def test_no_raw_token_leak(self):
        """Giá trị hiển thị không được để lộ token thô 'DUNG NGOAI' trần."""
        for k in ("DUNG NGOAI", "QUAN SAT", "THAM GIA"):
            assert localize_label(k, "full") != k


# ============================================================
# XAI Override Trace — GOVERNOR_DEC line
# ============================================================
class TestXaiOverrideTrace:
    def test_action_vn_dung_ngoai(self):
        from src.utils.xai_override_trace import _ACTION_VN

        assert _ACTION_VN["DUNG NGOAI"] == "ĐỨNG NGOÀI (STAND ASIDE - 100% CASH)"

    def test_action_vn_quan_sat(self):
        from src.utils.xai_override_trace import _ACTION_VN

        assert _ACTION_VN["QUAN SAT"] == "QUAN SÁT (WATCH)"

    def test_action_vn_tham_gia(self):
        from src.utils.xai_override_trace import _ACTION_VN

        assert _ACTION_VN["THAM GIA"] == "THAM GIA (PARTICIPATE)"
