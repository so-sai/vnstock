import sys, pathlib

def _hydrate_path():
    current = pathlib.Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root_path = current
            break
        current = current.parent
    backend_path = str(root_path / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.engine.cross_market_flow_map import CrossMarketFlowMap

print("=" * 60)
print("CROSS-MARKET FLOW MAP — SMOKE TEST")
print("=" * 60)

macro = {
    "us_real_yield": 1.85,
    "breakeven_inflation": 2.45,
    "dxy_index": 106.5,
    "gold_silver_ratio": 88.5,
    "vgb10y_data_quality": "REAL",
    "interbank_rate": 4.5,
}

snapshot = {
    "ngay": "2026-06-11",
    "thoi_gian_tao": "2026-06-11 10:00:00",
    "regime": {"adx": 18.5, "do_rong": 52.0, "trang_thai": "RANGING"},
}

engine = CrossMarketFlowMap(macro, snapshot)
result = engine.execute_pipeline()

# Layer 1
d = result["layer_1_drivers"]
assert "delta_real_yield" in d
assert "delta_dxy" in d
assert "delta_gs_ratio" in d
print(f"[OK] Layer 1 drivers: {list(d.keys())}")

# Layer 2
s = result["layer_2_settlement"]
assert s in ("CASH_SHELTER", "HARD_ASSET_SHELTER", "EQUITY_EXPANSION", "TRANSITION_STATE")
print(f"[OK] Layer 2 settlement: {s}")

# Layer 3
r = result["layer_3_reputation"]
assert r["confidence_level"] in ("HIGH_CONFIDENCE", "LOW_CONFIDENCE_MACRO_VN")
assert r["vgb10y_quality"] == "REAL"
assert r["interbank_status"] == "REAL"
print(f"[OK] Layer 3 reputation: {r['confidence_level']}")

# Layer 4
dr = result["layer_4_drift"]
assert "has_drift" in dr
print(f"[OK] Layer 4 drift: has_drift={dr['has_drift']}")

# Metadata
assert result["ngay"] == "2026-06-11"
assert result["timestamp"] == "2026-06-11 10:00:00"
print(f"[OK] Metadata present")

print()
print("ALL FLOW MAP ASSERTIONS PASSED")
print("=" * 60)
