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
MAX_CONTINUITY = 0.20  # G2 cho khối ĐỦ 2 MỐC
# G2 cho khối CHỈ 1 MỐC: siết chặt hơn, vì thiếu mốc kiểm chứng phía kia.
MAX_CONTINUITY_SINGLE = 0.15
# Vùng "sát biên" của khối 1 mốc ⇒ KHÔNG đoán, chuyển cách ly (luật chủ hệ thống chốt,
# ca tham chiếu là BHC: tỷ lệ 1250, độ lệch chạm trần 20%).
EDGE_RATIO = 1200.0
EDGE_JUMP = 0.12
NEIGHBOUR_LOOKBACK = 5  # G3
# G4 — KỀ THỜI GIAN: mốc tham chiếu phải nằm sát khối (≤ N ngày lịch).
#
# VÌ SAO CẦN: tỷ lệ giá chỉ nhận diện được sai thang khi hai điểm Ở GẦN NHAU — lúc đó
# biến động giá thật nhỏ so với 1000×. Nếu mốc cách khối nửa năm (chuyện xảy ra thật khi
# các phiên ở giữa bị cách ly vì close = 0), giá thật có thể đã đi 40%+ và tỷ lệ đo được
# KHÔNG còn phân biệt được "sai thang" với "thị trường đã đổi giá".
#   HCI: khối kết thúc 2026-03-20 (7.500), mốc kbs kế tiếp 2026-09-07 (4.400) ⇒ tỷ lệ 0,59.
#   Đó là giá RƠI 41% trong 5,5 tháng — hoàn toàn bình thường, KHÔNG phải sai thang.
# Không có mốc kề ⇒ KHÔNG ĐOÁN, và cũng KHÔNG cách ly: để yên.
MAX_ANCHOR_GAP_DAYS = 15
CONSISTENT_BAND = (0.9, 1.1)  # dải "khớp chặt" (báo cáo)
# Dải "CÙNG ĐƠN VỊ": trong khoảng này thì cobị coi là cùng đơn vị và giá chỉ biến động
# thật ⇒ KHÔNG chạm. Phải rộng: mốc có thể cách khối tới 15 ngày và giá thật đi ±30-50%.
#
# BÀI HỌC (2026-10-09): bản đầu đặt dải này (0.9, 1.1). Hệ quả: L45 tỷ lệ 0,87 (giá rơi
# 13%), LG9 1,12, LLM 1,15 — TOÀN BỘ đã đúng đơn vị mà bị đẩy xuống G1 rồi cách ly, vì
# 0,87 không nằm trong [500,1500]. Kết quả sai: 45.042 dòng dữ liệu ĐÚNG bị cách ly.
# Tỷ lệ "không ≈1 và không ≈1000" KHÔNG phải vi phạm — nó là "chưa kết luận được",
# và câu trả lời đúng là ĐỂ YÊN, không phải cách ly.
SAME_UNIT_BAND = (0.5, 2.0)
PRICE_COLS = ("open", "high", "low", "close", "adj_close")

TAG_RESCALED = "rescaled_vci_x1000"
TAG_GUARD_FAIL = "quarantine:scale_guard_fail"
TAG_EDGE = "quarantine:single_anchor_edge_case"

RESCALE, SKIP_OK, QUARANTINE, NEEDS_REVIEW, UNIFORM = (
    "RESCALE",
    "SKIP_DA_DUNG",
    "QUARANTINE",
    "CAN_XEM",
    "MOT_HE_SO_VO_HAI",
)


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
    tag: str | None = None


def _is_kbs(src: str | None) -> bool:
    return bool(src) and src.lower().startswith("kbs")


def _is_vci(src: str | None) -> bool:
    return bool(src) and src.lower().startswith("vci")


def _ratio_band(numer: float, denom: float) -> float:
    return numer / denom if denom else 0.0


def _gap_days(d1: str, d2: str) -> int:
    from datetime import date as _d

    a, b = _d.fromisoformat(d1), _d.fromisoformat(d2)
    return abs((a - b).days)


