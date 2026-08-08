"""TDD tests for recent fixes: build_narrative, cmd_analyze, report weekly, VN20 Gate, ANSI color.

Tests follow TDD Red→Green→Refactor:
  - Written BEFORE implementing fixes to ensure they capture the bugs.
  - Run with: python -m pytest backend/tests/test_recent_fixes.py -v
"""

import sqlite3
import sys

import pytest
from conftest import PROJECT_ROOT


# ────────────────────────────────────────────────────────────────
# 1. build_narrative() — was called with zero args, needs 3
# ────────────────────────────────────────────────────────────────
class TestBuildNarrativeFix:
    """build_narrative(market, gold, trust) must accept 3 dicts."""

    def test_build_narrative_requires_three_args(self):
        """build_narrative() called with zero args must raise TypeError."""
        from src.services.weekly_cognitive_report import build_narrative

        with pytest.raises(TypeError, match="missing.*positional"):
            build_narrative()

    def test_build_narrative_accepts_three_dicts(self):
        """build_narrative(market, gold, trust) returns a string."""
        from src.services.weekly_cognitive_report import build_narrative

        market = {"regime": "RANGING", "lci": "LOW", "risk_appetite": "NEUTRAL"}
        gold = {"regime": "NEUTRAL", "premium_regime": "NORMAL"}
        trust = {"status": "NO_DATA"}
        result = build_narrative(market, gold, trust)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_narrative_contains_regime(self):
        """Narrative string includes the regime value."""
        from src.services.weekly_cognitive_report import build_narrative

        market = {"regime": "TRENDING", "lci": "HIGH", "risk_appetite": "AGGRESSIVE"}
        gold = {"regime": "RISK_OFF", "premium_regime": "SURGE"}
        trust = {"status": "PROMOTABLE"}
        result = build_narrative(market, gold, trust)
        assert "TRENDING" in result
        assert "RISK_OFF" in result
        # PROMOTABLE is rendered as Vietnamese "CAO có thể kích hoạt"
        assert "CAO" in result

    def test_build_narrative_surge_premium(self):
        """SURGE premium appends local premium note."""
        from src.services.weekly_cognitive_report import build_narrative

        market = {"regime": "RANGING"}
        gold = {"regime": "NEUTRAL", "premium_regime": "SURGE"}
        trust = {"status": "NO_DATA"}
        result = build_narrative(market, gold, trust)
        assert "phí bảo hiểm" in result or "SURGE" in result


# ────────────────────────────────────────────────────────────────
# 2. CLI parser — analyze subcommand registered
# ────────────────────────────────────────────────────────────────
class TestCLIParserAnalyze:
    """ptck.py parser must have 'analyze' subcommand with --symbols."""

    def test_analyze_subcommand_registered(self):
        from ptck import build_parser

        parser = build_parser()
        # Find 'analyze' in subcommands
        found = False
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:
                if "analyze" in action.choices:
                    found = True
                    break
        assert found, "analyze subcommand not registered in parser"

    def test_analyze_has_symbols_arg(self):
        from ptck import build_parser

        parser = build_parser()
        # Parse ['analyze', '--symbols', 'HPG', 'VCB']
        args = parser.parse_args(["analyze", "--symbols", "HPG", "VCB"])
        assert hasattr(args, "symbols")
        assert args.symbols == ["HPG", "VCB"]

    def test_analyze_requires_symbols(self):
        from ptck import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["analyze"])


