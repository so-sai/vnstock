# SPEC: Automated Corporate Actions Engine (CA Engine)

> **Type:** future work — **BLOCKED by POST_MORTEM §5.**
> **Status (2026-09-22):** spec only. No branch, no implementation, no
> production changes until reopen conditions (a)(b)(c) ALL hold.
> **Motivation:** FPT 10:1 bonus (ex 2026-09-21) and VCB 450d cash dividend
> (ex 2026-07-23) both bypassed an empty `paper_corporate_actions` table and
> an `adj_close = close` mirror across `daily_ohlcv`. Manual 5-gate audits do
> not scale to 1,454 symbols.

## 1. Architecture (4 closed tiers, runs inside `daily-close`)

```text
[1. INGESTION]          VCI Company.events() / HOSE Calendar
                               │
                               ▼
[2. STAGING & AUDIT]    Auto 5-Gate Verification (G1 → G4)
                               │ (PASS only)
                               ▼
[3. CALCULATION ENGINE] Cumulative Adjustment Factor (CAF) Matrix
                               │
                               ▼
[4. DATA STORE]         Clean writes to daily_ohlcv.adj_close
```

## 2. Tier 1 — Event ingestion

Reuse `Company(symbol, source="VCI").events()` (already in `backend/libs/vnstock/`).
New staging table (proposed):

```sql
CREATE TABLE IF NOT EXISTS corporate_actions_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,   -- CASH_DIV / STOCK_DIV / BONUS_ISSUE / RIGHT_ISSUE / SPLIT
    ex_date TEXT NOT NULL,      -- GDKHQ (YYYY-MM-DD)
    record_date TEXT NOT NULL,  -- DKCC
    ratio REAL,                 -- e.g. 0.1 for 10:1
    cash_amount REAL,           -- e.g. 1500 for 1,500d
    issue_price REAL,           -- rights offering price
    status TEXT DEFAULT 'PENDING',  -- PENDING / VERIFIED / SUSPICIOUS / REJECTED
    crawled_at TEXT NOT NULL,
    UNIQUE(symbol, event_type, ex_date)
);
```

## 3. Tier 2 — Automated 5-gate audit (zero-human path)

On `ex_date` after EOD candles land: fetch cum-date close `P_cum`, compute
expected reference via existing `adjust_reference_price()` VNX formulas
(`backend/src/data/schema_normalizer.py:160-176`):

- STOCK_DIV/SPLIT: `P' = P / (1 + r)`
- CASH_DIV: `P' = max(0.1, P − D)`
- RIGHT_ISSUE: `P' = (P + IP × r) / (1 + r)`
- Dual ex-date: `P' = (P − D) / (1 + r)`

Compare vs actual ex-date reference/open: deviation ≤ 1% → `VERIFIED`;
> 1% → `SUSPICIOUS` + alert log, **never auto-rewrite prices**.
Capital gate (G3): post-event charter ≈ pre × (1+r) via
`financial_facts.CHARTER_CAPITAL` / CafeF `SHARES_OUT`.

## 4. Tier 3 — Cumulative Adjustment Factor (CAF, CRSP/Bloomberg standard)

Backward recursion from today T to past t. Per-day factor `k_t`:

- Stock bonus/stock div (r): `k_t = 1 / (1 + r)`
- Cash div (D): `k_t = (P_cum − D) / P_cum`
- Rights (price P_right, ratio r):
  `k_t = (P_cum + r × P_right) / (P_cum × (1 + r))`
- Normal day: `k_t = 1.0`

Cumulative factor for past day i: `F_i = ∏_{t > i} k_t`.
Adjusted series: `P_adj,i = P_raw,i × F_i`, `V_adj,i = V_raw,i / F_i`.
Result: history free of artificial ex-date gaps; MA/RSI/drawdown read true returns.

## 5. Tier 4 — `daily-close` integration (proposed hook)

```python
def run_corporate_action_pipeline():
    sync_upcoming_corporate_actions()      # Tier 1: VCI → staging
    events_today = get_verified_events_for_date(today)  # Tier 2 gate
    for event in events_today:
        recalculate_symbol_adjusted_series(event.symbol)  # Tier 3 CAF
```

## 6. Rollout (when unblocked)

1. **Historical backfill:** standalone script scanning 2020→now (Top-50 first),
   recompute `adj_close` for affected symbols. NOTE: rewrites history that
   prior backtests (114 trials) ran on — must version the dataset and
   re-baseline, not silently overwrite.
2. **Remove `adj_close = close` hardcodes** (`screener.py`, `daily_updater.py`,
   `backfill_engine.py`, `background_sweep.py`, `hydrate_2020.py`) → route
   through CAF.
3. **Schedule Tier 1–2 job** inside `daily-close` post-16:00.

## 7. Verification precedent (manual 5-gate, 2026-09-22)

- FPT STOCK_DIV r=0.1 ex 2026-09-21: 5/5 raw bars ÷1.1 match VCI adjusted
  series; ref 65,200 = 71,700/1.1; lot 2700→2970 capital-conserved.
- VCB CASH_DIV 450d ex 2026-07-23: multi-source (VCI + HOSE notice via CafeF
  + press); ref check on VCI+KBS consensus (DB 22/07 bar flagged stale);
  lot cb −450, +1.44M dividend pending.
- HPG/TCB/VNM: no ex-dates in 2026-07-01..2026-09-22 window.

Ledger: `backend/src/ingestion/ledger/ingestion-ledger.jsonl`.
