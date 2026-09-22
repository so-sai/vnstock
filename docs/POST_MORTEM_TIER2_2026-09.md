# POST-MORTEM: Tier-2 R&D Phase (Closed 2026-09-17)

## 1. Verdict

**Goal:** Net return > VNINDEX + 8%/yr, MDD < 15%, N ≤ 25 trades/yr
**Result:** UNREACHABLE with infrastructure available as of 2026-09.

Three independent kill vectors converge on the same terminal conclusion:
no signal with positive edge after friction exists in the current feature set
for VN 2020-2024.

## 2. Evidence

### 2.1 Execution layer (114 trials, 4 hypotheses)

| Hypothesis | Trials | DSR Gate | Result |
|---|---|---|---|
| v1 — Linkage | 36 | < 0.95 | FAIL |
| v2 — Breakout | 36 | < 0.95 | FAIL |
| H3 — Gold regime | 18 | < 0.95 | FAIL |
| H4 — Mean-reversion | 24 | < 0.95 | FAIL |

Hypothesis class `technical_price_only_daily_ohlcv` invalidated.
Branch `research/tier2-h4-mean-reversion` archived.

### 2.2 Feasibility test — Path C macro switching

Macro regime switching on 47 variables from `macro_history`:
- MDD = -40.52% (indistinguishable from buy-and-hold)
- CAGR ≈ -0.02%
- Dead on arrival. 0 trials consumed.

### 2.3 AUC non-replication (Go/No-Go gate)

| Variant | N | AUC(A) DD>=5% | AUC(B) Fwd10>=+2% | Verdict |
|---|---|---|---|---|
| Restricted (with INTERBANK+VIX) | 217 | 0.5831 (p=0.01) | 0.5954 (p=0.01) | GO_RESTRICTED |
| Full IS (drop INTERBANK+VIX) | 1192 | 0.5134 (p=0.26) | 0.4641 (p=0.98) | **NO_GO** |

AUC at N=217 was a local artifact of 2023-2024 regime homogeneity.
Full sample including 2022 collapse: discriminatory power = random.

This is the most important result. It proves the signal does not exist
outside a narrow window — not that the model is wrong.

### 2.4 Cross-asset audit (A9/A9x6)

- US factors explain 1–3% of forward VNI return variance
- +China/Gold adds ~1–2pp incremental (R² 2–4%)
- Gold divergence cells (G-V+) show 81% hit rate (POST-HOC, hypothesis fuel only)
- Signal decay across sub-samples: R² halves from first to second half
- CNY incremental information ~2x Gold's increment at h60
- Japan: UNTESTABLE — no JGB/JPY in database (no provenance = no trust)

### 2.5 Missing Quad

| Track | Status | Blocker |
|---|---|---|
| 1 — HOSE matching | HALT | No authenticated broker API |
| 2 — OMO/T-bill | HALT | No data source (SBV portal timed out) |
| 3 — SBV FX | PASS | Parser + audit verified (379/TB-NHNN) |
| 4 — KBNN yield | PARTIAL | Oracle ADF form blocks extraction |

## 3. Terminal conclusions

1. **Technical price-only (OHLCV daily):** Dead after friction in VN market.
   114 trials across 4 hypotheses, 0 pass Gate 2.

2. **Macro switching on 47 variables:** Dead. Signal does not robustly
   discriminate across regimes. AUC non-replication confirms this.

3. **Cross-asset features (Gold/CNY):** Marginal incremental information
   (R² ~2-4%) but insufficient for trading. Not a missing master variable.

4. **Production system:** Defensive behavior validated. Avoided most of 2022
   drawdown. But defensive correctness != predictive correctness. System
   says "stay out" correctly — it cannot say "get in."

## 4. What was preserved