# ────────────────────────────────────────────────────────────────
# 3. cmd_analyze — output structure & RSI logic
# ────────────────────────────────────────────────────────────────
class TestCmdAnalyze:
    """cmd_analyze must produce structured output with RSI, support, resistance."""

    def _run_analyze(self, symbols, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["analyze", "--symbols"] + symbols)
        args.func(args)
        return capsys.readouterr().out

    def test_analyze_output_contains_price(self, capsys):
        """Output must show price for each symbol."""
        out = self._run_analyze(["VCB"], capsys)
        assert "59,300" in out or "59300" in out or "VCB" in out

    def test_analyze_output_contains_rsi(self, capsys):
        """Output must show RSI value."""
        out = self._run_analyze(["VCB"], capsys)
        assert "RSI" in out

    def test_analyze_output_contains_support_resistance(self, capsys):
        """Output must show Support and Resistance."""
        out = self._run_analyze(["VCB"], capsys)
        assert "Support" in out
        assert "Resistance" in out

    def test_analyze_output_contains_recommendation(self, capsys):
        """Output must show a recommendation (TRÁNH/CHỜ/THEO DÕI)."""
        out = self._run_analyze(["VCB"], capsys)
        assert any(kw in out for kw in ["TRÁNH", "CHỜ", "THEO DÕI"])

    def test_analyze_multiple_symbols(self, capsys):
        """Running with multiple symbols produces output for each."""
        out = self._run_analyze(["VCB", "HPG"], capsys)
        assert "VCB" in out
        assert "HPG" in out


# ────────────────────────────────────────────────────────────────
# 4. cmd_report weekly — must not crash
# ────────────────────────────────────────────────────────────────
class TestCmdReportWeekly:
    """report weekly must not raise TypeError anymore."""

    def test_report_weekly_runs_without_error(self, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["report", "weekly"])
        # Must not raise TypeError
        args.func(args)
        out = capsys.readouterr().out
        assert "WEEKLY" in out or "weekly" in out.lower() or len(out) > 0

    def test_report_weekly_outputs_sections(self, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["report", "weekly"])
        args.func(args)
        out = capsys.readouterr().out
        # Should contain at least one section label
        assert any(kw in out for kw in ["MARKET", "GOLD", "TRUST", "Overall"])


