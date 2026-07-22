"""ECL classifier — determine if a module is execution core or observability."""

EXECUTION_CORE_PREFIXES = [
    "src.portfolio.decision_tensor",
    "src.portfolio.exposure_engine",
    "src.portfolio.memory_engine",
    "src.portfolio.position_sizer",
    "src.portfolio.risk_budget",
    "src.portfolio.risk_governor_engine",
    "src.portfolio.decision_fusion",
    "src.engine.regime_engine",
    "src.engine.rsi_regime_engine",
    "src.engine.breadth_engine",
    "src.engine.rs_ranker",
    "src.engine.screener_logic",
    "src.engine.meanrev_engine",
    "src.engine.liquidity_wave",
    "src.engine.sector_rotation_graph",
    "src.engine.breakout_continuation",
    "src.engine.flow_decay_engine",
    "src.engine.unit_normalizer",
    "src.core.data_quality",
    "src.cao_validation",
    "core.macro",
    "core.holdings",
    "core.guard",
    "core.signal_provenance",
    "core.presentation",
]

OBSERVABILITY_PREFIXES = [
    "src.shadow_cao",
    "src.telemetry",
    "src.core.psr",
    "src.core.ecl",
    "src.cao_readiness",
    "src.services",
    "src.api",
    "src.engine.capital_flow_forecasting_engine",
    "src.engine.capital_displacement_engine",
    "src.engine.sentinel_alert",
    "src.engine.heatmap_engine",
    "src.engine.dashboard_engine",
    "src.engine.elite_scanner",
    "src.engine.ipo_engine",
    "src.engine.recovery_engine",
    "src.engine.strategy_commander",
    "src.engine.backtest_engine",
    "src.engine.universe",
    "src.portfolio.shadow_tracker",
    "src.utils",
    "core.flow",
]


def is_execution_core(module_name: str) -> bool:
    """Return True if module_name is part of the execution core."""
    for prefix in EXECUTION_CORE_PREFIXES:
        if module_name == prefix or module_name.startswith(prefix + "."):
            return True
    return False


def is_observability(module_name: str) -> bool:
    """Return True if module_name is observability-only."""
    for prefix in OBSERVABILITY_PREFIXES:
        if module_name == prefix or module_name.startswith(prefix + "."):
            return True
    return False
