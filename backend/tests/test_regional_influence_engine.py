"""test_regional_influence_engine.py — TDD cho RegionalInfluenceEngine (Copper/VNINDEX/data quality).

Kiểm thử:
  - COPPER unit conversion (USD/lb → USD/ton)
  - VNINDEX fallback (macro_history → daily_ohlcv)
  - All 9 component scores ∈ [0.0, 1.0]
  - Macro vector shift on data fixes
  - Degraded mode when DB is empty
"""

import pathlib
import sys

# Hydrate path (AGENTS.md LAW-003)
_root = pathlib.Path(__file__).resolve().parent.parent
for _p in [_root / "src", _root]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_copper_unit_conversion_lb_to_ton():
    """COPPER_HG stored in USD/lb, normalization bounds in USD/ton.
    Engine must convert: copper_lb * 2204.62 before normalization."""
    from governor.regional_influence_engine import RegionalInfluenceEngine

    engine = RegionalInfluenceEngine()
    result = engine.compute()

    # COPPER component score must be in [0, 1] (not 0.0 from unit mismatch)
    copper_score = result.component_scores.get("COPPER")
    assert copper_score is not None, "COPPER score should not be None"
    assert 0.0 <= copper_score <= 1.0, f"COPPER score {copper_score} out of [0,1] — possible unit mismatch"
    # With copper at ~$6.65/lb = ~$14,650/ton, score should be high
    assert copper_score > 0.5, f"COPPER score {copper_score} suspiciously low for ~$14,650/ton"


def test_vnindex_fallback_mechanism():
    """VNINDEX lives in daily_ohlcv, not macro_history.
    Engine must fall back to daily_ohlcv when macro_history returns None."""
    from governor.regional_influence_engine import RegionalInfluenceEngine

    engine = RegionalInfluenceEngine()
    result = engine.compute()

    vnindex_score = result.component_scores.get("VNINDEX")
    assert vnindex_score is not None, "VNINDEX score should not be None"
    assert 0.0 <= vnindex_score <= 1.0, f"VNINDEX score {vnindex_score} out of [0,1]"
    assert result.data_quality.get("VNINDEX") is True, "VNINDEX data_quality should be True (fallback to daily_ohlcv)"


def test_component_scores_range_assertion():
    """All 9 indicators must produce scores ∈ [0.0, 1.0]."""
    from governor.regional_influence_engine import RegionalInfluenceEngine

    engine = RegionalInfluenceEngine()
    result = engine.compute()

    expected_indicators = [
        "US_FED_RATE",
        "DXY",
        "US10Y",
        "VIX",
        "USDCNY",
        "BRENT_OIL",
        "COPPER",
        "INTERBANK_ON",
        "VNINDEX",
    ]
    for ind in expected_indicators:
        score = result.component_scores.get(ind)
        assert score is not None, f"{ind} score is None"
        assert 0.0 <= score <= 1.0, f"{ind} score {score} out of [0.0, 1.0]"


def test_macro_vector_shift_on_data_fixes():
    """After COPPER unit fix + VNINDEX fallback, macro vector must shift.
    China_Economy and Commodity_Cycle should increase significantly."""
    from governor.regional_influence_engine import RegionalInfluenceEngine

    engine = RegionalInfluenceEngine()
    result = engine.compute()
    M = result.macro_vector

    # China_Economy includes COPPER (25% weight) — should be > 0.5
    assert M["China_Economy"] > 0.5, f"China_Economy {M['China_Economy']} should be > 0.5 after COPPER fix"
    # Commodity_Cycle includes COPPER (40% weight) — should be > 0.5
    assert M["Commodity_Cycle"] > 0.5, f"Commodity_Cycle {M['Commodity_Cycle']} should be > 0.5 after COPPER fix"
    # Domestic_Liquidity includes VNINDEX (30% weight) — should be > 0.3
    assert M["Domestic_Liquidity"] > 0.3, f"Domestic_Liquidity {M['Domestic_Liquidity']} should be > 0.3 after VNINDEX fix"
    # All macro nodes must be in [0, 1]
    for node in ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]:
        assert 0.0 <= M[node] <= 1.0, f"{node} = {M[node]} out of [0,1]"


def test_degraded_mode_trigger_conditions():
    """When DB has no data, engine must return degraded results safely."""
    import sqlite3
    import tempfile

    from governor.regional_influence_engine import RegionalInfluenceEngine

    # Create empty DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        empty_db = f.name

    conn = sqlite3.connect(empty_db)
    conn.execute("""CREATE TABLE macro_history (
        variable TEXT, date TEXT, value REAL,
        PRIMARY KEY (variable, date)
    )""")
    conn.execute("""CREATE TABLE daily_ohlcv (
        symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
        close REAL, adj_close REAL, volume REAL, source TEXT,
        PRIMARY KEY (symbol, date)
    )""")
    conn.commit()
    conn.close()

    try:
        engine = RegionalInfluenceEngine(db_path=empty_db)
        result = engine.compute()

        # All scores should be None (no data)
        for ind in ["US_FED_RATE", "DXY", "US10Y", "VIX", "USDCNY", "BRENT_OIL", "COPPER", "INTERBANK_ON", "VNINDEX"]:
            assert result.component_scores.get(ind) is None, f"{ind} should be None with empty DB"

        # Macro vector should still be valid (default 0.5)
        for node in ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]:
            assert 0.0 <= result.macro_vector[node] <= 1.0, (
                f"{node} = {result.macro_vector[node]} out of [0,1] in degraded mode"
            )
    finally:
        import os

        os.unlink(empty_db)
