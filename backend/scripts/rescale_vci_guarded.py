"""Rescale CÓ KIỂM SOÁT các dòng vci về đúng đơn vị đồng (×1000).

BỐI CẢNH ĐO ĐƯỢC (2026-10-09)
-----------------------------
Store quy ước giá tính bằng ĐỒNG. Nguồn `kbs` trả đúng đồng; nguồn `vci` trả NGHÌN
đồng. Trong một chuỗi của cùng một mã, hai nguồn xen kẽ theo từng phiên nên tạo ra
bước nhảy ~1000× — và vì momentum là tỷ lệ giá, một bước nhảy như vậy tạo ra lợi suất
giả khổng lồ. Bằng chứng: HHG có `kbs 700` (09-17) kề `vci 0.70` (09-18) — tỷ lệ đúng
1000; A32 `30.000` → `27.50` (= 27.500, −8%); ADG `8.510` → `8.00` (= 8.000, −6%).

Một chuỗi mà TOÀN BỘ là một nguồn thì hệ số không đổi KHÔNG ảnh hưởng lợi suất — nên
script này chỉ chạm vào các KHỐI vci nằm giữa hai dòng kbs, tức đúng những chỗ bị vỡ.

BA GUARD (chủ hệ thống chốt 2026-10-09)
---------------------------------------
  G1 — RATIO RANGE: chỉ rescale khi 500 ≤ kbs/vci ≤ 1500. Ngoài dải ⇒ không đoán.
  G2 — CONTINUITY : sau rescale, |Δclose|/close_trước ≤ 20% ở CẢ HAI biên. Đây là
       guard chặn dương tính giả của G1 (nếu 27.50 thực sự là 27.50 chứ không phải
       27.500 thì ×1000 sẽ tạo bước nhảy vô lý ⇒ chặn lại).
  G3 — CROSS-SOURCE: các dòng kbs lân cận (tối đa 5 mỗi phía) phải xác nhận cùng
       một hệ số — chứng minh sai lệch là HỆ THỐNG, không phải lỗi cục bộ một phiên.

Hệ số dùng ĐÚNG 1000, không dùng tỷ lệ đo được: tỷ lệ đo được đã lẫn biến động giá
thật (A32 ra 1091 vì cổ phiếu giảm 8%), dùng nó là khớp vào nhiễu. Guard chỉ có nhiệm
vụ XÁC NHẬN 1000 là đúng.

Mặc định DRY-RUN. Phải `--apply` mới ghi.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.database.data_validity import VALID_ROW_SQL  # noqa: E402

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

SCALE = 1000.0
RATIO_MIN, RATIO_MAX = 500.0, 1500.0  # G1
MAX_CONTINUITY = 0.20  # G2
NEIGHBOUR_LOOKBACK = 5  # G3
CONSISTENT_BAND = (0.9, 1.1)  # dải coi là "đã đúng đơn vị, không cần làm gì"
PRICE_COLS = ("open", "high", "low", "close", "adj_close")

RESCALE, SKIP_OK, QUARANTINE, NEEDS_REVIEW = "RESCALE", "SKIP_DA_DUNG", "QUARANTINE", "CAN_XEM"


@dataclass
class Run:
    """Một khối vci liên tiếp trong chuỗi của một mã."""

    symbol: str
    start_date: str
    end_date: str
    n_rows: int
    rows: list[tuple[str, float]] = field(default_factory=list)  # (date, close) — THỨ TỰ NÀY LÀ HỢP ĐỒNG
    verdict: str = NEEDS_REVIEW
    reasons: list[str] = field(default_factory=list)
    factor: float = SCALE


def _is_kbs(src: str | None) -> bool:
    return bool(src) and src.lower().startswith("kbs")


def _is_vci(src: str | None) -> bool:
    return bool(src) and src.lower().startswith("vci")


def _ratio_band(numer: float, denom: float) -> float:
    return numer / denom if denom else 0.0


def classify_run(run: Run, before: list[float], after: list[float]) -> Run:
    """Áp 3 guard. Trả về Run đã có verdict + lý do (không sửa dữ liệu)."""
    first_close = run.rows[0][1]
    last_close = run.rows[-1][1]

    if not before or not after:
        # Không có mốc tham chiếu ở một phía ⇒ không thể xác nhận hệ số.
        run.verdict = NEEDS_REVIEW
        run.reasons.append(
            f"thiếu mốc kbs {'trước' if not before else 'sau'} — không xác minh được, KHÔNG đoán"
        )
        return run

    r_before = _ratio_band(before[-1], first_close)
    r_after = _ratio_band(after[0], last_close)

    # Nếu cả hai phía đã khớp (~1×) thì khối này vốn đã đúng đơn vị ⇒ không chạm.
    if CONSISTENT_BAND[0] <= r_before <= CONSISTENT_BAND[1] and (
        CONSISTENT_BAND[0] <= r_after <= CONSISTENT_BAND[1]
    ):
        run.verdict = SKIP_OK
        run.reasons.append(f"đã đúng đơn vị (tỷ lệ {r_before:.2f} / {r_after:.2f}) — không cần làm gì")
        return run

    # G1 — dải tỷ lệ
    for label, r in (("trước", r_before), ("sau", r_after)):
        if not (RATIO_MIN <= r <= RATIO_MAX):
            run.verdict = QUARANTINE
            run.reasons.append(f"G1: tỷ lệ phía {label} = {r:.0f} ngoài [{RATIO_MIN:.0f},{RATIO_MAX:.0f}]")
            return run

    # G3 — tính hệ thống: các kbs lân cận phải cùng xác nhận dải
    ref_before = run.rows[0][1] * SCALE
    ref_after = run.rows[-1][1] * SCALE
    n_ok_before = sum(1 for v in before if RATIO_MIN <= _ratio_band(v, first_close) <= RATIO_MAX)
    n_ok_after = sum(1 for v in after if RATIO_MIN <= _ratio_band(v, last_close) <= RATIO_MAX)
    if n_ok_before == 0 or n_ok_after == 0:
        run.verdict = QUARANTINE
        run.reasons.append(
            f"G3: kbs lân cận không xác nhận hệ số (trước {n_ok_before}/{len(before)}, "
            f"sau {n_ok_after}/{len(after)}) ⇒ không phải sai lệch hệ thống"
        )
        return run

    # G2 — liên tục sau rescale ở cả hai biên
    jump_before = abs(ref_before - before[-1]) / before[-1] if before[-1] else 1.0
    jump_after = abs(after[0] - ref_after) / ref_after if ref_after else 1.0
    if jump_before > MAX_CONTINUITY or jump_after > MAX_CONTINUITY:
        run.verdict = QUARANTINE
        run.reasons.append(
            f"G2: sau ×1000 bước nhảy {jump_before*100:.1f}% (biên trước) / "
            f"{jump_after*100:.1f}% (biên sau) > {MAX_CONTINUITY*100:.0f}% ⇒ 1000× không đúng"
        )
        return run

    run.verdict = RESCALE
    run.reasons.append(
        f"G1 {r_before:.0f}/{r_after:.0f} · G2 rìa {jump_before*100:.1f}%/{jump_after*100:.1f}% · "
        f"G3 {n_ok_before}/{len(before)} + {n_ok_after}/{len(after)}"
    )
    return run


def scan(con: sqlite3.Connection) -> list[Run]:
    """Tìm mọi khối vci nằm giữa hai dòng kbs hợp lệ."""
    syms = [
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT symbol FROM daily_ohlcv WHERE is_stale=0 AND {VALID_ROW_SQL} "
            "AND (source LIKE 'vci%' OR source LIKE 'VCI%') ORDER BY symbol"
        )
    ]
    runs: list[Run] = []
    for sym in syms:
        rows = con.execute(
            f"SELECT date, close, source, open, high, low, adj_close, volume FROM daily_ohlcv "
            f"WHERE symbol=? AND is_stale=0 AND {VALID_ROW_SQL} ORDER BY date",
            (sym,),
        ).fetchall()
        idx = 0
        while idx < len(rows):
            if not _is_vci(rows[idx][2]):
                idx += 1
                continue
            j = idx
            while j < len(rows) and _is_vci(rows[j][2]):
                j += 1
            block = rows[idx:j]
            before = [rows[k][1] for k in range(max(0, idx - NEIGHBOUR_LOOKBACK), idx) if _is_kbs(rows[k][2])]
            after = [rows[k][1] for k in range(j, min(len(rows), j + NEIGHBOUR_LOOKBACK)) if _is_kbs(rows[k][2])]
            run = Run(
                symbol=sym,
                start_date=block[0][0],
                end_date=block[-1][0],
                n_rows=len(block),
                rows=[(r[0], r[1]) for r in block],
            )
            runs.append(classify_run(run, before, after))
            idx = j
    return runs


def _apply(con: sqlite3.Connection, runs: list[Run]) -> int:
    """Rescale đúng các dòng thuộc khối RESCALE; gắn nhãn truy vết."""
    sets = ", ".join(f"{c} = {c} * {SCALE}" for c in PRICE_COLS)
    total = 0
    for run in runs:
        if run.verdict != RESCALE:
            continue
        cur = con.execute(
            f"UPDATE daily_ohlcv SET {sets}, source = source || '|rescaled_vci_x1000' "
            "WHERE symbol=? AND date BETWEEN ? AND ? AND is_stale=0 AND (source LIKE 'vci%' OR source LIKE 'VCI%')",
            (run.symbol, run.start_date, run.end_date),
        )
        total += cur.rowcount
    return total


def run(db: Path, apply: bool, sample: int) -> int:
    con = sqlite3.connect(str(db))
    runs = scan(con)
    tally = Counter(r.verdict for r in runs)
    print(f"DB      : {db}")
    print(f"CHẾ ĐỘ  : {'APPLY (ghi thật)' if apply else 'DRY-RUN (không ghi)'}")
    print(f"KHỐI vci: {len(runs)} khối / {len({r.symbol for r in runs})} mã\n")
    for v in (RESCALE, QUARANTINE, NEEDS_REVIEW, SKIP_OK):
        n_rows = sum(r.n_rows for r in runs if r.verdict == v)
        print(f"  {v:<12} {tally.get(v,0):>5} khối · {n_rows:>7,} dòng")

    print(f"\n── MẪU {sample} KHỐI (để review trước khi apply) ──")
    shown = [r for r in runs if r.verdict == RESCALE][: sample - 2]
    shown += [r for r in runs if r.verdict == QUARANTINE][:1]
    shown += [r for r in runs if r.verdict == NEEDS_REVIEW][:1]
    for r in shown:
        first, last = r.rows[0][1], r.rows[-1][1]
        print(
            f"\n  {r.symbol:<6} {r.start_date} → {r.end_date}  ({r.n_rows} phiên)  [{r.verdict}]"
        )
        print(f"     close vci : {first:,.2f} … {last:,.2f}")
        if r.verdict == RESCALE:
            print(f"     sau ×1000  : {first*SCALE:,.0f} … {last*SCALE:,.0f}")
        for reason in r.reasons:
            print(f"     · {reason}")

    if apply:
        n = _apply(con, runs)
        con.commit()
        print(f"\n✅ Đã rescale {n:,} dòng (nhãn 'rescaled_vci_x1000').")
        left = con.execute(
            f"SELECT COUNT(*) FROM daily_ohlcv WHERE is_stale=0 AND {VALID_ROW_SQL} "
            "AND (source LIKE 'vci%' OR source LIKE 'VCI%') AND source NOT LIKE '%rescaled%'"
        ).fetchone()[0]
        print(f"   dòng vci còn lại chưa xử lý: {left:,}")
    else:
        print("\n(dry-run — chưa ghi gì. Thêm --apply để thực thi.)")
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Rescale có kiểm soát dòng vci ×1000")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sample", type=int, default=10)
    a = ap.parse_args()
    return run(a.db, a.apply, a.sample)


if __name__ == "__main__":
    sys.exit(main())
