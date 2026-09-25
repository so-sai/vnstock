# SPEC: Multi-Archetype Screener Engines (Cash-Cow / Windfall / J-Curve)

> **Type:** future work — **BLOCKED by POST_MORTEM §5.**
> **Status (2026-09-25):** spec only. No branch, no implementation, no
> production changes until reopen conditions (a)(b)(c) ALL hold.
> **Flag:** `BLOCKED_BY_POST_MORTEM_SECTION_5`.
> **Motivation:** the single-factor (P/E, ROE, EPS) template is structurally
> unable to represent the three distinct earnings archetypes present in the
> VN universe. A cash-rich monopoly, a commodity windfall, and a capex-driven
> J-curve have non-overlapping causal drivers and cannot be scored by one
> formula. This document freezes the design and the data blockers before any
> engine code is written.

---

## 0. Doctrine invariants (binding)

These hold regardless of archetype and are enforced by Governor, not by the
screener:

- **Zero-Hallucination:** no provenance = no trust. A candidate with missing
  PIT-verified inputs is dropped, never imputed.
- **Evidence theo Regime (LAW-010):** every score is normalized within the
  regime distribution at observation time. Absolute thresholds are forbidden.
- **Answer two separate questions:**
  - `MUA CÁI GÌ?` → the three parallel screeners (this spec).
  - `KHI NÀO ĐƯỢC MUA?` → Regime + Governor (existing).

The screeners **propose a candidate pool only**. They never size, never enter,
never bypass the entry lock.

---

## 1. Architecture (3 parallel screens → pool → Governor)

```text
                  ┌────────────────────────────────────────┐
                  │      UNIVERSE (ALL_EXCHANGES)          │
                  └───────────────────┬────────────────────┘
                                      │
       ┌──────────────────────────────┼──────────────────────────────┐
       ▼                              ▼                              ▼
┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
│   SCREENER 1    │          │   SCREENER 2    │          │   SCREENER 3    │
│   CASH-COW      │          │   WINDFALL      │          │   J-CURVE       │
│ dividend fort / │          │ commodity shock │          │ unit-opening /  │
│ net-cash / FCF  │          │ volume spike    │          │ SSSG inflection │
└────────┬────────┘          └────────┬────────┘          └────────┬────────┘
         │                            │                            │
         └────────────────────────────┼────────────────────────────┘
                                      │  (union of passing candidates)
                                      ▼
                  ┌────────────────────────────────────────┐
                  │        CANDIDATE POOL (union)          │
                  │  per-symbol provenance + archetype tag │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │   GOVERNOR / MACRO RAD (existing gate) │
                  │  CRISIS? breadth > 0.40? liquidity open?│
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │   EXECUTABLE BOOK  OR  PAPER WATCHLIST  │
                  │   (ENTRY_LOCK=TRUE ⇒ paper only)        │
                  └────────────────────────────────────────┘
```

Design rules:

1. The three screeners are **independent** — a symbol may pass any subset.
   Archetype is a *tag*, not a mutually exclusive class.
2. Pool membership is **not** permission to trade. Governor remains the sole
   holder of the entry key.
3. When `ENTRY_LOCK = TRUE` (current state), pool output routes to the
   existing paper watchlist only.

---

## 2. Screener 1 — `CASH-COW` (dividend fort & net-cash monopoly)

**Data tier:** traditional BCTC + OHLCV. Cheapest to build. Blocked only by PIT.

### 2.1 Screening questions

| # | Question | Formal test |
|---|---|---|
| 1 | Cash dividend yield beats deposit rate? | `DPS_ttm / close >= 0.08` (configure 8–10%) |
| 2 | Net cash, not net debt? | `NetDebt = TotalDebt − Cash&Equivalents <= 0` |
| 3 | Free cash flow real and persistent? | `FCF = CFO − CAPEX > 0` for 3 consecutive years |
| 4 | Cash conversion quality? | `FCF / NetIncome` sustained, not one-off |
| 5 | Margin stability (no price war)? | `stdev(GrossMargin) <= threshold` over 3y |
| 6 | Liquidity mandate (AGENTS.md)? | `ADV_20D >= 50_000` shares |

