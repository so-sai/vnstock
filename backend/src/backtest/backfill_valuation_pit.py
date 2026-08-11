"""backfill_valuation_pit.py — Reconstruct valuation_scores PIT-clean (expanding-window z-score).

Phase 2: giải quyết valuation_zone = NO_DATA (97-98%) trong replay 2022-2025.

Trước (BROKEN):
  - valuation_engine.compute_valuation tính z-score/zone bằng mean/std TOÀN CHUỖI lịch sử
    (gồm cả các period sau target_date) → LOOK-AHEAD bias. Replay chỉ filter
    period <= quarter(target_date) khi đọc (company_state.get_latest_valuation), nên
    nhận zone đã nhiễm tương lai.
  - valuation_scores chỉ có 39 mã → ~562 mã còn lại của universe replay vĩnh viễn NO_DATA.

Sau (FIX):
  - valuation_engine.compute_valuation dùng EXPANDING WINDOW: mỗi kỳ chỉ tính
    mean/std/percentile trên các kỳ <= kỳ hiện tại → z-score PIT-clean.
  - Backfill valuation_scores cho 58 mã có BCTC trong financial_facts.db.
  - PS/EV_EBITDA không thể tính (financial_facts thiếu SHARES_OUT) — PE/PB/ROE đủ
    để tạo dispersion valuation_zone.

PIT-safe: ratio dùng price tại cuối quý (không nhìn tương lai); z-score expanding-window.
QUYỀN GHI CÓ CHỦ ĐÍCH: chỉ sửa financial_facts.db.valuation_scores (gitignored).
Backup tự động sang valuation_scores_backup trước khi xoá.

Usage:
  python backend/src/backtest/backfill_valuation_pit.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

# ── Path setup ──
_current = Path(__file__).resolve().parent
for _par in [_current] + list(_current.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        ROOT = _par
        break
BACKEND = ROOT / "backend"
SRC = BACKEND / "src"
DATA = BACKEND / "data"
FIN_DB = DATA / "financial_facts.db"

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SRC))

from src.financial.valuation_engine import ValuationEngine


def backup_scores() -> None:
    conn = sqlite3.connect(str(FIN_DB))
    conn.execute("DROP TABLE IF EXISTS valuation_scores_backup")
    conn.execute("CREATE TABLE valuation_scores_backup AS SELECT * FROM valuation_scores")
    n = conn.execute("SELECT COUNT(*) FROM valuation_scores_backup").fetchone()[0]
    conn.commit()
    conn.close()
    print(f"[BACKUP] valuation_scores -> valuation_scores_backup ({n} rows)")


def clear_scores() -> None:
    conn = sqlite3.connect(str(FIN_DB))
    conn.execute("DELETE FROM valuation_scores")
    conn.commit()
    conn.close()
    print("[CLEAR] valuation_scores emptied")


def get_symbols() -> list[str]:
    conn = sqlite3.connect(str(FIN_DB))
    syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM financial_facts ORDER BY symbol")]
    conn.close()
    return syms


def main() -> None:
    backup_scores()
    clear_scores()

    eng = ValuationEngine()
    eng.init_schema()
    syms = get_symbols()
    print(f"[BACKFILL] {len(syms)} symbols")
    done = no_data = failed = 0
    t0 = time.time()
    for sym in syms:
        try:
            r = eng.compute_valuation(sym)
            if r.get("status") == "DONE":
                done += 1
            else:
                no_data += 1
                print(f"  [{r.get('status')}] {sym}")
        except Exception as e:  # noqa: BLE001 - backfill phải chạy hết, log lỗi từng mã
            failed += 1
            print(f"  [ERROR] {sym}: {e!r}")
    elapsed = time.time() - t0
    print(f"[DONE] DONE={done} NO_DATA={no_data} FAILED={failed} elapsed={elapsed:.1f}s")

    conn = sqlite3.connect(str(FIN_DB))
    total = conn.execute("SELECT COUNT(*) FROM valuation_scores").fetchone()[0]
    per_ratio = conn.execute(
        "SELECT ratio_name, COUNT(*) FROM valuation_scores GROUP BY ratio_name ORDER BY ratio_name"
    ).fetchall()
    per_zone = conn.execute("SELECT zone, COUNT(*) FROM valuation_scores GROUP BY zone ORDER BY zone").fetchall()
    per_year = conn.execute(
        "SELECT substr(period,1,4), COUNT(DISTINCT symbol) FROM valuation_scores "
        "GROUP BY substr(period,1,4) ORDER BY substr(period,1,4)"
    ).fetchall()
    conn.close()
    print(f"[STATS] total={total}")
    for r in per_ratio:
        print(f"   ratio {r[0]}: {r[1]}")
    for r in per_zone:
        print(f"   zone {r[0]}: {r[1]}")
    for r in per_year:
        print(f"   year {r[0]}: {r[1]} symbols")


if __name__ == "__main__":
    main()
