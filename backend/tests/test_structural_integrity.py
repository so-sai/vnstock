"""
test_structural_integrity.py — WHY: import-chain breakage under Nuitka packaging.

BOUNDARY: Every critical module chain (macro_service, cross_market_flow_map,
regime_engine, gold routes, presentation layer) must resolve at both dev-time
(Python) and build-time (Nuitka frozen). A missing import causes silent binary
failure. Do NOT delete or rename exported symbols without adding a new test here.
"""
import sqlite3
import sys
import pathlib
import pytest


def _find_root():
    current = pathlib.Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    raise RuntimeError("Cannot find project root")


PROJECT_ROOT = _find_root()
SRC_DIR = str(PROJECT_ROOT / "backend" / "src")
BACKEND_DIR = str(PROJECT_ROOT / "backend")
DATA_DIR = PROJECT_ROOT / "backend" / "data"


@pytest.fixture(scope="session", autouse=True)
def hydrate_path():
    for p in [SRC_DIR, BACKEND_DIR, str(PROJECT_ROOT)]:
        if p not in sys.path:
            sys.path.insert(0, p)


class TestCriticalImports:
    """Import tests that catch structural drift between development and production environments.

    Blind Spot 1 (AGENTS.md §8): TDD tests run in Python Dev environment where paths are
    always available. When Nuitka packages into onefile .exe, path resolution changes.
    These tests verify that ALL critical import chains resolve, catching ModuleNotFoundError
    due to duplicate/missing packages before build time.
    """

    IMPORT_CHAINS = [
        "src.services.macro_service:get_macro_status",
        "src.engine.cross_market_flow_map:CrossMarketFlowMap",
        "src.core.market_snapshot:tao_anh_chup",
        "src.core.market_state_coordinator:build_market_state",
        "src.engine.regime_engine:detect_regime",
        "src.api.routes.gold:analyze_gold_regime",
        "src.services.weekly_cognitive_report:analyze_gold_regime",
        "core.macro.gold_regime_engine:cross_reference_with_market",
        "core.macro.gold_regime_engine:analyze_gold_regime",
        "core.flow.liquidity_concentration_engine:get_lci_dashboard",
        "core.holdings.exposure_engine:compute_exposure_summary",
        "core.holdings.holdings_view_builder:build_holdings_view",
        "core.validation.state_space_validator:StateSpaceValidator",
        "core.presentation.directional_bias_extractor:compute_directional_bias",
        "core.presentation.state_stability_index:compute_ssi",
        "core.presentation.trade_state_policy:compute_trade_state",
    ]

    @pytest.mark.parametrize("chain", IMPORT_CHAINS)
    def test_import_chain_resolves(self, chain):
        module_path, _, attr_name = chain.partition(":")
        try:
            __import__(module_path, fromlist=[attr_name])
        except ImportError as e:
            pytest.fail(f"FAIL: {chain} — {e}")

    def test_core_package_is_backend_src(self):
        import core
        expected = str(PROJECT_ROOT / "backend" / "src" / "core")
        actual = pathlib.Path(core.__file__).parent
        assert str(actual) == expected, (
            f"core package resolved to {actual}, expected {expected}. "
            "Merge of root core/ into backend/src/core/ may be incomplete."
        )


class TestMacroHistorySchema:
    """Schema drift detection: code assumes columns that don't exist in production DB.

    Blind Spot 2 (AGENTS.md §8): Mocking in unit tests skips real DB access.
    Schema changes in db_core.py CREATE TABLE are not reflected in existing databases
    until a migration or full recreate is run. Without explicit schema testing,
    ALTER TABLE migrations can be forgotten.
    """

    DB_PATH = DATA_DIR / "screener_cache.db"

    def test_macro_history_has_is_stale_column(self):
        if not self.DB_PATH.exists():
            pytest.skip("screener_cache.db not found")
        conn = sqlite3.connect(str(self.DB_PATH))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(macro_history)").fetchall()}
        finally:
            conn.close()
        assert "is_stale" in cols, (
            "macro_history table is missing 'is_stale' column. "
            "Run optimize_sqlite_engine() or apply ALTER TABLE."
        )

    def test_macro_history_has_expected_columns(self):
        if not self.DB_PATH.exists():
            pytest.skip("screener_cache.db not found")
        conn = sqlite3.connect(str(self.DB_PATH))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(macro_history)").fetchall()}
        finally:
            conn.close()
        expected = {"date", "variable", "value", "is_stale"}
        missing = expected - cols
        assert not missing, f"macro_history missing columns: {missing}"


class TestHydratePathPriority:
    """Verify that PROJECT_ROOT stays at sys.path[0] after hydration.

    This prevents third-party libs (e.g. vnstock) pushed by config.py
    from breaking relative path resolution across the stack.
    """

    def test_project_root_before_backend_src_in_syspath(self):
        root_str = str(PROJECT_ROOT)
        src_str = str(PROJECT_ROOT / "backend" / "src")
        try:
            root_idx = sys.path.index(root_str)
            src_idx = sys.path.index(src_str)
        except ValueError:
            pytest.fail(f"PROJECT_ROOT={root_str} or backend/src={src_str} not in sys.path")
        assert root_idx < src_idx

    def test_core_imports_correct_package(self):
        import core
        core_path = pathlib.Path(core.__file__).parent
        expected = PROJECT_ROOT / "backend" / "src" / "core"
        assert core_path == expected, (
            f"core resolves to {core_path}, not {expected}"
        )

    def test_core_macro_importable(self):
        import core.macro
        assert hasattr(core.macro, "gold_regime_engine")
