"""test_determinism.py — Test Contract 1: Determinism Guard (PYTHONHASHSEED).

Quy tắc: mọi hàm tạo danh sách/vũ trụ cổ phiếu phải bất biến thứ tự giữa các
tiến trình Python khác nhau. Verify bằng subprocess với PYTHONHASHSEED khác nhau
(100 vs 200) — nếu kết quả lệch → nondeterminism (như lỗi `UNIVERSE = list(set(...))`).

- Test nhanh: digest của UNIVERSE giống hệt giữa 2 hash seed.
- Test chậm (marker `slow`): chữ ký trade-log OOS backtest giống hệt giữa 2 seed.

Run:  python -m pytest backend/tests/test_determinism.py -q
      python -m pytest backend/tests/test_determinism.py -q -m slow
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"
_SRC = _BACKEND / "src"

# Bộ tham số khóa từ Walk-Forward chính thức (2026-08-08, run deterministic).
_LOCKED_PARAMS = {
    "w_fund": 0.5, "w_macro": 0.1, "w_alpha": 0.15, "w_behav": 0.25,
    "entry_thresh": 0.55, "exit_thresh": 0.3,
    "trailing_stop": 0.10, "trailing_take": 0.20, "min_hold_days": 15,
}

_CHECK_UNIVERSE = (
    "import hashlib, json, sys;"
    f"sys.path.insert(0, {str(_SRC)!r});"
    "from backtest.unified_system_replay import UNIVERSE;"
    "print(hashlib.sha256(json.dumps(UNIVERSE).encode()).hexdigest())"
)

_CHECK_BACKTEST = (
    "import hashlib, json, sqlite3, sys;"
    f"sys.path.insert(0, {str(_SRC)!r});"
    f"_db={str(_BACKEND / 'data' / 'screener_cache.db')!r};"
    f"_fin={str(_BACKEND / 'data' / 'financial_facts.db')!r};"
    f"_lri={str(_BACKEND / 'data' / 'reports' / 'ablation_studies' / 'grid_lri_cache.json')!r};"
    f"_params={json.dumps(_LOCKED_PARAMS)};"
    "from backtest.grid_search import precompute_scores;"
    "from backtest.grid_search_v2 import run_backtest_with_guard;"
    "from backtest.unified_system_replay import _get_trading_days;"
    "conn=sqlite3.connect(_db); conn.execute(\"ATTACH DATABASE '\"+_fin+\"' AS fin\");"
    "dates=_get_trading_days(conn,'2026-01-01','2026-08-07'); sd=dates[::5];"
    "scores=precompute_scores(conn,dates,sd); conn.close();"
    "import os;"
    "_ld=lambda _p: json.load(open(_p,encoding='utf-8')) if os.path.exists(_p) else {};"
    "_c=_ld(_lri); lri={d:_c.get(d,1.0) for d in dates};"
    "r=run_backtest_with_guard(scores,dates,sd,_params,db_path=_db,lri_cache=lri,capture_trade_log=True);"
    "trades=r.pop('trade_log',[]);"
    "sig=hashlib.sha256(json.dumps(trades,sort_keys=True,default=str).encode()).hexdigest();"
    "print(r['total_trades'], round(r['final_nav'],1), round(r['sharpe'],4), sig)"
)


def _run_code_with_seed(code: str, seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed)}
    out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True, cwd=str(_ROOT))
    return out.strip().splitlines()[-1]


class TestUniverseDeterminismAcrossSeeds:
    """Contract 1 — universe phải trùng 100% giữa các PYTHONHASHSEED."""

    def test_universe_digest_identical_across_hash_seeds(self):
        d100 = _run_code_with_seed(_CHECK_UNIVERSE, 100)
        d200 = _run_code_with_seed(_CHECK_UNIVERSE, 200)
        assert d100 == d200, f"UNIVERSE lệch giữa seed 100 ({d100}) và 200 ({d200})"

    def test_universe_is_sorted_and_unique(self):
        from backtest.unified_system_replay import UNIVERSE

        assert UNIVERSE == sorted(UNIVERSE)
        assert len(UNIVERSE) == len(set(UNIVERSE))


@pytest.mark.slow
class TestBacktestDeterminismAcrossSeeds:
    """Contract 1 (đầy đủ) — chữ ký trade-log OOS giống hệt giữa 2 hash seed.

    Chạy precompute_scores + run_backtest_with_guard trong 2 subprocess riêng
    với PYTHONHASHSEED khác nhau; chữ ký sha256 của trade-log phải trùng khớp.
    """

    def test_oos_trade_log_signature_identical_across_seeds(self):
        r100 = _run_code_with_seed(_CHECK_BACKTEST, 100)
        r200 = _run_code_with_seed(_CHECK_BACKTEST, 200)
        assert r100 == r200, f"OOS trade-log lệch giữa seed 100 ({r100}) và 200 ({r200})"
