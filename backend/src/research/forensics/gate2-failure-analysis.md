# Gate-2 Failure Forensics — trial 041 (best v2), n=361, top-30 universe, IS 2020-2024

FORENSIC ONLY — re-materialized deterministically from locked params
(k=1.3, lb=15, atr=2.5, rr=2.0). NOT a new trial. N stays 72.
Trades: `ledger/forensic_trial041_trades.json` (361 rows, net matches ledger exactly).

## Q1 — Cost decomposition: COST DOMINATES, gross alone still fails
- Sharpe gross **+0.78** vs net **-0.07** → cost drag **0.85 Sharpe units**.
- Gross +23.6M, costs −17.8M (75% of gross eaten), net +5.8M on 100M NAV / 5y.
- Verdict: costs are the bigger killer, BUT gross +0.78 would still fail DSR>0.95.
  Both problems real; fixing costs alone insufficient.

## Q2 — Turnover: NOT churn, thin edge
- 72 trades/year total = **2.4 /symbol/year**, median hold **10 days**, all 30 symbols traded.
- Per-trade: gross ≈ +65k, cost ≈ −49k. Edge thinner than friction per round-trip.
- Verdict: problem is edge/cost ratio per trade, not overtrading frequency.

## Q3 — Entry timing: mildly buying high
- Median entry **+0.60% above T+3 close**, **+0.31% above T+5**; 53.7% entries above T+5 close.
- Weak bull-trap signature (fractions of a day's noise, not dramatic).
- Verdict: contributes, not decisive.

## Q4 — PnL concentration: extreme tail dependence
- Top-10% trades made **12.28× total net** (≈+71M) while bottom-90% lost ≈−65M.
- Verdict: edge (such as it is) lives in a few tails — not robust, not tradeable at size.

## Q5 — Regime attribution: THE CLUE (post-hoc, hypothesis-generating ONLY)
| Regime at entry | n | Net (M) | Sharpe |
|---|---|---|---|
| TRENDING | 164 | −19.8 | −2.64 |
| RANGING | 160 | +8.7 | +0.59 |
| CRISIS | 26 | +8.3 | +4.88 (small-n, do not overclaim) |
| UNKNOWN | 10 | +9.6 | mirage, excluded |

- Breakout entries LOSE in TRENDING (−2.64) and WIN in RANGING (+0.59).
- Inverted vs textbook: VN "trending" entries = late-stage chasing (tops);
  ranging entries catch mean-reversion bounces. Regime labels may lag — either
  way the split is regime-specific, not uniform.
- ⚠️ This split was NOT pre-declared → exploratory. A "RANGING-only" variant
  must be a NEW pre-declared grid with cumulative N (72+n), never a retrofit.

## Bottom line for next hypothesis
1. Cut friction need: fewer, thicker edges (holding longer won't help — median
   hold already 10d; need bigger per-trade expectancy, not fewer trades).
2. Regime-conditional mean-reversion in RANGING is the only empirically
   supported direction (post-hoc; needs pre-declared confirmation).
3. Breakout-after-thrust on daily VN large-caps 2020-2024: ARCHIVED.
