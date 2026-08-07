import importlib
import sys
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()


class ProjectRegistry:
    """
    Institutional Module Registry v1.0.
    Cung cấp quyền truy cập tập trung, lazy-loaded vào tất cả các module của PTCK_VNSTOCK.
    Giúp tránh lỗi Circular Import và tối ưu hóa bộ nhớ.
    """

    # --- ENGINES ---
    @property
    def regime_engine(self):
        return importlib.import_module("src.engine.regime_engine")

    @property
    def meanrev_engine(self):
        return importlib.import_module("src.engine.meanrev_engine")

    @property
    def backtest_engine(self):
        return importlib.import_module("src.engine.backtest_engine")

    @property
    def breadth_engine(self):
        return importlib.import_module("src.engine.breadth_engine")

    @property
    def dashboard_engine(self):
        return importlib.import_module("src.engine.dashboard_engine")

    @property
    def decision_engine(self):
        return importlib.import_module("src.engine.decision_engine")

    @property
    def elite_scanner(self):
        return importlib.import_module("src.engine.elite_scanner")

    @property
    def heatmap_engine(self):
        return importlib.import_module("src.engine.heatmap_engine")

    @property
    def money_flow_engine(self):
        return importlib.import_module("src.engine.money_flow_engine")

    @property
    def recovery_engine(self):
        return importlib.import_module("src.engine.recovery_engine")

    @property
    def rs_ranker(self):
        return importlib.import_module("src.engine.rs_ranker")

    @property
    def screener_logic(self):
        return importlib.import_module("src.engine.screener_logic")

    @property
    def sector_ranker(self):
        return importlib.import_module("src.engine.sector_ranker")

    @property
    def strategy_commander(self):
        return importlib.import_module("src.engine.strategy_commander")

    @property
    def stress_test_2022(self):
        return importlib.import_module("src.engine.stress_test_2022")

    # --- RESEARCH & BACKTEST ---
    @property
    def system_replay(self):
        return importlib.import_module("src.backtest.system_replay")

    @property
    def audit_generator(self):
        return importlib.import_module("src.backtest.audit_generator")

    @property
    def analyze_recovery(self):
        return importlib.import_module("src.research.analyze_recovery")

    @property
    def audit_2022(self):
        return importlib.import_module("src.research.audit_2022")

    @property
    def check_data(self):
        return importlib.import_module("src.research.check_data")

    @property
    def check_integrity(self):
        return importlib.import_module("src.research.check_integrity")

    @property
    def check_ohlcv(self):
        return importlib.import_module("src.research.check_ohlcv")

    @property
    def check_schema(self):
        return importlib.import_module("src.research.check_schema")

    # debug_2022_stats removed — file no longer exists
    @property
    def hydrate_2020(self):
        return importlib.import_module("src.research.hydrate_2020")

    @property
    def sim_regime(self):
        return importlib.import_module("src.research.sim_regime")

    # --- DATA & DATABASE ---
    @property
    def daily_closer(self):
        return importlib.import_module("src.daily_closer")

    @property
    def daily_updater(self):
        return importlib.import_module("src.daily_updater")

    @property
    def db_core(self):
        return importlib.import_module("src.database.db_core")

    @property
    def timeline_manager(self):
        return importlib.import_module("src.database.timeline_manager")

    @property
    def background_sweep(self):
        return importlib.import_module("src.background_sweep")

    @property
    def shadow_tracker(self):
        return importlib.import_module("src.portfolio.shadow_tracker")

    # --- SHIELDS ---
    @property
    def sentinel_alert(self):
        return importlib.import_module("src.engine.sentinel_alert")

    @property
    def sentinel_check(self):
        return importlib.import_module("src.utils.sentinel_check")

    @property
    def defense(self):
        return importlib.import_module("src.utils.defense")

    # --- UTILS ---
    @property
    def config(self):
        return importlib.import_module("src.config")

    @property
    def env_audit(self):
        return importlib.import_module("src.utils.env_audit")

    @property
    def macro_sensors(self):
        return importlib.import_module("src.utils.macro_sensors")

    # --- GOLD MACRO ---
    @property
    def gold_regime_engine(self):
        return importlib.import_module("core.macro.gold_regime_engine")

    @property
    def gold_service(self):
        return importlib.import_module("src.services.macro.gold_service")

    @property
    def gold_world_service(self):
        return importlib.import_module("src.services.macro.gold_world_service")

    @property
    def gold_spread_engine(self):
        return importlib.import_module("core.macro.gold_spread_engine")


# Singleton instance
registry = ProjectRegistry()

if __name__ == "__main__":
    print("\n" + "═" * 50)
    print("PTCK_VNSTOCK: INSTITUTIONAL MODULE REGISTRY")
    print("═" * 50)
    print(f"Project Root: {PROJECT_ROOT}")

    # Kiểm tra thử tải module config
    try:
        cfg = registry.config
        print(f"✅ Registry Load Success: {cfg.__name__}")
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        print(f"❌ Registry Load Failed: {e}")

    print("\n[Categories]:")
    print("- Engines: 15 modules mapped.")
    print("- Research: 11 modules mapped.")
    print("- Data: 6 modules mapped.")
    print("- Shields: 3 modules mapped.")
    print("- Utils: 3 modules mapped.")
    print("- Gold Macro: 4 modules mapped (gold_regime_engine + gold_service + gold_world_service + gold_spread_engine).")
    print("═" * 50 + "\n")
