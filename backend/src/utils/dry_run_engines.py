"""
DRY RUN: Kiểm tra output thực tế của Regime Engine và Screener Logic
Chạy từ: backend/
"""
import sys
import os
import json
from pathlib import Path

def _hydrate_path():
    if getattr(sys, 'frozen', False):
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

print("=" * 70)
print("🧪 DRY RUN: ENGINE OUTPUT VERIFICATION")
print("=" * 70)

# ============================================================
# TEST 1: Regime Engine
# ============================================================
print("\n" + "=" * 70)
print("TEST 1: regime_engine.detect_regime()")
print("=" * 70)

try:
    from src.engine.regime_engine import detect_regime
    result = detect_regime()
    print(f"\n📦 TYPE: {type(result)}")
    print(f"📦 KEYS: {result.keys() if isinstance(result, dict) else 'N/A'}")
    print(f"📦 OUTPUT:\n{json.dumps(result, indent=2, default=str)}")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# ============================================================
# TEST 2: Screener Logic
# ============================================================
print("\n" + "=" * 70)
print("TEST 2: screener_logic.run_screener()")
print("=" * 70)

try:
    from src.engine.screener_logic import run_screener
    result = run_screener()
    print(f"\n📦 TYPE: {type(result)}")
    if hasattr(result, 'empty'):
        print(f"📦 EMPTY: {result.empty}")
    if hasattr(result, 'columns'):
        print(f"📦 COLUMNS: {list(result.columns)}")
    if hasattr(result, 'to_dict'):
        records = result.to_dict(orient='records') if not getattr(result, 'empty', True) else []
        print(f"📦 ROWS: {len(records)}")
        if records:
            print(f"📦 FIRST ROW:\n{json.dumps(records[0], indent=2, default=str)}")
    else:
        print(f"📦 OUTPUT: {result}")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# ============================================================
# TEST 3: RS Ranker (load_rs_data)
# ============================================================
print("\n" + "=" * 70)
print("TEST 3: rs_ranker.load_rs_data()")
print("=" * 70)

try:
    from src.engine.rs_ranker import load_rs_data
    data = load_rs_data()
    print(f"📦 TYPE: {type(data)}")
    print(f"📦 SYMBOLS COUNT: {len(data)}")
    if data:
        first_key = list(data.keys())[0]
        print(f"📦 FIRST SYMBOL: {first_key}")
        print(f"📦 FIRST DATA: {json.dumps(data[first_key], indent=2, default=str)}")
        # Show top 5 by rs_rating
        sorted_items = sorted(data.items(), key=lambda x: x[1].get('rs_rating', 0), reverse=True)
        print(f"\n🏆 TOP 5 BY RS RATING:")
        for sym, d in sorted_items[:5]:
            print(f"  {sym}: rs_rating={d.get('rs_rating')}, price={d.get('price')}, rvol={d.get('rvol')}")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# ============================================================
# TEST 4: Breadth Engine
# ============================================================
print("\n" + "=" * 70)
print("TEST 4: breadth_engine.run_breadth_analysis()")
print("=" * 70)

try:
    from src.engine.breadth_engine import run_breadth_analysis
    result = run_breadth_analysis()
    print(f"\n📦 TYPE: {type(result)}")
    if result:
        print(f"📦 KEYS: {result.keys()}")
        print(f"📦 OUTPUT:\n{json.dumps(result, indent=2, default=str)}")
    else:
        print("📦 OUTPUT: None (empty DB or no data)")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# ============================================================
# TEST 5: Macro History (latest from DB)
# ============================================================
print("\n" + "=" * 70)
print("TEST 5: Latest Macro History from DB")
print("=" * 70)

try:
    from src.database.db_core import get_connection
    import pandas as pd
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT variable, date, value FROM macro_history 
            WHERE date = (SELECT MAX(date) FROM macro_history)
            ORDER BY variable
        """, conn)
    print(f"📦 ROWS: {len(df)}")
    print(f"📦 DATA:")
    for _, row in df.iterrows():
        print(f"  {row['variable']}: {row['value']} (date: {row['date']})")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("✅ DRY RUN COMPLETE")
print("=" * 70)