| Artifact | Location | Value |
|---|---|---|
| DSR module | `backend/src/research/modules/calculate_dsr.py` (archived branch) | Reusable |
| Walk-forward harness | `backend/src/research/modules/pure_ohlcv_wf.py` (archived branch) | Reusable |
| Ledger infrastructure | `backend/src/ingestion/ledger/` (archived branch) | Active |
| Backfill policy | Locked in ingestion-ledger (archived branch) | Governance |
| Track 3 SBV FX parser | Promoted to main (`backend/src/ingestion/parsers/sbv_fx_parser.py`) | Staging |
| Track 3 probe_helper | Promoted to main (`backend/src/ingestion/probe_helper.py`) | Tooling |
| Track 3 audit script | Promoted to main (`backend/src/ingestion/audits/audit_sbv_fx_track3.py`) | Audit |
| Track 3 staging DB | **LOCAL ONLY** — `backend/data/staging_microstructure.db` (gitignored) | **Not in repo** |
| Breadth engine | `backend/src/analysis/market_wide_breadth.py` | Active |
| A9/A9x6 audit scripts | `backend/src/research/modules/` (archived branch) | Evidence |
| Go/No-Go gate | `backend/src/research/modules/run_gonogo_gate.py` (archived branch) | Evidence |
| Prior: strong negative | Throughout repo | Scientific asset |

> **Known limitation (2026-09-22, infrastructure note — not a reopen):**
> Corporate Action pipeline: `adjust_reference_price()` exists in code but is orphaned;
> `paper_corporate_actions` = 0 rows; `adj_close = close` mirror across entire database.
> Known infrastructure gap, strictly frozen and not reopened without conditions (a)(b)(c).

## 5. Reopen conditions

R&D may ONLY be reopened when ALL of the following hold:

(a) **New PIT-verified data source** with documented `publication_date`,
    not scraped from live portal. Examples: broker API with order matching
    data, SBV OMO with confirmed timestamp, KBNN yield with reverse-engineered
    form.

(b) **AUC >= 0.58 on N >= 800 with p < 0.01**, replicated out-of-sample
    on a holdout period of >= 6 months.

(c) **Explicit approval** — not implied by passage of time.

Without (a), (b), and (c): no new trials, no new hypotheses, no feature
additions.

## 6. Production state

- **Portfolio:** Cash 100%, no positions
- **Signal:** DUNG NGOAI / SURVEILLANCE
- **Entry lock:** TRUE
- **daily-close:** Running with `--skip-sync` (18.6s)
- **Governor:** Frozen. No changes permitted.
- **Breadth engine:** Active (ALL_EXCHANGES_UNWEIGHTED)
- **Track 3:** Parser promoted to main. Staging DB local, not in repo.

## 7. FTSE Russell review — 18/09/2026

Natural experiment, not a model input. System will observe:
- Forecast flow (SSI estimate $245.92M net) vs actual foreign/ETF flow
- Price/volume response on 27 reclassified codes
- Persistence of flow over T+1, T+3

No pre-positioning. Lock remains until breadth > 0.7 + vol recovery
+ O/N < 2% sustained — and even then, observation only, not automatic entry.

## 8. Meta-state

Three independent kill vectors (execution, feasibility, signal) converging
on the same conclusion is not failure — it is a validated negative result.
The system correctly identified that its own hypothesis does not hold.

This is the most valuable output of the R&D phase: a rigorous map of what
does not work, with quantitative evidence, archived for future reference.

## 9. Ledger & archive references

- Research branch archived: `archive/research-closed-2026-09-17`
  (ref: `data/missing-quad-p1` @ `ba4033bd94c42839cfe6a22d6040252585b19f50`)
- Research ledger (archived): `backend/src/research/ledger/evidence-ledger.jsonl`
  (on branch `research/tier2-h4-mean-reversion`, 29 entries)
- Ingestion ledger (archived): `backend/src/ingestion/ledger/ingestion-ledger.jsonl`
  (on branch `data/missing-quad-p1`)
- Track 3 promotion commit: see `ops(ingestion): promote SBV FX parser...` on main
- Staging DB: local only, not tracked (see `.gitignore` → `data/`)
- This document is the canonical closeout record on main.