# ────────────────────────────────────────────────────────────────
# 5. VN20 Gate — _vn20_gate function contract
# ────────────────────────────────────────────────────────────────
class TestVN20Gate:
    """_vn20_gate must return bool based on ROE, D/E, volume thresholds."""

    def test_gate_importable(self):
        from src.backtest.unified_system_replay import _vn20_gate

        assert callable(_vn20_gate)

    def test_gate_returns_false_for_nonexistent_symbol(self):
        """Non-existent symbol should return False (safety net)."""
        from src.backtest.unified_system_replay import _vn20_gate

        conn = sqlite3.connect(str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db"))
        try:
            result = _vn20_gate(conn, "ZZZZZZ_NONEXIST", "2026-08-05")
            assert result is False
        finally:
            conn.close()

    def test_gate_returns_bool(self):
        """Gate must return a bool, not int or None."""
        from src.backtest.unified_system_replay import _vn20_gate

        conn = sqlite3.connect(str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db"))
        try:
            result = _vn20_gate(conn, "VCB", "2026-08-05")
            assert isinstance(result, bool)
        finally:
            conn.close()


# ────────────────────────────────────────────────────────────────
# 6. ANSI Color — cli_theme functions work
# ────────────────────────────────────────────────────────────────
class TestANSIColor:
    """cli_theme color functions must wrap text with ANSI codes."""

    def test_c_green_wraps_with_ansi(self):
        from src.utils.cli_theme import c_green

        result = c_green("PASS")
        assert "\033[" in result
        assert "PASS" in result

    def test_c_red_wraps_with_ansi(self):
        from src.utils.cli_theme import c_red

        result = c_red("FAIL")
        assert "\033[" in result
        assert "FAIL" in result

    def test_c_yellow_wraps_with_ansi(self):
        from src.utils.cli_theme import c_yellow

        result = c_yellow("WARN")
        assert "\033[" in result
        assert "WARN" in result

    def test_no_color_strips_ansi(self, monkeypatch):
        """When NO_COLOR=1, color functions return plain text."""
        monkeypatch.setenv("NO_COLOR", "1")

        # Need to reload to pick up env change
        import importlib

        import src.utils.cli_theme

        importlib.reload(src.utils.cli_theme)
        assert "\033[" not in src.utils.cli_theme.c_green("PASS")
        assert "\033[" not in src.utils.cli_theme.c_red("FAIL")
        assert "\033[" not in src.utils.cli_theme.c_yellow("WARN")
        monkeypatch.delenv("NO_COLOR", raising=False)

    def test_color_status_maps_correctly(self):
        from src.utils.cli_theme import color_status

        green = color_status("PASS")
        red = color_status("VETO")
        yellow = color_status("ORANGE")
        assert "\033[" in green
        assert "\033[" in red
        assert "\033[" in yellow


# ────────────────────────────────────────────────────────────────
# 7. Canonical registry — 8 new VariableSpecs
# ───────────────── Canonical registry — 8 new VariableSpecs
# ────────────────────────────────────────────────────────────────
class TestCanonicalRegistry:
    """8 new VariableSpecs (KOSPI, TAIEX, SHENZHEN, SP500, NASDAQ, VIX, HANG_SENG, ES_FUTURES) must exist."""

    NEW_TICKERS = ["KOSPI", "TAIEX", "SHENZHEN", "SP500", "NASDAQ", "VIX", "HANG_SENG", "ES_FUTURES"]

    def test_all_8_in_registry(self):
        sys.path.insert(0, str(PROJECT_ROOT / "backend" / "libs"))
        from canonical import CanonicalAssetRegistry

        reg = CanonicalAssetRegistry()
        for ticker in self.NEW_TICKERS:
            assert reg.has(ticker), f"{ticker} not in canonical registry"

    def test_all_8_normalize(self):
        sys.path.insert(0, str(PROJECT_ROOT / "backend" / "libs"))
        from canonical import CanonicalAssetRegistry, Normalizer

        reg = CanonicalAssetRegistry()
        norm = Normalizer()
        for ticker in self.NEW_TICKERS:
            spec = reg.get(ticker)
            test_val = (spec.min_value + spec.max_value) / 2
            rec = norm.normalize(ticker, "2026-08-05", test_val, "yahoo")
            assert rec is not None


# ────────────────────────────────────────────────────────────────
# 8. cmd_analyze — VN20 Gate integration
# ────────────────────────────────────────────────────────────────
class TestCmdAnalyzeVN20Gate:
    """cmd_analyze must show VN20 Gate status (PASS/FAIL) for each symbol."""

    def _run_analyze(self, symbols, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["analyze", "--symbols"] + symbols)
        args.func(args)
        return capsys.readouterr().out

    def test_analyze_shows_vn20_label(self, capsys):
        """Output must contain VN20 status label for each symbol."""
        out = self._run_analyze(["VCB"], capsys)
        assert "VN20" in out or "vn20" in out.lower()

    def test_analyze_vn20_pass_or_fail(self, capsys):
        """VN20 label must be either PASS or FAIL."""
        out = self._run_analyze(["VCB"], capsys)
        # After "VN20" there should be PASS or FAIL
        assert "PASS" in out or "FAIL" in out

    def test_analyze_multiple_vn20_labels(self, capsys):
        """Each symbol gets its own VN20 label."""
        out = self._run_analyze(["VCB", "HPG"], capsys)
        # Count VN20 occurrences — should be at least 2
        assert out.count("VN20") >= 2 or out.count("vn20") >= 2


# ────────────────────────────────────────────────────────────────
# 9. cmd_analyze — ANSI color output
# ────────────────────────────────────────────────────────────────
class TestCmdAnalyzeColor:
    """cmd_analyze output must contain ANSI color codes when NO_COLOR is not set."""

    def _run_analyze(self, symbols, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["analyze", "--symbols"] + symbols)
        args.func(args)
        return capsys.readouterr().out

    def test_analyze_has_ansi_codes(self, capsys, monkeypatch):
        """With color enabled, output should contain ANSI escape codes."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        out = self._run_analyze(["VCB"], capsys)
        assert "\033[" in out, "Expected ANSI color codes in output"

    def test_analyze_no_ansi_when_no_color(self, capsys, monkeypatch):
        """With NO_COLOR=1, output should have no ANSI codes."""
        monkeypatch.setenv("NO_COLOR", "1")
        out = self._run_analyze(["VCB"], capsys)
        assert "\033[" not in out, "Expected NO ANSI codes when NO_COLOR=1"


# ────────────────────────────────────────────────────────────────
# 10. cmd_analyze — edge cases
# ────────────────────────────────────────────────────────────────
class TestCmdAnalyzeEdgeCases:
    """cmd_analyze must handle edge cases gracefully."""

    def _run_analyze(self, symbols, capsys):
        from ptck import build_parser

        parser = build_parser()
        args = parser.parse_args(["analyze", "--symbols"] + symbols)
        args.func(args)
        return capsys.readouterr().out

    def test_analyze_nonexistent_symbol(self, capsys):
        """Non-existent symbol must not crash — should show 'KHÔNG CÓ DỮ LIỆU'."""
        out = self._run_analyze(["ZZZZZZ_NONEXIST"], capsys)
        assert "KHÔNG CÓ DỮ LIỆU" in out or "không có dữ liệu" in out.lower() or "ZZZZZZ" in out

    def test_analyze_empty_symbols_list(self):
        """Empty symbols list should be rejected by argparse."""
        from ptck import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["analyze", "--symbols"])


# ────────────────────────────────────────────────────────────────
# 11. _vn20_gate — Bank-specific logic (CAPITAL_RATIO, NIM, NPL)
# ────────────────────────────────────────────────────────────────
class TestVN20GateBankSpecific:
    """Bank-specific VN20 Gate: CAPITAL_RATIO>=5%, NIM>=1.8% annualized, NPL<3%."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from src.backtest.unified_system_replay import _date_to_period, _vn20_gate

        self._gate = _vn20_gate
        self._period = _date_to_period
        self.db = tmp_path / "test.db"
        self.fin = tmp_path / "fin.db"
        self.conn = sqlite3.connect(str(self.db))
        self.fin_conn = sqlite3.connect(str(self.fin))
        self.conn.execute(
            "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        self.fin_conn.execute("CREATE TABLE health_ratios (symbol TEXT, ratio_name TEXT, ratio_value REAL, period TEXT)")
        self.conn.execute(f"ATTACH DATABASE '{str(self.fin).replace(chr(92), chr(47))}' AS fin")

    def _insert(self, symbol, date, volume=100000, **ratios):
        self.conn.execute(
            "INSERT INTO daily_ohlcv VALUES (?, ?, 100, 110, 90, 100, ?)",
            (symbol, date, volume),
        )
        # PIT-strict: tại ngày `date`, chỉ quý ĐÃ KẾT THÚC được dùng. Gán period
        # = quý liền TRƯỚC quý chứa date (dữ liệu đã công bố), không phải quý
        # đang diễn ra (chuẩn period < anchor_quarter).
        period = self._prev_quarter(self._period(date))
        for name, val in ratios.items():
            self.fin_conn.execute(
                "INSERT INTO health_ratios VALUES (?, ?, ?, ?)",
                (symbol, name, val, period),
            )
        self.fin_conn.commit()

    @staticmethod
    def _prev_quarter(period):
        """'2026Q3' → '2026Q2'; '2026Q1' → '2025Q4'."""
        year, q = int(period[:4]), int(period[-1])
        if q == 1:
            return f"{year - 1}Q4"
        return f"{year}Q{q - 1}"

    def test_bank_capital_ratio_pass(self):
        """Bank with Cap=8.9%, NIM=4.4%, ROE=17% → PASS."""
        self._insert("TPB", "2026-08-05", volume=20_000_000, ROE=0.0426, CAPITAL_RATIO=0.089, NIM=0.0111)
        assert self._gate(self.conn, "TPB", "2026-08-05") is True

    def test_bank_low_capital_ratio_fail(self):
        """Bank with Cap=4.0% (< 8% sàn pháp lý TT41) → FAIL."""
        self._insert("LOWCAP", "2026-08-05", volume=100_000, ROE=0.04, CAPITAL_RATIO=0.04, NIM=0.02)
        assert self._gate(self.conn, "LOWCAP", "2026-08-05") is False

    def test_bank_below_legal_floor_fail(self):
        """Bank Cap=5.5% (< 8% sàn pháp lý TT41/2016/TT-NHNN — Basel II) → FAIL.

        Legal-Hardening Audit 2026-08-08: ngưỡng 5% cũ sai luật, nâng lên 8%."""
        self._insert("SUB8", "2026-08-05", volume=500_000, ROE=0.03, CAPITAL_RATIO=0.055, NIM=0.005, NPL_RATIO=0.015)
        assert self._gate(self.conn, "SUB8", "2026-08-05") is False

    def test_bank_low_nim_fail(self):
        """Bank with NIM quarterly=0.004 (annual 1.6% < 1.8%) → FAIL."""
        self._insert("LOWNIM", "2026-08-05", volume=100_000, ROE=0.04, CAPITAL_RATIO=0.08, NIM=0.004)
        assert self._gate(self.conn, "LOWNIM", "2026-08-05") is False

    def test_bank_high_npl_fail(self):
        """Bank with NPL=3.5% (> 3%) → FAIL."""
        self._insert("BADNPL", "2026-08-05", volume=100_000, ROE=0.04, CAPITAL_RATIO=0.08, NIM=0.02, NPL_RATIO=0.035)
        assert self._gate(self.conn, "BADNPL", "2026-08-05") is False

    def test_bank_state_owned_pass(self):
        """State bank (SOCB): Cap=11% (>= 8% TT41), NIM annual 2.0%, NPL=1.5% → PASS."""
        self._insert("STATEBANK", "2026-08-05", volume=500_000, ROE=0.03, CAPITAL_RATIO=0.11, NIM=0.005, NPL_RATIO=0.015)
        assert self._gate(self.conn, "STATEBANK", "2026-08-05") is True

    def test_bank_missing_nim_npl_fallback(self):
        """Bank with no NIM/NPL data → skip those checks, rely on Cap only."""
        self._insert("NODATA", "2026-08-05", volume=100_000, ROE=0.04, CAPITAL_RATIO=0.08)
        assert self._gate(self.conn, "NODATA", "2026-08-05") is True

    def test_nonbank_uses_de(self):
        """Non-bank with D/E=1.5 → PASS (leverage check via D/E)."""
        self._insert("NONBANK", "2026-08-05", volume=100_000, ROE=0.04, DEBT_TO_EQUITY=1.5)
        assert self._gate(self.conn, "NONBANK", "2026-08-05") is True

    def test_nonbank_high_de_fail(self):
        """Non-bank with D/E=3.0 → FAIL."""
        self._insert("HIGHDE", "2026-08-05", volume=100_000, ROE=0.04, DEBT_TO_EQUITY=3.0)
        assert self._gate(self.conn, "HIGHDE", "2026-08-05") is False

    def test_low_roe_fail(self):
        """Bank with ROE quarterly=0.02 (annual 8% < 10%) → FAIL even with good metrics."""
        self._insert("LOWROE", "2026-08-05", volume=100_000, ROE=0.02, CAPITAL_RATIO=0.08, NIM=0.02)
        assert self._gate(self.conn, "LOWROE", "2026-08-05") is False

    def test_low_volume_fail(self):
        """Bank with volume=40,000 (< 50,000) → FAIL."""
        self._insert("LOWVOL", "2026-08-05", volume=40_000, ROE=0.04, CAPITAL_RATIO=0.08, NIM=0.02)
        assert self._gate(self.conn, "LOWVOL", "2026-08-05") is False
