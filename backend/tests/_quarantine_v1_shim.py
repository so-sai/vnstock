"""Bản MÔ PHỎNG thuật toán CŨ (dry-run lần 1: mốc = "đoạn trội theo số dòng") — chỉ để chứng minh RED.

Không phải code sản phẩm. Mục đích: chạy `test_quarantine_unit_mix.py` với `QUARANTINE_SCRIPT` trỏ vào
file này để chứng minh test "chống vá quá tay" bắt được lỗi của thuật toán cũ (nó gắn cờ nhánh ĐÚNG).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REAL = Path(__file__).resolve().parents[1] / "scripts" / "quarantine_unit_mix.py"
_spec = importlib.util.spec_from_file_location("_qum_real", _REAL)
assert _spec and _spec.loader
_real_mod = importlib.util.module_from_spec(_spec)
sys.modules["_qum_real"] = _real_mod
_spec.loader.exec_module(_real_mod)

for _k in dir(_real_mod):
    if not _k.startswith("__"):
        globals()[_k] = getattr(_real_mod, _k)


def analyse(conn):  # type: ignore[no-untyped-def]
    """Thuật toán CŨ: mốc thang = đoạn trội theo số dòng; cờ mọi đoạn lệch ≥10× so với mốc."""
    syms = [
        r[0]
        for r in conn.execute(f"SELECT DISTINCT symbol FROM daily_ohlcv WHERE {_real_mod.VALID_ROW_SQL}")
    ]
    quarantine: list[dict] = []
    unresolved: list[dict] = []
    for sym in sorted(syms):
        rows = conn.execute(
            f"SELECT date, close FROM daily_ohlcv WHERE symbol=? AND {_real_mod.VALID_ROW_SQL} ORDER BY date",
            (sym,),
        ).fetchall()
        if len(rows) < 2:
            continue
        segs = _real_mod._segments(rows)
        if len(segs) == 1:
            continue
        dom = max(segs, key=len)
        dom_scale = sorted(c for _d, c in dom)[len(dom) // 2]
        for seg in segs:
            if seg is dom:
                continue
            med = sorted(c for _d, c in seg)[len(seg) // 2]
            if not (med / dom_scale >= _real_mod.JUMP or dom_scale / med >= _real_mod.JUMP):
                continue
            quarantine.append(
                {
                    "symbol": sym,
                    "from": seg[0][0],
                    "to": seg[-1][0],
                    "n_rows": len(seg),
                    "median": med,
                    "cut": 0.0,  # bản cũ ghi theo CỬA SỔ NGÀY
                    "anchor": None,
                    "verdict": "CÁCH LY (mốc đoạn trội)",
                }
            )
    return quarantine, unresolved
