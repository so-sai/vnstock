# A9 Cross-Asset Regime Audit — DESCRIPTIVE, 0 trials (2026-09-14)

Branch: data/missing-quad-p1. Window IS 2021-04–2024-12. No Governor/M writes.

## Japan leg: UNTESTABLE
JGB10Y/JGB30Y/JPY absent from macro_history. Headline "JGB 30Y 4.18% /
highest in history" NOT imported (no provenance). No causal claim made.
A9 cannot answer whether Japan node belongs in M until ingestion exists.

## Runnable scope (n≈600-650 obs/horizon)
USA {DXY, US10Y} vs +China/Gold {USD_CNY, GOLD_XAU} → VNINDEX Fwd5/20/30/60.

| h | R² US | R² +China/Gold | Δ | h1→h2 stability |
|---|---|---|---|---|
| 5 | 0.028 | 0.039 | +0.011 | 0.068→0.017 |
| 20 | 0.022 | 0.029 | +0.007 | 0.040→0.018 |
| 30 | 0.017 | 0.027 | +0.010 | 0.036→0.017 |
| 60 | 0.013 | 0.024 | +0.011 | 0.030→0.015 |

Correlations (correct signs, tiny): DXY −0.10..−0.15, US10Y −0.09..−0.13,
USD_CNY −0.09..−0.11, GOLD +0.02..+0.06 (≈noise).

## Verdict
1. Cross-asset daily changes explain **2-4%** of VN forward variance.
   Directionally sensible, magnitude untradeable alone.
2. China/Gold adds **~1pp incremental R²** — real but marginal, NOT the
   missing global-regime representation by itself.
3. Signal **halves across halves** (h1 2×h2): unstable, regime-dependent.
4. Japan question OPEN. Recommendation: ingest JGB/JPY series first
   (Missing Quad extension), re-run A9, then decide on M — in that order.
   No P_cap change, no Governor change, no new node.