### 2.2 Metric definitions

```text
DPS_ttm        = sum of cash dividends with ex_date in trailing 365d
DividendYield  = DPS_ttm / latest_close
NetDebt        = ShortTermBorrowings + LongTermBorrowings - (Cash + STInv)
FCF_y          = CFO_y - CAPEX_y
FCF_Yield      = FCF_ttm / market_cap
GrossMargin_y  = (Revenue_y - COGS_y) / Revenue_y
```

All `y` values must be read **as of the observation date** using
`publication_date <= obs_date` (see §5 PIT blocker).

### 2.3 Expected candidates (hypothesis, not evidence)

BMP, VEA, QNS, SMB — named as *research names*, not recommendations.

---

## 3. Screener 2 — `WINDFALL` (commodity shock / supply-demand)

**Data tier:** requires real-time world commodity sensors. Currently blocked.

### 3.1 Screening questions

| # | Question | Formal test |
|---|---|---|
| 1 | Output price shock vs stable input price? | `ΔOutput_spot > +20%` AND `|ΔInput_spot| < 5%` |
| 2 | Margin spread expansion? | `CrackSpread_t > CrackSpread_MA60 × 1.2` |
| 3 | Early accumulation before the print? | `Volume_t >= 1.8 × ADV_20D` with OBV up |
| 4 | Confirmation not yet priced in? | signal fires *before* BCTC release date |

### 3.2 Required sensors (none currently healthy)

| Product | Symbol examples | Required series | Status |
|---|---|---|---|
| Yellow phosphorus | DGC | China 100ppi spot | NOT CONNECTED |
| Brent / SG crack | BSR, PVD | Brent + Singapore crack spread | NOT CONNECTED |
| Container freight | HAH | BDI / SCFI | NOT CONNECTED |
| Iron ore | HPG | Dalian spot | `unhealthy` (2026-09-25) |

`VolumeSpike = Volume_t / mean(Volume_{t-20..t-1})`.

### 3.3 Why BCTC cannot substitute

Quarterly reports lag the price move. By the time net income prints, the
commodity peak is typically already reflected in price. This screener is
therefore **required** to run on commodity sensor + volume, not on BCTC.

---

## 4. Screener 3 — `J-CURVE` (unit expansion / SSSG inflection)

**Data tier:** non-financial operating data — does not exist in any current DB.

### 4.1 Screening questions

| # | Question | Formal test |
|---|---|---|
| 1 | Revenue accelerating while stores open? | `g(Revenue) > 25%` AND `ΔStoreCount > 0` |
| 2 | Same-store sales positive? | `SSSG = (Rev_old_stores_t / Rev_old_stores_{t-1}) − 1 > 0` |
| 3 | Gross margin holds during expansion? | `GrossMargin_t >= GrossMargin_{t-4q} − buffer` |
| 4 | Capex/PPE expansion precedes EPS? | `CAPEX/Depreciation > 1`, PPE growing |

### 4.2 Data blockers

| Required field | Where it lives today | Gap |
|---|---|---|
| Store count time-series | IR slides / monthly releases / website | no historical series; web crawl only yields today |
| Store-level revenue (SSSG) | IR presentations / PDF reports | not machine-readable, not PIT |
| Sq-meter / area | annual report footnotes | manual curation required |
| Capex/PPE by vintage | BCTC (partial) | available but not PIT-verified |

VN standard balance sheets do **not** carry store count or revenue-per-store.
A backtest-grade 2020→2025 series requires manual curation from IR material —
the same class of work that **HALTed Track 1**.

---

## 5. Cross-cutting data blockers (must clear before ANY engine code)

### B1 — PIT poisoning in `financial_facts` (critical)

- Table `financial_facts` stores `reported_at` but the current population is
  reported as 200/200 records failing the PIT guard: **no verified
  `publication_date`** linked to each `(symbol, period, metric)`.
- Without the actual UBCK filing timestamp, a Q2-ending `30/06` fact is
  readable on `01/07` → **look-ahead leak**.
- Every metric in §2 and §4 reads this table. Building engines now = building
  on sand.
