# Phase 1b-i Source Probe — Repo Audit + Bond Sub-domains

Branch: data/missing-quad-p1. Date: 2026-09-14.
Order: audit → helper → probes. 0 parser, 0 DB writes.

## Task 1 — Repo audit (SBV/macro provenance)

- **No `providers/sbv*` module.** SBV access lives ONLY in
  `backend/src/services/macro/interbank_seeder.py`: SBV interbank page via
  Playwright (+ vietnambiz JSON fallback). Code documents Cloudflare blocks;
  alert/recovery flow via `ptck.py sbv-update`. Verdict: FRAGILE, alive.
- **INTERBANK_ON (954 rows, 2023+)**: `refresh_interbank_rate()` scrape +
  `bootstraps/interbank_bootstrap.py` HARDCODED quarterly seeds 2023-2026.
  Provenance MIXED (scrape + seed) — flag for future PIT review.
- **OMO/T-bill**: ZERO real fields. OMO exists ONLY as computed proxy
  (`regime_classifier`: -(interbank-3)*10000; `liquidity_recovery_index`:
  "use INTERBANK_ON as proxy"). Track 2 GREENFIELD confirmed.
- **SBV central_rate/ask/bid/intervention**: ZERO code hits. Track 3 GREENFIELD.
  (USD_VND series exists from vendor/WorldSensor — NOT SBV provenance.)
- **VGB10Y**: `vgb10y_seeder.py` (World Bank API) — 91 seeded rows. Not PIT.

## Task 2 — probe_helper.py codified
`backend/src/ingestion/probe_helper.py` (ruff clean, smoke-tested vs
example.com): channel=chrome, domcontentloaded+sleep, UTF-8, server Date
header → tier live/forward/backfill, SHA256 raw hash, table inventory.

## Task 3-4 — Bond sub-domain probes (1 each)

**bonds.hnx.vn → FAIL (TLS).**
`ERR_CERT_COMMON_NAME_INVALID` on HTTPS. No HTTP fallback attempted
(time-box). Candidate NOT dead, but needs cert workaround + re-probe
in Phase 1b-ii/1c.

**vst.mof.gov.vn (KBNN portal) → PASS (candidate CONFIRMED).**
Renders under Chrome: 17 tables, incl. module **"THÔNG TIN ĐẤU THẦU"**
(auction info), "TỶ GIÁ HẠCH TOÁN", FX table sourced `www.sbv.gov.vn`.
This is the first VERIFIED page with auction-content presence for Track 4
(KBNN primary auctions = immutable admin docs per approved order).

## Phase 1b-ii readiness
- SBV probes (`sbv.gov.vn` homepage + OMO section) NOT run — gated on this
  report per approved split. Repo audit shows no existing SBV OMO/FX parser
  to reuse, so 1b-ii probe is justified whenever operator orders it.
- Backfill tiers (live/forward/backfill) LOCKED per operator approval.
