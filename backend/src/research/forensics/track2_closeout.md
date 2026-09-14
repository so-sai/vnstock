# Track 2 Closeout — interbank_seeder audit (2026-09-14)

File: backend/src/services/macro/interbank_seeder.py (665 lines).

## Hosts / paths in use
- PRIMARY: `https://sbv.gov.vn/lãi-suất1` (URL-encoded) via Playwright
  (domcontentloaded) — parses interbank tenor table (ON/1W/2W/1M/3M/6M/9M).
- FALLBACK: `https://data.vietnambiz.vn/_next/data/<BUILD_ID>/currency-interest-rate.json`
  (hardcoded Next.js build ID — brittle on redeploy, documented risk).
- Defenses: table-signature check, Cloudflare-signature check, JS-render
  check, alert file `data/alerts/sbv_structure_changed.json`, cooldown,
  `sbv-update` 5-step recovery flow.

## Verdict: TRACK 2 OMO/T-BILL REMAINS GREENFIELD
Seeder covers interbank RATES ONLY. Zero code parses OMO repo volumes,
T-bill issuance/maturity, auction rates, or tenors. No internal SBV API
path discovered (no hidden JSON/XHR endpoint in this module).
OMO stays proxy-derived (`-(interbank-3)*10000`) until Missing Quad
Track 2 parser exists. Priority for that parser: SBV OMO announcements
(new source hunt, separate budget) — dttktt portal root timed out in 1b-ii.
