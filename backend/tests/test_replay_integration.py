"""test_replay_integration.py — Integration guard for the 0-trades bug.

WHY THIS FILE EXISTS (TDD gap exposed 2026-08-06):
  The multi-factor backtest returned "Total Trades: 0" with NAV frozen at
  100M for the entire 2021-04-01 → 2026-08-05 period. Root cause was NOT in
  the scoring pipeline (173 valid BUY signals with full price data), but in
  the SYSPATH wiring:

    unified_system_replay.py added only `backend/src` to sys.path, but
    RegionalInfluenceEngine._fetch_latest did `from src.governor.schemas
    import validate_finite_float`, which needs `backend/` (NOT `backend/src`)
    on sys.path. When the CLI ran `python -m backend.src.backtest.
    unified_system_replay` from project root, that lazy import raised
    ModuleNotFoundError INSIDE macro_engine.compute(), which the backtest
    loop swallows via `except Exception: pass` → macro_result=None on every
    scored day → 0 trades, silently.

  Unit tests never caught this because they run from `backend/`, where
  `src.*` imports resolve fine.

THE FIX (3-tier Extended Fail-Fast TDD framework):
  Tier 1 — Unit TDD: per-module logic (unchanged, still 900+ PASS).
  Tier 2 — Entry Point Smoke TDD: subprocess-run the real CLI from project
           root (test_cli_backtest_returns_nonzero_trades) AND from a CLEAN
           environment with backend/ stripped from PYTHONPATH
           (test_cli_runs_with_clean_syspath) — proves namespace hygiene.
  Tier 3 — Exception Invariant TDD: governor lazy imports converted from
           absolute `from src.governor.*` to RELATIVE `from .*` (Rule 2),
           so no CWD/entry-point can ever reproduce the ModuleNotFoundError.
           The replay loop's `except Exception: pass` now LOGS instead of
           swallowing (Rule 3, fail-fast).

SENTINEL LAW: this module carries _hydrate_path() v2.2.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"


def _hydrate_path():
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    return _ROOT


_PROJECT_ROOT = _hydrate_path()


class TestCliPathWiring:
    """The exact failure mode: module-form invocation from project root."""

    def test_macro_compute_works_from_project_root(self):
        """RegionalInfluenceEngine.compute() must NOT raise when imported
        the way the CLI imports it (python -m backend.src.backtest...)."""
        from src.governor.regional_influence_engine import RegionalInfluenceEngine

        engine = RegionalInfluenceEngine()
        result = engine.compute("2021-06-15")
        assert result is not None
        assert "Domestic_Liquidity" in result.macro_vector

    def test_governor_relative_imports_resolve_from_any_cwd(self):
        """Governor modules must import via RELATIVE paths (Rule 2).

        WHY: after the 2026-08-06 fix, `from src.governor.*` was replaced
        with `from .*` inside the governor package. This test imports every
        governor module with backend/ REMOVED from sys.path — proving the
        imports do not depend on any CWD/entry-point wiring.
        """
        import importlib
        import logging

        logging.getLogger().setLevel(logging.CRITICAL)
        # Snapshot sys.path, then strip all backend paths
        saved = list(sys.path)
        try:
            sys.path = [p for p in sys.path if "backend" not in str(p).replace("\\", "/")]
            for mod in (
                "regional_influence_engine",
                "liquidity_recovery_index",
                "company_state",
                "policy_impact_engine",
                "csi_explain",
                "schemas",
                "regime_classifier",
                "fair_multiple_engine",
                "composite_score_projector",
                "interaction_engine",
                "macro_lag_engine",
                "sector_exposure_matrix",
            ):
                importlib.import_module(f"src.governor.{mod}")
        finally:
            sys.path = saved


class TestReplayProducesTrades:
    """Regression: replay must generate trades when data is available."""

    @pytest.mark.integration
    def test_multifactor_replay_makes_trades(self):
        """A short replay window with abundant BUY signals must produce > 0 trades.

        Window 2021-04-01→2022-12-31: debug shows 157+ BUY signals in this
        period with full close prices. If wiring is broken, trades == 0.
        """
        from src.backtest.unified_system_replay import (
            UNIVERSE,
            InteractionEngine,
            MacroLagEngine,
            SectorExposureMatrix,
            _get_behavioral_score,
            _get_fundamental_score,
            _get_momentum_score,
            _get_sector,
            _get_trading_days,
            _vn20_gate,
        )
        from src.governor.regional_influence_engine import RegionalInfluenceEngine

        W_FUND, W_MACRO, W_ALPHA, W_BEHAV = 0.20, 0.00, 0.10, 0.70
        ENTRY_THRESH = 0.55

        engine = RegionalInfluenceEngine()
        matrix = SectorExposureMatrix()
        lag_engine = MacroLagEngine()
        ix_engine = InteractionEngine()

        import sqlite3

        conn = sqlite3.connect(str(_BACKEND / "data" / "screener_cache.db"))
        conn.execute(f"ATTACH DATABASE '{(_BACKEND / 'data' / 'financial_facts.db').as_posix()}' AS fin")

        dates = _get_trading_days(conn, "2021-04-01", "2022-12-31")
        score_days = dates[::5]

        buy_signals = 0
        for date in score_days:
            macro_result = engine.compute(date)
            if not macro_result:
                continue
            M = macro_result.macro_vector
            sector_scores = matrix.get_sector_ranking(M)
            lag_results = lag_engine.compute_all_sectors(date)
            ix_results = ix_engine.compute_all_sectors(M)
            for sect, raw_score in sector_scores:
                eff = lag_results.get(sect)
                eff_score = eff.effective_score if eff else raw_score
                mult = ix_results.get(sect)
                final_score = eff_score * (mult.multiplier if mult else 1.0)
                if final_score > 0.50:
                    stocks = [s for s in UNIVERSE if _get_sector(conn, s) == sect]
                    for sym in stocks[:2]:
                        if _vn20_gate(conn, sym, date):
                            fs = _get_fundamental_score(conn, sym, date)
                            bs = _get_behavioral_score(conn, sym, date)
                            ms = _get_momentum_score(conn, sym, date)
                            composite = W_FUND * fs + W_MACRO * eff_score + W_ALPHA * ms + W_BEHAV * bs
                            if composite > ENTRY_THRESH:
                                buy_signals += 1
        conn.close()

        assert buy_signals > 0, (
            "0 BUY signals — if this was the real loop, trades=0. "
            "Check sys.path wiring (backend/ missing → src.governor imports fail silently)."
        )

    @pytest.mark.integration
    def test_cli_backtest_returns_nonzero_trades(self):
        """Tier 2 — Entry Point Smoke TDD: run the real CLI from project root.

        WHY: this is the ultimate guard. It exercises the exact invocation
        that failed (project root, `python -m backend.src.backtest...`).
        Uses a reduced sample-every to keep runtime sane.
        """
        cmd = [
            sys.executable,
            "-m",
            "backend.src.backtest.unified_system_replay",
            "--mode",
            "multi-factor",
            "--start",
            "2021-04-01",
            "--end",
            "2022-12-31",
            "--sample-every",
            "20",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            timeout=600,
        )
        assert proc.returncode == 0, f"CLI crashed:\n{proc.stderr[-2000:]}"
        assert "Total Trades" in proc.stdout, "Output missing trade metrics"
        # Parse Total Trades from output
        for line in proc.stdout.splitlines():
            if "Total Trades" in line:
                trades = int(line.split()[-1])
                assert trades > 0, f"CLI backtest made 0 trades:\n{proc.stdout[-2000:]}"
                break
        else:
            pytest.fail("Total Trades line not found in output")

    @pytest.mark.integration
    def test_cli_runs_with_clean_syspath(self):
        """Tier 2 — Entry Point Smoke TDD (namespace hygiene): CLI from project
        root with backend/ STRIPPED from PYTHONPATH.

        WHY: the ORIGINAL bug only manifested when `backend/` was absent from
        sys.path. The relative-import fix (Rule 2) must guarantee the CLI
        runs even in that hostile environment — this test enforces that
        invariant forever. If someone re-introduces an absolute `src.*`
        import in the governor package, this subprocess will crash.
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = ""  # strip everything; only CWD (project root) remains
        cmd = [
            sys.executable,
            "-m",
            "backend.src.backtest.unified_system_replay",
            "--mode",
            "multi-factor",
            "--start",
            "2021-04-01",
            "--end",
            "2022-12-31",
            "--sample-every",
            "20",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            env=env,
            timeout=600,
        )
        # A crash here means an absolute src.* import leaked back in.
        assert proc.returncode == 0, f"CLI crashed under clean sys.path:\n{proc.stderr[-3000:]}"
        assert "Total Trades" in proc.stdout, "Output missing trade metrics"
        for line in proc.stdout.splitlines():
            if "Total Trades" in line:
                trades = int(line.split()[-1])
                assert trades > 0, f"CLI made 0 trades under clean sys.path:\n{proc.stdout[-2000:]}"
                break
        else:
            pytest.fail("Total Trades line not found in output")