- Required fix: a publication-timestamp reconciliation table per symbol/period,
  independently audited, before any screener is allowed to read the DB in a
  backtest.

### B2 — Commodity sensors (§3)

- No contractual/free source wired for phosphorus / crack / BDI.
- Scraping third-party portals is brittle (HTML drift, IP blocks, CAPTCHA)
  and constitutes no provenance. Licensed feeds (Platts, Fastmarkets, Mysteel)
  cost ~15k–30k USD/yr.
- Live proof of fragility: `IRON_ORE sensor unhealthy`.

### B3 — Non-financial operating data (§4)

- No store-count / SSSG pipeline exists, and no historical source is
  crawlable. This is a manual-curation project, not a script.

### B4 — Corporate Action hygiene (shared dependency)

- `adj_close = close` mirror across `daily_ohlcv`; `paper_corporate_actions`
  = 0 rows. Any dividend-yield or price-based screen is wrong across ex-dates
  until the CA Engine ships. See `SPEC_CA_ENGINE_2026-09.md`.

---

## 6. Governor integration (when engines are eventually unblocked)

```python
def build_candidate_pool(obs_date):
    pool = {}
    for engine in (screen_cash_cow, screen_windfall, screen_jcurve):
        for sym in engine(obs_date):            # screeners are read-only
            pool.setdefault(sym, set()).add(engine.archetype)
    return pool                                  # tagged, not sized

def governor_gate(pool, obs_date):
    decision = macro_rad.evaluate(obs_date)      # regime / breadth / liquidity
    if decision.entry_lock:
        write_paper_watchlist(pool)              # observation only
        return []
    return rank_within_regime(pool, decision)    # LAW-010 normalization
```

- Screener 1/2/3 never call the broker and never set sizes.
- In `CREDIT_STRESS` / `CRISIS`, every candidate — regardless of archetype —
  goes to paper only.
- The Governor freeze in POST_MORTEM §6 remains in force: this spec does not
  authorize Governor changes.

---

## 7. Unblock conditions (identical to POST_MORTEM §5)

No screener code may be written until ALL hold:

- **(a)** New PIT-verified data source with documented `publication_date`,
  not scraped from a live portal. Applies to B1 (and B2/B3 if those
  archetypes are to be built).
- **(b)** `AUC >= 0.58` on `N >= 800`, `p < 0.01`, replicated out-of-sample
  on a holdout of `>= 6` months.
- **(c)** Explicit approval — not implied by passage of time.

Until then: this document is the only permitted artifact. **Zero engine code.**

---

## 8. Rollout sequence (post-unblock, illustrative)

1. **B1 first:** publish-timestamp reconciliation + audit; re-baseline any
   prior backtest that read these facts.
2. **Screener 1** on PIT-clean BCTC + CA-adjusted prices. Smoke + walk-forward
   on IS only.
3. **B2 second:** license or wire one commodity sensor with a documented
   fallback and a `sensor_validation` health gate. Then Screener 2.
4. **B3 last:** manual-curated store/SSSG dataset with a validation pipeline.
   Only then Screener 3.
5. Each engine ships independently behind the pool interface (§6); none may
   touch sizing or the entry lock.

---

## 9. Explicit non-goals

- Do not merge the three archetypes into one score.
- Do not rank by a single P/E / ROE / EPS formula.
- Do not backfill missing fundamentals by imputation.
- Do not wire scrapers to paid-news portals as if they were provenance.
- Do not enable any engine while `ENTRY_LOCK = TRUE`.

---

## 10. References

- `docs/POST_MORTEM_TIER2_2026-09.md` §5 (reopen conditions), §6 (production state)
- `docs/SPEC_CA_ENGINE_2026-09.md` (B4 dependency)
- `backend/src/financial/financial_facts.py:788` (`financial_facts` schema,
  `reported_at`)
- `backend/src/governor/regime_classifier.py` (LAW-010 regime normalization)
- `backend/src/sensors/world_sensor.py` (sensor registry pattern to follow)
- `backend/src/sensors/sensor_validation.py:148` (`sensor_validation` health gate)
- `AGENTS.md` (ADV_20D ≥ 50k; Zero-Hallucination; regime evidence)
