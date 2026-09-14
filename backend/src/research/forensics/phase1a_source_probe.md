# Phase 1a Source Probe — Missing Quad Tracks 1 & 4

Branch: data/missing-quad-phase1. Date: 2026-09-14. Budget: 1 session.
Scope: PROVE field existence via DOM/HTML capture ONLY. 0 parser code,
0 DB writes. All scripts in temp (not committed); dumps in temp.

## Environment notes (blocking details for future phases)
- Playwright 1.61.0 lib + bundled browsers 1200/1234, but lib demands
  headless-shell-1228 (missing) → MUST launch with channel="chrome"
  (real Chrome present, same pattern as repo cafef crawler).
- `wait_until="networkidle"` NEVER fires on VN market pages (streaming XHR).
  Use domcontentloaded + fixed sleep (6-8s).
- Console cp1252 breaks on VN text → reconfigure stdout to utf-8 in scripts.

## Track 1 — order_matching_value (khớp lệnh vs thỏa thuận)

| Candidate | Method | Result |
|---|---|---|
| SSI iBoard API (2 guessed paths) | GET | 404 JSON both |
| CafeF Ajax (2 guessed paths) | GET | 404 empty |
| CafeF `/du-lieu/thong-ke-thi-truong.chn` | GET | **404 — URL retired** |
| HSX homepage | GET | 200 but 1.9KB JS shell, no data |
| Vietstock `/thi-truong.htm` | Playwright render | **REDIRECTS to stock page (THI)** — 0/9 keywords |
| Vietstock 3 market-path guesses | GET | 200→redirect stock page / Error/Index |
| FireAnt restv2 guessed path | GET | 404 |

Verdict Track 1: **FAIL this session — no confirmed table.**
Vietstock market section is redirect/gated (bot-wall or restructured IA).
Next (Phase 1b, separate budget): HSX data modules via Playwright interaction,
or broker quote APIs (auth), or SSI iBoard JS-bundle reverse-engineering.
Do NOT guess more URLs.

## Track 4 — VGB10Y auction / curve

| Candidate | Method | Result |
|---|---|---|
| HNX `/thong-tin-trai-phieu.html` | requests | **TLS handshake fail** (server-side config) |
| HNX homepage | Playwright+Chrome | 200, renders (Chrome TLS OK). Shell 2264 chars; "trái phiếu"×4, NO yield/auction table on landing |
| MOF portal | GET | 200 but 2KB JS shell |
| KBNN auction announcements | — | NOT LOCATED this session |

Verdict Track 4: **PARTIAL — site reachable, table not located.**
HNX works under Chrome (not requests). Auction data lives deeper
(requires module navigation). Next: Playwright interaction on HNX bond
auction module + MOF announcement search, separate budget.

## Evidence artifacts (temp, uncommitted)
- vs_render.txt, vs_market.html, hnx_render.txt, h1_survey.json
  in C:/Users/Admin/AppData/Local/Temp/opencode/

## Standing rules re-confirmed
- No historical backfill (live/forward only). No parser until table proven.
- Quarantine + raw archive + server-time stamp when Phase 1b starts.
