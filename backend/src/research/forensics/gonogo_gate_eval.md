# Go/No-Go Gate — Information Diagnostic (0 trials, 0 production changes)

Date 2026-09-14. Branch `data/missing-quad-p1`.

## Setup

- **Features:** US10Y (level + Δ20d), DXY (ROC20), USD_CNY (ROC20), GOLD (ROC20), VIX (level), breadth_pct, ad_ratio
- **Method:** Logistic Regression, Purged K-Fold CV (K=5, embargo=10d), AUC-ROC, permutation p-value (n=100)
- **N after merge:** 217 (CONSTRINED by INTERBANK_ON from 2023 + VIX monthly sparse)
- **Prevalence:** Target A (DD≥5%) = 31.3%, Target B (Fwd10d≥2%) = 42.4%

## Results

| Target | AUC-ROC | p-value | Threshold |
|---|---|---|---|
| A: Fwd20d DD ≥ 5% | **0.5831** | **0.01** | ≥ 0.58, p < 0.05 ✓ |
| B: Fwd10d Ret ≥ +2% | **0.5954** | **0.01** | ≥ 0.60, p < 0.01 ✗ (0.5954 < 0.60) |

## Verdict: GO_RESTRICTED

Macro features **do** discriminate downside hazard (AUC(A) = 0.5831 ≥ 0.58, p = 0.01).

Macro features **do not** reach the full GO threshold on upside (AUC(B) = 0.5954 < 0.60).

**Interpretation:** Existing macro features are useful as a **risk-sentinel** (downside alert) but **not as a buy-signal engine**.

## Critical Caveats

1. **N = 217 is small.** INTERBANK_ON starts 2023-01-02, VIX has only ~322 rows (monthly-ish). Sample constrains power.
2. **No hyperparameter search was done.** Logistic Regression with default C=0.5. Could try other models, but that would move toward trial territory.
3. **Purged but not OOS.** This is IS cross-validation, not a forward walk.
4. **Feature set is PIT-clean but incomplete.** No Japan, no Credit Impulse, no SBV OMO.

## Implications

- **Do NOT kill macro hypothesis.** The signal is weak but real.
- **Do NOT build Causal Decision Engine yet.** AUC(B) < 0.60 = insufficient for buy permission.
- **Risk Sentinel viable?** AUC(A) = 0.5831 is marginal. Real-world AUC degrades with transaction costs, slippage, and regime shifts. Need validation on 2025 OOS before any deployment claim.
- **Priority:** Increase N by resolving INTERBANK_ON (daily frequency) and VIX (daily frequency) ingestion gaps. Re-run gate with larger sample.
