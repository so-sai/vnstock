"""
=======================================================================
 SHADOW TRACKER V1.0 - MODULE DOI SOAT DOC LAP (L4.5)
 Alpha Forge Backend V1.0 Unified
=======================================================================
 NGUYEN TAC: Module nay lay chu ky gia tu L0 Vault (SQLite) thay vi API.
 No doi xet giua 'Reality' (JSON) va 'Market' (SQLite).

 CHAY: .venv\\Scripts\\python.exe src/portfolio/shadow_tracker.py
=======================================================================
"""

import json
import os
import sys
from datetime import datetime

# --- SENTINEL PATH PROTECTION (v2.1 Anchor Fix) ---
def _hydrate_path():
    import os, sys
    from pathlib import Path
    current = Path(__file__).resolve().parent
    root_path = current
    while current != current.parent:
        if (current / ".kit").exists() or (current / "src").exists():
            root_path = current
            break
        current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.db_core import get_connection

# --- CONSTANTS ---
PORTFOLIO_PATH = os.path.join(PROJECT_ROOT, "src", "portfolio", "my_portfolio.json")
STOP_LOSS_THRESHOLD = -5.0   # % - Nguong cat lo (Rumor Shield)

def _load_portfolio() -> dict | None:
    if not os.path.exists(PORTFOLIO_PATH):
        print(f"❌ [LOI] Khong tim thay so cai tai: {PORTFOLIO_PATH}")
        return None
    try:
        with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ [LOI] File JSON bi hong: {e}")
        return None

def _get_latest_price_from_vault(symbol: str) -> float | None:
    """ Truy van gia close moi nhat tu L0 Vault (SQLite) """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT adj_close FROM daily_ohlcv 
                WHERE symbol = ? 
                ORDER BY date DESC LIMIT 1
            ''', (symbol,))
            row = cursor.fetchone()
            return row[0] if row else None
    except:
        return None

def _format_vnd(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:.2f} ty VND"
    return f"{amount / 1_000_000:.2f} tr VND"

def shadow_reconcile():
    print("\n" + "="*70)
    print("🛡️  ALPHA FORGE V1.0 - SHADOW RECONCILIATION REPORT")
    print(f"⏰ Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    data = _load_portfolio()
    if not data: return

    positions = data.get("positions", [])
    cash = data.get("cash", 0)
    
    print(f"💰 Cash in Hand: {_format_vnd(cash)}")
    print("-" * 70)

    total_cost = 0
    total_market_value = 0
    alerts = []

    for pos in positions:
        sym = pos['symbol']
        qty = pos['quantity']
        buy_price = pos['entry_price'] # k VND
        fee_rate = pos.get('fee_paid', 0.0015)

        # Tinh chi phi (co phi)
        cost_raw = qty * buy_price * 1000
        cost_with_fee = cost_raw * (1 + fee_rate)
        total_cost += cost_with_fee

        # Lay gia thi truong tu Vault
        market_price = _get_latest_price_from_vault(sym)
        
        if market_price is None:
            print(f"⚠️  [{sym}] Khong tim thay gia trong Vault (L0). Hay chay screener.py!")
            continue

        market_value = qty * market_price * 1000
        total_market_value += market_value
        
        pnl_pct = ((market_price - buy_price) / buy_price) * 100
        pnl_vnd = market_value - cost_with_fee
        
        status = "🔥" if pnl_pct >= 0 else "❄️"
        print(f"{status} [{sym:<5}] PnL: {pnl_pct:+.2f}% | Value: {_format_vnd(market_value)} | Gain: {_format_vnd(pnl_vnd)}")

        if pnl_pct <= STOP_LOSS_THRESHOLD:
            alerts.append(f"🚨 [RUMOR SHIELD] {sym} vi pham {STOP_LOSS_THRESHOLD}% (Gia hien tai: {market_price:.2f}k)")

    # Tong ket P&L
    if total_cost > 0:
        total_pnl_pct = ((total_market_value - total_cost) / total_cost) * 100
        total_nav = total_market_value + cash
        
        print("-" * 70)
        print(f"📈 TOTAL COST  : {_format_vnd(total_cost)}")
        print(f"📉 MARKET VAL  : {_format_vnd(total_market_value)}")
        print(f"💎 TOTAL NAV   : {_format_vnd(total_nav)}")
        print(f"📊 PORTFOLIO RS: {total_pnl_pct:+.2f}%")
        
    if alerts:
        print("\n" + "!"*70)
        for a in alerts: print(a)
        print("!"*70)

    print("="*70 + "\n")

if __name__ == "__main__":
    shadow_reconcile()
