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

## Phase 1b-ii results (2026-09-14, probe_helper exclusively)

SBV homepage renders (tier live, server_time present) but thin: 6085 chars,
0 tables — nav links only. Nav mining found 4 distinct targets, NO OMO
section link anywhere in 250 homepage links.

**SBV.1 Track 3 (Tỷ giá) → PASS (candidate CONFIRMED).**
`sbv.gov.vn/tỷ-giá` renders 3 DOM tables: central rate 1 USD = 25,607 VND
(doc 379/TB-NHNN, issued 14/09/2026 — same-day fresh); Sở GD reference
(USD Mua 24,377 / Bán 26,837 + 6 currencies); 28-row cross-rate table.
Tier live. intervention_type: NONE baseline derivable; SPOT/FORWARD only
from separate announcements (not on this page) — documented, not assumed.

**SBV.2 Track 2 (OMO) → FAIL this session.**
`dttktt.sbv.gov.vn/` root: connection timeout (23.6s). Deep paths
(lsttlnh etc. used by interbank_seeder) NOT tested — out of time-box.
No OMO auction table located. Do NOT chase without new budget; OMO stays
proxy-only until a reachable source is proven.
