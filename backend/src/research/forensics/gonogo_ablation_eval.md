# Go/No-Go Gate — Ablation Test (0 trials, 0 production changes)

Date 2026-09-14. Branch `data/missing-quad-p1`.

## Setup

- **Ablation:** DROP INTERBANK_ON + VIX → expands N from 217 to 1192
- **Features retained:** US10Y (level + Δ20d), DXY (ROC20), USD_CNY (ROC20), GOLD (ROC20), breadth_pct, ad_ratio
- **Period:** 2021-04 → 2024-12 (covers both 2021-2022 crash AND 2023-2024 recovery)
- **Method:** Same as original gate — Logistic Regression, Purged K-Fold (K=5, embargo=10d), AUC-ROC, permutation p-value (n=100)

## Results

| Target | AUC-ROC | p-value | vs N=217 |
|---|---|---|---|
| A: Fwd20d DD ≥ 5% | **0.5134** | **0.26** | 0.5831 → 0.5134 |
| B: Fwd10d Ret ≥ +2% | **0.4641** | **0.98** | 0.5954 → 0.4641 |

**N = 1192. Prevalence: A = 26.0%, B = 35.2%.**

## Verdict: NO_GO

Both AUCs collapse to chance (~0.50) on the full sample. p-values are > 0.05 (no statistical significance).

**The 0.58 AUC observed at N=217 was a local artifact of the 2023-2024 sub-sample.**

## Implications

1. **Macro features have NO discrimination power on the full 2021-2024 cycle.**
2. **The "risk-sentinel" hypothesis is killed** — macro cannot reliably warn of drawdowns across regimes.
3. **No causal decision engine should be built** on this feature set.
4. **Signal at N=217 was spurious** — small sample, concentrated in a favorable regime window.

## FULL FREEZE

All R&D activity frozen. System enters passive observation mode.
Next event: FTSE reclassification observed trading 18/09/2026.
