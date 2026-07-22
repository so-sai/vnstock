"""
Production Hardening Smoke Test
Run: python -X utf8 backend/tests/test_hardened_engine.py (from project root)
"""
import sys
import pathlib

# Sentinel v2.1 hydration
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

from src.engine.regime_engine import detect_regime

print("=" * 60)
print("PRODUCTION HARDENING SMOKE TEST -- Adaptive EMA Engine")
print("=" * 60)

result = detect_regime()

print()
print("=== VERDICT STRUCTURE ===")
print(f"  date:                 {result['date']}")
print(f"  status:               {result['status']}")
print(f"  regime_score:         {result['regime_score']}  (smoothed)")
print(f"  regime_score_raw:     {result['regime_score_raw']}")
print(f"  ema_alpha:            {result['ema_alpha']}")

d = result['details']
print()
print("=== CONTINUOUS SCORES ===")
print(f"  b_score  (continuous): {d['b_score']}  <-- was discrete 0/0.3/0.6/1.0")
print(f"  t_score  (discrete):   {d['t_score']}")
print(f"  v_score  (continuous): {d['v_score']}  <-- was discrete 0.2/0.6/1.0")
print(f"  atr_ratio:             {d['atr_ratio']}")

# Validation assertions
assert 'regime_score_raw' in result, "FAIL: missing regime_score_raw"
assert 'ema_alpha' in result, "FAIL: missing ema_alpha"
assert 0.0 <= result['ema_alpha'] <= 1.0, f"FAIL: ema_alpha out of range: {result['ema_alpha']}"
assert 0.0 <= result['regime_score'] <= 1.0, f"FAIL: smoothed score out of range: {result['regime_score']}"
assert 0.0 <= d['b_score'] <= 1.0, f"FAIL: b_score out of range: {d['b_score']}"
assert 0.2 <= d['v_score'] <= 1.0, f"FAIL: v_score out of range: {d['v_score']}"
assert result['status'] in ('TRENDING', 'RANGING', 'CRISIS'), f"FAIL: invalid status: {result['status']}"

print()
print("ALL ASSERTIONS PASSED -- Adaptive EMA engine is production-ready.")
