r"""Chuẩn hóa ĐƠN VỊ LỊCH SỬ cho các mã có dải giá cũ ở đơn vị "nghìn đồng" (×1000).

[WHY] Đo 2026-10-16 trên `screener_cache.db` (dòng qua vị từ): 7 mã `PPS MGG VTJ MIE HHG PXA VIN`
mang HAI đơn vị trong cùng một chuỗi — dải lịch sử ở "nghìn đồng", dải gần đây (2026-04/08→) ở
đồng. Hệ quả đo được: `momentum`/RS/ADV đọc một cú nhảy ~1000× giữa hai dải ⇒ lợi suất giả khổng lồ;
và đây chính là đối chứng của ổ phantom ở `DDG/BII/NOS` (bên kia bị nhân lên, bên này bị thiếu đi).

[ROOT CAUSE] Hai nguồn dùng hai đơn vị khác nhau cho CÙNG một mã (`kbs` ghi "nghìn đồng" ở dải cũ —
6/7 mã — còn `vci` ở `VTJ`), rồi một đường nạp mới ghi đồng từ 2026-04/08. `scripts/
rescale_vci_guarded.py` đã ghi lại luật "store quy ước ĐỒNG, vci trả NGHÌN đồng"; phát hiện này cho
thấy luật đó phải đọc là **THEO TỪNG MÃ-VÀ-THỜI ĐOẠN**, không phải một quy tắc bất biến theo nguồn.

[INVARIANT]
  1. **Hệ số đúng 1000**, KHÔNG dùng tỷ lệ đo được (tỷ lệ lẫn biến động giá thật) — guard chỉ XÁC NHẬN.
  2. **G1** tái dùng: 500 ≤ (median dải cao ÷ median dải thấp) ≤ 1500.
  3. **G2** (guard chủ hệ thống chốt): sau ×1000, độ lệch tại MỌI điểm giao thoa phải ≤ 20%.
  4. **G3 hậu kiểm**: sau khi mô phỏng rescale, chuỗi KHÔNG được còn bước nhảy nào ≥ 10× — nếu còn
     ⇒ hệ số/ranh giới sai ⇒ HUỶ cả mã (fail-closed), không ghi nửa vời.
  5. Chỉ đụng dòng `is_stale = 0` qua vị từ; dòng đã mang nhãn rescale thì bỏ qua (idempotent).
  6. Nhãn provenance theo NGUỒN THẬT của dòng (`kbs`/`vci`), không gán nhãn nguồn khác.
  7. Backup trước khi ghi; rowcount phải khớp dự đoán, lệch ⇒ rollback.

Chạy:  python backend/scripts/rescale_unit_history.py            # dry-run
       python backend/scripts/rescale_unit_history.py --apply    # ghi (có backup)
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.database.data_validity import VALID_ROW_SQL

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

#: Tái dùng NGUỒN SỰ THẬT của công cụ đã được kiểm định (`rescale_vci_guarded.py`) — không chép lại
#: hằng số, để hai công cụ không trôi khỏi nhau.
_spec = importlib.util.spec_from_file_location(
    "rescale_vci_guarded", PROJECT_ROOT / "backend" / "scripts" / "rescale_vci_guarded.py"
)
assert _spec and _spec.loader
_guarded = importlib.util.module_from_spec(_spec)
sys.modules["rescale_vci_guarded"] = _guarded
_spec.loader.exec_module(_guarded)

SCALE = _guarded.SCALE
PRICE_COLS = _guarded.PRICE_COLS
RATIO_MIN, RATIO_MAX = _guarded.RATIO_MIN, _guarded.RATIO_MAX
MAX_CONTINUITY = _guarded.MAX_CONTINUITY
JUMP = 10.0  # bước nhảy coi là bất khả thi (luật biên độ sàn VN)

DEFAULT_SYMBOLS = ("PPS", "MGG", "VTJ", "MIE", "HHG", "PXA", "VIN")


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2]


def leftover_jumps(rows: list[tuple], t: float) -> list[tuple[str, float]]:
    """G3 — bước nhảy ≥10× CÒN LẠI sau khi mô phỏng ×1000 cho dải `close < t` (hậu kiểm).

    [WHY] Tách khỏi `analyse_symbol` vì qua đường công khai G1/G2 gần như luôn bắt trước (G1 chặn
    tỷ lệ dải ngoài [500,1500], G2 chặn mọi giao thoa >20%) — đây là lưới HẬU KIỂM, tồn tại để bảo
    đảm bất biến cuối cùng, nên phải test được ở tầng đơn vị bằng chuỗi dựng tay.
    """
    sim = [(d, cl * SCALE if cl < t else cl) for d, cl, *_ in rows]
    return [
        (a[0], round(b[1] / a[1], 1))
        for a, b in zip(sim, sim[1:])
        if a[1] and (b[1] / a[1] >= JUMP or b[1] / a[1] <= 1 / JUMP)
    ]


def analyse_symbol(conn: sqlite3.Connection, sym: str) -> dict:
    """Chia chuỗi thành 2 dải theo NGƯỠNG GIÁ T = trung bình nhân hai median; áp 3 guard.

    Lọc theo CỬA SỔ GIÁ chứ không theo ngày: `HHG`/`PXA` có hai dải XEN KẼ theo thời gian
    (12 đoạn), lọc theo ngày sẽ bỏ sót dòng cũ nằm sau mốc.
    [WHY] Trả về dict để DRY-RUN in được và test kiểm được, không tác dụng phụ.
    """
    rows = conn.execute(
        f"SELECT date, close, COALESCE(source,''), is_stale FROM daily_ohlcv "
        f"WHERE symbol=? AND {VALID_ROW_SQL} ORDER BY date",
        (sym,),
    ).fetchall()
    rec: dict = {"symbol": sym, "verdict": "KHONG_DU_DU_LIEU", "n_old": 0, "n_new": 0, "reason": "quá ít dòng"}
    if len(rows) < 3:
        return rec

    segs: list[list[tuple]] = [[rows[0]]]
    for a, b in zip(rows, rows[1:]):
        r = b[1] / a[1] if a[1] else 0
        if r >= JUMP or r <= 1 / JUMP:
            segs.append([b])
        else:
            segs[-1].append(b)
    if len(segs) < 2:
        rec["reason"] = "chuỗi liền mạch — không có bước nhảy bất khả thi"
        return rec

    meds = [_median([x[1] for x in s]) for s in segs]
    lo_med, hi_med = min(meds), max(meds)
    T = (lo_med * hi_med) ** 0.5
    old = [x for x in rows if x[1] < T]
    new = [x for x in rows if x[1] >= T]
    rec |= {"n_old": len(old), "n_new": len(new), "T": T, "ratio": hi_med / lo_med if lo_med else 0}

    if not old or not new:
        rec["reason"] = "một dải rỗng"
        return rec
    if not (RATIO_MIN <= hi_med / lo_med <= RATIO_MAX):
        rec |= {"verdict": "HUY", "reason": f"G1: tỷ lệ dải {hi_med / lo_med:,.0f} ngoài [500,1500]"}
        return rec

    # G2 — mọi cặp NGÀY LIỀN KỀ vắt qua ngưỡng T phải liên tục sau ×1000
    worst, worst_at = 0.0, None
    for a, b in zip(rows, rows[1:]):
        a_old, b_old = a[1] < T, b[1] < T
        if a_old == b_old:
            continue
        lo_v = (a[1] if a_old else b[1]) * SCALE
        hi_v = b[1] if a_old else a[1]
        dev = abs(lo_v - hi_v) / hi_v if hi_v else 1.0
        if dev > worst:
            worst, worst_at = dev, f"{a[0]}→{b[0]}"
    if worst > MAX_CONTINUITY:
        rec |= {
            "verdict": "HUY",
            "reason": f"G2: giao thoa {worst_at} lệch {worst * 100:.1f}% > trần 20%",
        }
        return rec

    # G3 — hậu kiểm: sau rescale không được còn bước nhảy ≥10×
    leftover = leftover_jumps(rows, T)
    if leftover:
        rec |= {"verdict": "HUY", "reason": f"G3: còn {len(leftover)} bước nhảy ≥10× sau rescale: {leftover[:3]}"}
        return rec

    rec |= {
        "verdict": "RESCALE",
        "reason": f"G1 {hi_med / lo_med:,.0f}× · G2 giao thoa tệ nhất {worst * 100:.1f}% · G3 sạch",
        "n_old_stale0": sum(1 for x in old if x[3] == 0),
        "n_old_stale1": sum(1 for x in old if x[3] == 1),
        "n_tagged": sum(1 for x in old if "rescaled" in x[2]),
        "sources": sorted({x[2].split("|")[0].lower() for x in old}),
    }
    return rec


def apply_symbol(conn: sqlite3.Connection, rec: dict) -> int:
    """Ghi ×1000 cho dải cũ. Nhãn theo nguồn thật; chỉ dòng is_stale=0 chưa gắn nhãn."""
    total = 0
    for src in rec["sources"]:
        tag = f"rescaled_{src}_historical_x1000"
        sets = ", ".join(f"{c} = {c} * {SCALE}" for c in PRICE_COLS)
        cur = conn.execute(
            f"UPDATE daily_ohlcv SET {sets}, source = COALESCE(source,'') || '|{tag}' "
            f"WHERE symbol=? AND close < ? AND is_stale=0 AND {VALID_ROW_SQL} "
            f"AND LOWER(COALESCE(source,'')) LIKE ? AND source NOT LIKE '%rescaled%'",
            (rec["symbol"], rec["T"], f"{src}%"),
        )
        total += cur.rowcount
    return total


def run(db: Path, symbols: tuple[str, ...], apply: bool) -> int:
    """DRY-RUN mặc định. Ghi theo lô có kiểm rowcount + rollback (kế thừa kỷ luật công cụ cũ)."""
    conn = sqlite3.connect(str(db))
    recs = [analyse_symbol(conn, s) for s in symbols]
    print(f"DB: {db}\nCHẾ ĐỘ: {'APPLY (ghi thật)' if apply else 'DRY-RUN (không ghi)'}")
    print(f"\n{'mã':6s} {'dải cũ':>7} {'dải mới':>8} {'T':>10} {'tỷ lệ':>8}  phán quyết")
    for r in recs:
        if r["verdict"] == "RESCALE":
            print(f"{r['symbol']:6s} {r['n_old']:>7} {r['n_new']:>8} {r['T']:>10,.1f} {r['ratio']:>8,.0f}  "
                  f"RESCALE — {r['reason']}")
            print(f"       nguồn dải cũ: {r['sources']} | is_stale=0: {r['n_old_stale0']} · "
                  f"is_stale=1: {r['n_old_stale1']} | đã có nhãn rescale: {r['n_tagged']}")
        else:
            print(f"{r['symbol']:6s} {'-':>7} {'-':>8} {'-':>10} {'-':>8}  {r['verdict']} — {r['reason']}")

    todo = [r for r in recs if r["verdict"] == "RESCALE"]
    n_rows = sum(r["n_old_stale0"] - r["n_tagged"] for r in todo)
    print(f"\nSẽ ghi ×1000 cho {n_rows:,} dòng (is_stale=0, chưa gắn nhãn) thuộc {len(todo)}/{len(symbols)} mã.")
    if not apply or n_rows == 0:
        print("\nDRY-RUN hoặc idempotent: không ghi gì." if not apply else "\nKhông có dòng nào cần ghi.")
        conn.close()
        return 0

    bak = db.with_name(f"{db.name}.bak_rescale_unit_{datetime.now():%Y%m%d_%H%M%S}")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(bak))
    src.backup(dst)
    src.close()
    dst.close()
    print(f"\n[BACKUP] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")

    try:
        written = sum(apply_symbol(conn, r) for r in todo)
        for r in todo:
            left = conn.execute(
                f"SELECT COUNT(*) FROM daily_ohlcv WHERE symbol=? AND close < ? AND is_stale=0 "
                f"AND {VALID_ROW_SQL} AND source NOT LIKE '%rescaled%'",
                (r["symbol"], r["T"]),
            ).fetchone()[0]
            if left:
                raise RuntimeError(f"{r['symbol']}: còn {left} dòng is_stale=0 chưa được xử lý")
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — fail-closed: không để ghi nửa vời
        conn.rollback()
        print(f"DỪNG — đã rollback. Lý do: {exc}")
        conn.close()
        return 1

    print(f"✅ Đã rescale {written:,} dòng.")
    for r in todo:
        lo, hi = conn.execute(
            f"SELECT MIN(close), MAX(close) FROM daily_ohlcv WHERE symbol=? AND is_stale=0 AND {VALID_ROW_SQL}",
            (r["symbol"],),
        ).fetchone()
        print(f"   {r['symbol']:6s} dải giá is_stale=0 hiện tại: {lo:,.0f} – {hi:,.0f}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Chuẩn hóa đơn vị lịch sử (nghìn đồng → đồng, ×1000)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="danh sách mã, phân cách phẩy")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    syms = tuple(s.strip().upper() for s in a.symbols.split(",") if s.strip())
    return run(a.db, syms, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())

