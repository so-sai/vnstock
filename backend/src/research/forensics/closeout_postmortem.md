# Post-mortem — R&D Equity Alpha Lab (2026-09-10 → 2026-09-14)

## Goal (locked, NOT met)
Net > VNINDEX+8%/yr, MDD<15%, N<=25 trades/yr. No path alive. Stated plainly.

## Trial accounting (cumulative N decks, no cherry-pick)
- v1 linkage equity 001-036: DSR 0.68, theta inert (36→12 effective).
- v2 breakout top-30 037-072: Type A fail, Sharpe<0, lb inert (15≡20).
- H3 gold rotation 073-090: all Sharpe<0, h inert (20≡60), timing-vs-trend mismatch.
- H4 mean-reversion 091-114: WR 14-27%, Sharpe<0, heat-cap bug fixed mid-flight.
- Path C macro-switching feasibility: 0 trials, MDD -40.52% = buy-hold. Dead.
- Total: 114 trials + 1 feasibility. OOS 2025-2026 NEVER opened. No merge to main.

## Corrections accepted at closeout
1. "Cash 100% optimal" was post-hoc rationalization. Truth: cash is the
   safe DEFAULT with no edge; it underperforms VNINDEX long-term (~5-6% vs
   ~6-8% CAGR). Default ≠ optimum.
2. Track 3 daily accumulation without a consumer hypothesis = technical debt.
   No cron scheduled; staging holds exactly 1 PASS record (379/TB-NHNN).
3. Desktop App contributes 0 to goal; at most a personal monitor. Not built.

## Assets preserved (not achievements)
- 114-trial ledger + DSR/heat/ledger tooling (audit trail, reusable method).
- market_wide_breadth engine (1669 sessions, research-side, not wired).
- staging_sbv_fx_status: 1 live PASS record + parser + in-repo audit.
- probe_helper + Phase 1a/1b forensic reports (what failed and where).

## Policy going forward
- Class `technical_price_only_daily_ohlcv`: INVALIDATED, do not reopen
  without non-price input (flow, fundamental PIT, macro liquidity).
- Any new hypothesis: pre-declare, cumulative N continues from 115.
- Fundamental PIT pipeline (if ever): 6-12 months data work first,
  10-20% odds, stated upfront — no trial may run on dirty data.
