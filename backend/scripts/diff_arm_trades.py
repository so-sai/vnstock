"""diff_arm_trades.py — phép kiểm QUYẾT ĐỊNH cho bất thường V2 vs V5.

BỐI CẢNH
--------
Ablation v1.1: cổng `≠ DISTRESSED` của V5 bị kích 2.651 lần nhưng chỉ cắt mất 12 ứng viên
(234 → 222), trong khi Sharpe rơi từ +0,445 xuống +0,063 (Δ = −0,381). Một thay đổi biên
5% không thể tự nó tạo ra chênh lệch đó ⇒ phải kiểm xem:

  (a) hai tập LỆNH có thực sự khác nhau chỉ ~12 chỗ (⇒ khuếch đại hỗn độn, kết quả mong manh), hay
  (b) tập lệnh gần như trùng nhau mà đường NAV vẫn lệch (⇒ LỖI trong harness, ô V5 VÔ HIỆU).

CÁCH KIỂM
---------
Chạy lại đúng hai ô V2 và V5, ghi lại từng lượt vào lệnh (mã, ngày), rồi:
  - in tập chênh lệch hai chiều;
  - với các mã chỉ V2 mới có, hỏi thẳng organ: archetype lúc đó là gì (có phải DISTRESSED?);
  - so lãi/lỗ trung bình của nhóm chênh lệch so với phần còn lại.

KHÔNG ghi DB, KHÔNG sửa store — chỉ đọc và mô phỏng lại.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest import backtest_trend_2021_2025 as bt  # noqa: E402
from src.backtest import trend_loop as tl  # noqa: E402
from src.backtest.ablation_arms import ARMS  # noqa: E402
from src.engine.trend_organ_filter import OrganFilter  # noqa: E402


def run(arm_name: str):
    pf = bt.Portfolio(cost=bt.COST_SCENARIOS["primary"])
    tl.simulate(
        pf,
        start=bt.WINDOW_START,
        end=bt.WINDOW_END,
        min_sessions=bt.MIN_SESSIONS_PER_YEAR,
        arm=ARMS[arm_name],
    )
    entries = [(t.symbol, t.date_in, t.pnl_pct) for t in pf.trades]
    navs = [v for _, v in pf.nav_history]
    return entries, bt.sharpe(navs), navs


def main() -> int:
    v2_e, v2_s, v2_nav = run("V2_momentum_regime")
    v5_e, v5_s, v5_nav = run("V5_distressed_only")

    k2 = {(s, d) for s, d, _ in v2_e}
    k5 = {(s, d) for s, d, _ in v5_e}
    only2 = sorted(k2 - k5)
    only5 = sorted(k5 - k2)
    print(f"V2: {len(v2_e)} dòng trade / Sharpe {v2_s:.4f} | V5: {len(v5_e)} dòng / {v5_s:.4f}")
    print(f"chênh lệch tập (mã, ngày-into): chỉ V2 có {len(only2)} · chỉ V5 có {len(only5)}")
    print(f"  giao nhau: {len(k2 & k5)} / {len(k2 | k5)}")

    if not only2 and not only5:
        print("\n*** TẬP LỆNH TRÙNG NHAU HOÀN TOÀN MÀ SHARPE KHÁC ***")
        print(f"  NAV cuối: V2={v2_nav[-1]:.4f} V5={v5_nav[-1]:.4f} -> LỖI HARNESS: ô V5 VÔ HIỆU")
        return 2

    organ = OrganFilter()
    print("\nchỉ V2 mới vào (mã, ngày) — archetype lúc đó:")
    for sym, d in only2[:20]:
        st = organ.evaluate(sym, d)
        print(f"  {sym:<6} {d}  archetype={st.archetype!s:<16} passed_full={st.passed}")
    print("  ..." if len(only2) > 20 else "")

    pnl2 = {k: p for k, _, p in v2_e}
    diff_pnl = [pnl2[k] for k in only2 if k in pnl2]
    rest_pnl = [p for k, _, p in v2_e if k not in set(only2)]
    if diff_pnl:
        print(f"\nlãi/lỗ TB nhóm CHỈ V2 có  : {sum(diff_pnl) / len(diff_pnl):+.2f}%  (n={len(diff_pnl)})")
    if rest_pnl:
        print(f"lãi/lỗ TB phần còn lại     : {sum(rest_pnl) / len(rest_pnl):+.2f}%  (n={len(rest_pnl)})")
    print(f"NAV cuối: V2={v2_nav[-1]:.4f} V5={v5_nav[-1]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