def classify_run(
    run: Run, before: list[tuple[str, float]], after: list[tuple[str, float]], *, whole_series: bool
) -> Run:
    """Áp 4 guard. Trả về Run đã có verdict + lý do (không sửa dữ liệu).

    `whole_series=True` nghĩa là khối này CHIẾM TRỌN chuỗi sạch của mã (mã chỉ có nguồn
    vci). Khi đó hệ số là một hằng số trên toàn chuỗi nên **triệt tiêu trong mọi lợi
    suất** — không vỡ, không cần sửa. Tách thành verdict riêng để việc "để yên" là một
    quyết định CÓ TÊN, không phải hệ quả của việc guard không chạy.
    """
    first_close = run.rows[0][1]
    last_close = run.rows[-1][1]

    # G4 — chỉ giữ mốc KỀ THỜI GIAN. Mốc xa không phân biệt được sai thang với biến động giá.
    near_before = [(d, c) for d, c in before if _gap_days(d, run.start_date) <= MAX_ANCHOR_GAP_DAYS]
    near_after = [(d, c) for d, c in after if _gap_days(d, run.end_date) <= MAX_ANCHOR_GAP_DAYS]

    if not near_before and not near_after:
        if whole_series:
            run.verdict = UNIFORM
            run.reasons.append(
                "toàn chuỗi một nguồn ⇒ hệ số không đổi, triệt tiêu trong lợi suất — KHÔNG chạm"
            )
        else:
            run.verdict = NEEDS_REVIEW
            gaps = [f"{_gap_days(d, run.start_date)}n" for d, _ in before] + [
                f"{_gap_days(d, run.end_date)}n" for d, _ in after
            ]
            run.reasons.append(
                "không có mốc kbs nào trong "
                f"{MAX_ANCHOR_GAP_DAYS} ngày (gần nhất: {', '.join(gaps) or 'không có'}) "
                "⇒ không phân biệt được sai thang với biến động giá — KHÔNG đoán, KHÔNG cách ly"
            )
        return run

    if not (near_before and near_after):
        single = True
    else:
        single = False

    r_before = _ratio_band(near_before[-1][1], first_close) if near_before else None
    r_after = _ratio_band(near_after[0][1], last_close) if near_after else None

    ratios = [r for r in (r_before, r_after) if r is not None]

    def _kind(r: float) -> str:
        if SAME_UNIT_BAND[0] <= r <= SAME_UNIT_BAND[1]:
            return "same"  # cùng đơn vị, chỉ là biến động giá thật
        if RATIO_MIN <= r <= RATIO_MAX:
            return "scale"  # sai thang ~1000×
        return "unknown"  # không kết luận được — KHÔNG đoán

    kinds = [_kind(r) for r in ratios]

    if all(k == "same" for k in kinds):
        run.verdict = SKIP_OK
        run.reasons.append(f"đã đúng đơn vị ({', '.join(f'{r:.2f}' for r in ratios)}) — không cần làm gì")
        return run

    if "unknown" in kinds or "scale" not in kinds:
        # Không ≈1 cũng không ≈1000 (hoặc hai mốc mâu thuẫn nhau) ⇒ CHƯA KẾT LUẬN ĐƯỢC.
        # Để yên. Đây KHÔNG phải vi phạm: tỷ lệ 0,87 là giá rơi 13%, hoàn toàn bình thường.
        run.verdict = NEEDS_REVIEW
        run.reasons.append(
            f"tỷ lệ {', '.join(f'{r:.2f}' for r in ratios)} không ≈1 cũng không ≈1000 "
            "⇒ chưa kết luận được — ĐỂ YÊN (không đoán, không cách ly)"
        )
        return run

    # G1 — dải tỷ lệ (mọi mốc sẵn có)
    for label, r in (("trước", r_before), ("sau", r_after)):
        if r is None:
            continue
        if not (RATIO_MIN <= r <= RATIO_MAX):
            run.verdict, run.tag = QUARANTINE, TAG_GUARD_FAIL
            run.reasons.append(f"G1: tỷ lệ phía {label} = {r:.0f} ngoài [{RATIO_MIN:.0f},{RATIO_MAX:.0f}]")
            return run

    # G3 — tính hệ thống: các kbs lân cận sẵn có phải cùng xác nhận dải
    ref_before = first_close * SCALE if near_before else None
    ref_after = last_close * SCALE if near_after else None
    n_ok = {}
    for label, neigh, close_v in (("trước", near_before, first_close), ("sau", near_after, last_close)):
        if not neigh:
            continue
        n_ok[label] = sum(1 for _, v in neigh if RATIO_MIN <= _ratio_band(v, close_v) <= RATIO_MAX)
        if n_ok[label] == 0:
            run.verdict, run.tag = QUARANTINE, TAG_GUARD_FAIL
            run.reasons.append(f"G3: kbs phía {label} không xác nhận hệ số (0/{len(neigh)})")
            return run

    # G2 — liên tục sau rescale (trần siết hơn khi chỉ có 1 mốc)
    ceiling = MAX_CONTINUITY_SINGLE if single else MAX_CONTINUITY
    jumps = []
    if ref_before is not None and near_before[-1][1]:
        jumps.append(abs(ref_before - near_before[-1][1]) / near_before[-1][1])
    if ref_after is not None and ref_after:
        jumps.append(abs(near_after[0][1] - ref_after) / ref_after)
    worst = max(jumps) if jumps else 1.0
    if worst > ceiling:
        run.verdict, run.tag = QUARANTINE, TAG_GUARD_FAIL
        run.reasons.append(
            f"G2: sau ×1000 độ lệch {worst*100:.1f}% > trần {ceiling*100:.0f}%"
            f"{' (khối 1 mốc — siết biên)' if single else ''} ⇒ 1000× không đúng"
        )
        return run

    # Luật loại trừ ca SÁT BIÊN của khối 1 mốc — không đoán khi bằng chứng mỏng
    if single and (worst > EDGE_JUMP or max(ratios) >= EDGE_RATIO):
        run.verdict, run.tag = QUARANTINE, TAG_EDGE
        run.reasons.append(
            f"SÁT BIÊN (1 mốc): tỷ lệ {max(ratios):.0f}, độ lệch {worst*100:.1f}% "
            f"→ vùng nhạy cảm (>{EDGE_JUMP*100:.0f}% hoặc ≥{EDGE_RATIO:.0f}) ⇒ cách ly, KHÔNG đoán"
        )
        return run

    run.verdict = RESCALE
    run.tag = TAG_RESCALED
    run.reasons.append(
        f"{'1 mốc' if single else '2 mốc'} · G1 {r_before if r_before else r_after:.0f} · "
        f"G2 rìa {worst*100:.1f}% (trần {ceiling*100:.0f}%) · "
        f"G3 {' + '.join(f'{k} {v}/{len(before if k=='trước' else after)}' for k, v in n_ok.items())}"
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
            before = [(rows[k][0], rows[k][1]) for k in range(max(0, idx - NEIGHBOUR_LOOKBACK), idx) if _is_kbs(rows[k][2])]
            after = [(rows[k][0], rows[k][1]) for k in range(j, min(len(rows), j + NEIGHBOUR_LOOKBACK)) if _is_kbs(rows[k][2])]
            run = Run(
                symbol=sym,
                start_date=block[0][0],
                end_date=block[-1][0],
                n_rows=len(block),
                rows=[(r[0], r[1]) for r in block],
            )
            runs.append(classify_run(run, before, after, whole_series=(idx == 0 and j == len(rows))))
            idx = j
    return runs


def _apply(con: sqlite3.Connection, runs: list[Run]) -> tuple[int, int]:
    """Thi hành RESCALE và QUARANTINE. Kiểm rowcount từng khối trước khi commit."""
    sets = ", ".join(f"{c} = {c} * {SCALE}" for c in PRICE_COLS)
    where = (
        "symbol=? AND date BETWEEN ? AND ? AND is_stale=0 "
        "AND (source LIKE 'vci%' OR source LIKE 'VCI%')"
    )
    n_rescale = n_quar = 0
    for run in runs:
        if run.verdict == RESCALE:
            sql = f"UPDATE daily_ohlcv SET {sets}, source = source || '|{run.tag}' WHERE {where}"
        elif run.verdict == QUARANTINE:
            sql = f"UPDATE daily_ohlcv SET is_stale = 1, source = source || '|{run.tag}' WHERE {where}"
        else:
            continue
        cur = con.execute(sql, (run.symbol, run.start_date, run.end_date))
        if cur.rowcount != run.n_rows:
            con.rollback()
            raise RuntimeError(
                f"DỪNG — {run.symbol} {run.start_date}→{run.end_date}: rowcount "
                f"{cur.rowcount} ≠ số dòng khối {run.n_rows}. Đã rollback."
            )
        if run.verdict == RESCALE:
            n_rescale += cur.rowcount
        else:
            n_quar += cur.rowcount
    return n_rescale, n_quar


def run(db: Path, apply: bool, sample: int) -> int:
    con = sqlite3.connect(str(db))
    runs = scan(con)
    tally = Counter(r.verdict for r in runs)
    print(f"DB      : {db}")
    print(f"CHẾ ĐỘ  : {'APPLY (ghi thật)' if apply else 'DRY-RUN (không ghi)'}")
    print(f"KHỐI vci: {len(runs)} khối / {len({r.symbol for r in runs})} mã\n")
    for v in (RESCALE, QUARANTINE, NEEDS_REVIEW, SKIP_OK, UNIFORM):
        n_rows = sum(r.n_rows for r in runs if r.verdict == v)
        print(f"  {v:<16} {tally.get(v,0):>5} khối · {n_rows:>7,} dòng")

    edge = [r for r in runs if r.tag == TAG_EDGE]
    if edge:
        print(f"     ↳ trong QUARANTINE: {len(edge)} khối SÁT BIÊN (1 mốc) → {TAG_EDGE}")

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
        n_rescale, n_quar = _apply(con, runs)
        con.commit()
        print(f"\n✅ Rescale {n_rescale:,} dòng (nhãn '{TAG_RESCALED}')")
        print(f"✅ Cách ly {n_quar:,} dòng (nhãn '{TAG_GUARD_FAIL}' / '{TAG_EDGE}')")
        left = con.execute(
            f"SELECT COUNT(*) FROM daily_ohlcv WHERE is_stale=0 AND {VALID_ROW_SQL} "
            "AND (source LIKE 'vci%' OR source LIKE 'VCI%') "
            "AND source NOT LIKE '%rescaled%'"
        ).fetchone()[0]
        print(f"   dòng vci còn nguyên (đã kiểm tra ở trên): {left:,}")
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
