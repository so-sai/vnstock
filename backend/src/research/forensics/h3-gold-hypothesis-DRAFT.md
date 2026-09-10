# Hypothesis H3: Macro → Gold rotation (CASH ↔ GOLD_XAU) — DRAFT, NOT APPROVED

Branch: research/tier2-macro-gold (cut from main). Status: AWAITING OPERATOR SIGN-OFF.
No code written. No trial run. N stays 72 until grid executes.

## A-priori rationale
Gold rallies when real-rate pressure falls and liquidity stress is contained;
it sells off when rates spike. VN domestic premium (SJC +5.12% Sep-2026) is a
separate friction — strategy trades XAU proxy direction, NOT SJC capture.

## PIT-safe inputs ONLY (per macro_pit_audit 2026-09-10)
- GOLD_XAU (2021-04-05+, n=17917) — traded series.
- US10Y (2021-04-05+) — global rate pressure proxy (caveat kept).
- INTERBANK_ON (2023-01-02+) — VN liquidity stress; gate OFF where missing.
- FED_TARGET_RATE (2021-01-01+) — cost-of-capital level.
- EXCLUDED: VGB10Y (91 seeded rows), BREAKEVEN_INFLATION (91 rows),
  financial_facts.* (bulk-backfill, no publication_date).
- BONDS LEG: excluded — no PIT-safe VN bond price history exists.

## Window: 2021-01-01 → 2024-12-31 (GOLD_XAU coverage bound).

## Proposed signal (to be pre-declared on approval)
- ENTER gold: US10Y 60d pp-drop > d AND GOLD_XAU close > MA_g AND IB_ON < cap.
- EXIT to cash: US10Y 60d pp-rise > d OR trailing ATR OR max hold H.
- Sizing: fixed fractional risk; costs: fee 0.15%/side + slippage (no 0.1% sell
  tax — commodity proxy, to be confirmed vs broker reality; documented).

## Proposed grid (OPERATOR MUST LOCK — proposal only)
- d (rate-drop pp): [0.3, 0.5, 0.75] (3)
- MA_g (days): [20, 50, 100] (3)
- H (max hold, days): [20, 60] (2)
- → M = 3×3×2 = 18 new trials; cumulative N = 72 + 18 = 90 for DSR.
- Gate-2 thresholds unchanged: DSR>0.95, PF≥1.6, MDD>−15%.

## Open items for operator
1. Lock grid values (or amend).
2. Confirm sell-tax treatment for gold proxy.
3. Confirm single-series (GOLD_XAU) scope acceptable vs multi-asset ambition.
