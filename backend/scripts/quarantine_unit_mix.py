r"""Cách ly dòng "trộn đơn vị" (unit-mix phantom) trong `daily_ohlcv` — additive, idempotent, không xoá.

[WHY] Đo 2026-10-16: 452 cặp ngày liên tiếp nhảy ≥100× trên **dòng HỢP LỆ vị từ** (92 mã, ~100%
`is_stale = 0`). Dấu vân tay: hệ số đúng ~900–1000 và nhảy QUA LẠI trong cùng một mã
(`DDG`: 1.000 → 900.000 → 1.100; `BII`: 29 cặp `1.000 ⇄ 900.000`). Neo ngoài (Sở GDCK/Vietstock/CafeF
do chủ hệ thống tra): `DDG` ~800–1.100đ, `BII` ~300–500đ, `NOS` ~400–600đ ⇒ **nhánh hàng trăm nghìn
đồng là phantom**, nhánh vài trăm–vài nghìn đồng là giá thật.

[ROOT CAUSE] Một script backfill cũ áp phép ×1.000 của VCI lên chuỗi ĐÃ ở đơn vị đồng (penny < 10.000đ)
⇒ sinh một ĐOẠN (không phải một dòng) sai thang, bị chặn hai đầu bằng cặp nhảy ×~1000 và ÷~1000.

[INVARIANT]
  1. **Luật biên độ**: chứng khoán niêm yết VN bị chặn biên độ/ngày (HNX ±10%, HOSE ±7%, UPCoM ±15%,
     ngày đầu ±20%) ⇒ một bước nhảy **≥ 10× trong một ngày là BẤT KHẢ THI** ⇒ mọi biên đoạn ≥10× là
     LỖI DỮ LIỆU, không phải biến động. (Đo thật: bucket 3–10× = **0 cặp** trên toàn store ⇒ ngưỡng
     10× có dư địa an toàn rất lớn.)
  2. **Chỉ cách ly cái CHỨNG MINH ĐƯỢC**: đoạn phantom phải hoặc (a) bị kẹp giữa hai bước nhảy ≥10×
     (đoạn nội bộ), hoặc (b) nằm ở mép chuỗi VÀ có NEO NGOÀI xác nhận. Trường hợp còn mờ ⇒ ghi nhãn
     `UNIT_MIX_UNRESOLVED`, **KHÔNG cách ly** (thà thiếu còn hơn cách ly oan dữ liệu thật).
  3. **Additive, không xoá**: `is_stale = 1` + gắn nhãn vào `source`; giá trị `close/high/low/open`
     giữ nguyên để còn truy vết và còn khả năng phục hồi.
  4. **Idempotent**: chạy lại là no-op (chỉ đụng dòng `is_stale = 0` chưa mang nhãn).
  5. Mặc định DRY-RUN; chỉ ghi khi có `--apply`, và luôn backup trước khi ghi.

⚠️ HẠN CHẾ ĐÃ KHAI BÁO: `is_stale = 1` loại dòng khỏi đường **PIT/live**, nhưng **panel lịch sử chỉ
lọc `VALID_ROW_SQL`** nên dòng phantom VẪN lọt vào panel/backtest. Muốn bịt hẳn phải thêm vị từ theo
nhãn vào đường đọc panel — việc riêng, chưa làm ở đây.

Chạy:
    python backend/scripts/quarantine_unit_mix.py              # xem trước
    python backend/scripts/quarantine_unit_mix.py --apply      # ghi (có backup)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.database.data_validity import VALID_ROW_SQL
from src.database.db_core import DB_PATH

TAG = "quarantine:unit_mix_phantom_1000x"
TAG_UNRESOLVED = "unit_mix:UNRESOLVED"
JUMP = 10.0  # bước nhảy coi là bất khả thi (luật biên độ)

#: Neo ngoài do chủ hệ thống tra (Sở GDCK/Vietstock/CafeF, 2026-10-16) — giá THẬT theo đồng.
#: Dùng để phân xử khi đoạn phantom nằm ở mép chuỗi (không có bước nhảy bao hai đầu).
EXTERNAL_ANCHOR = {
    "DDG": (800.0, 1100.0),
    "BII": (300.0, 500.0),
    "NOS": (400.0, 600.0),
    # Bổ sung 2026-10-16 (neo chủ hệ thống tra). ĐO TRƯỚC KHI THÊM cho thấy 7 mã này KHÔNG có
    # dòng nào ≥100× cận trên neo ⇒ lệnh cách ly sẽ ghi 0 dòng; giá trị thật của chúng nằm ở
    # defect ĐỐI XỨNG: dải lịch sử bị THIẾU 1000× (đơn vị "nghìn đồng") chứ không phải bị nhân lên.
    "PPS": (9000.0, 10000.0),
    "MGG": (23500.0, 24500.0),
    "VTJ": (3400.0, 3600.0),
    "MIE": (5500.0, 5500.0),
    "HHG": (700.0, 1000.0),
    "PXA": (600.0, 800.0),
    "VIN": (30000.0, 40000.0),
}


def _segments(rows: list[tuple]) -> list[list[tuple]]:
    """Cắt chuỗi (date, close) thành các đoạn cùng thang; biên = bước nhảy ≥ JUMP."""
    segs: list[list[tuple]] = [[rows[0]]]
    for prev, cur in zip(rows, rows[1:]):
        r = cur[1] / prev[1] if prev[1] else 0
        if r >= JUMP or r <= 1 / JUMP:
            segs.append([cur])
        else:
            segs[-1].append(cur)
    return segs


def analyse(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """(đoạn CÁCH LY được, đoạn còn MỜ) — chỉ trên dòng qua được vị từ.

    [WHY] Hai bản dry-run trước đã chứng minh bằng THỰC NGHIỆM rằng **cấu trúc chuỗi một mình
    không đủ để phân xử bên nào sai**:
      · Bản 1 (mốc = "đoạn trội theo số dòng"): gắn cờ cả nhánh ĐÚNG của DDG (median 1.000–1.100).
      · Bản 2 (mốc = "đoạn cô lập giữa hai đoạn liền kề cùng thang"): gắn cờ cả chuỗi HỢP LỆ 6 năm
        của DDG (median 17.160, 1.304 dòng) và chuỗi 4.900 kéo 3 năm của BII — vì khi hai thang
        XEN KẼ trong cùng một mã thì mọi tiêu chí "đoạn nào bị bao quanh" đều có thể chỉ sai bên.
    ⇒ Nguyên tắc chốt: **chỉ cách ly khi có NEO NGOÀI**, và neo chỉ dùng được cho mã mà ta có nó;
    phần còn lại **KHAI BÁO, KHÔNG ĐỤNG** (fail-closed: thà để lại dữ liệu nghi vấn có nhãn còn hơn
    cách ly oan dữ liệu thật — và ngược lại, không hợp thức hoá phantom bằng suy đoán).

    Tiêu chí cách ly (đo được, bảo thủ): mã có neo `[lo, hi]` ⇒ dòng/đoạn có giá ≥ `100 × hi` là
    phantom. Hệ số 100× chừa dư địa cho biến động HỢP LỆ nhiều năm (DDG 2020 ~17.160đ = 17× neo)
    mà vẫn bắt trọn nhánh ~1.000× (600.000–900.000đ = 545–818× neo).
    """
    syms = [r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM daily_ohlcv WHERE {VALID_ROW_SQL}")]
    quarantine: list[dict] = []
    unresolved: list[dict] = []
    for sym in sorted(syms):
        rows = conn.execute(
            f"SELECT date, close FROM daily_ohlcv WHERE symbol=? AND {VALID_ROW_SQL} ORDER BY date", (sym,)
        ).fetchall()
        if len(rows) < 2:
            continue
        segs = _segments(rows)
        if len(segs) == 1:
            continue  # chuỗi liền mạch ⇒ không có bước nhảy bất khả thi ⇒ không đụng
        anchor = EXTERNAL_ANCHOR.get(sym)
        bands = ", ".join(f"{sorted(c for _d, c in s)[len(s) // 2]:,.0f}" for s in segs)
        jumped = sum(len(s) for s in segs)
        declared = False
        if anchor:
            cut = anchor[1] * 100
            for seg in segs:
                med = sorted(c for _d, c in seg)[len(seg) // 2]
                if med >= cut:
                    quarantine.append(
                        {
                            "symbol": sym,
                            "from": seg[0][0],
                            "to": seg[-1][0],
                            "n_rows": len(seg),
                            "median": med,
                            "cut": cut,
                            "anchor": anchor,
                            "verdict": f"CÁCH LY (neo ngoài {anchor[0]:,.0f}–{anchor[1]:,.0f}đ; "
                            f"đoạn {med:,.0f}đ = {med / anchor[1]:,.0f}× neo)",
                        }
                    )
            # [WHY] Lưới ĐỐI XỨNG: neo cũng phân xử được bên THIẾU thang. Đo 2026-10-16: PPS/MGG/VTJ/
            # MIE/HHG/PXA/VIN có dải lịch sử nhỏ hơn giá thật ~1000× (vd PPS 5–11 trong khi thật
            # 9.000–10.000đ ⇒ dải lịch sử ở đơn vị "nghìn đồng"), KHÔNG phải bị nhân lên. Nhưng đây là
            # quyết định ĐƠN VỊ CHO CẢ LỊCH SỬ (rescale) chứ không phải cách ly ⇒ CHỈ KHAI BÁO, cấm ghi.
            for seg in segs:
                med = sorted(c for _d, c in seg)[len(seg) // 2]
                if med and med * 100 <= anchor[0]:
                    unresolved.append(
                        {
                            "symbol": sym,
                            "from": seg[0][0],
                            "to": seg[-1][0],
                            "n_rows": len(seg),
                            "median": med,
                            "anchor": anchor,
                            "verdict": "KHAI BÁO: nghi THIẾU ~1000× (đơn vị 'nghìn đồng') — "
                            "cần quyết định ĐƠN VỊ cho cả lịch sử, KHÔNG tự rescale",
                            "bands": bands,
                        }
                    )
                    declared = True
        if declared:
            continue
        unresolved.append(
            {
                "symbol": sym,
                "from": rows[0][0],
                "to": rows[-1][0],
                "n_rows": jumped,
                "median": 0,
                "anchor": anchor,
                "verdict": TAG_UNRESOLVED,
                "bands": bands,
            }
        )
    return quarantine, unresolved


def audit_rescaled_rows(conn: sqlite3.Connection) -> None:
    """LƯỚI THEO NHÃN `rescaled_vci_x1000` — CHỈ KHAI BÁO, KHÔNG ghi.

    [WHY] Đo 2026-10-16: nhãn này có trong DỮ LIỆU nhưng KHÔNG có trong mã nguồn (`grep` = 0 kết quả)
    ⇒ di sản của một script ad-hoc đã biến mất. 4.633 dòng / 171 mã, trong đó 3.891 dòng vẫn LIVE.

    [ROOT CAUSE] Script đó nhân ×1000 lên một số dòng `vci`, nhưng việc đó ĐÚNG với 47 mã (giá vci vốn
    ở đơn vị "nghìn đồng") và SAI với một số mã khác.

    [INVARIANT] Cấm tự động cách ly theo nhãn: quy tắc "nhãn + tỉ lệ ∈ [300, 3000] ⇒ phantom" sẽ chặn
    nhầm chính những dòng ĐÚNG — 3 dòng rescaled của `PPS` ở 10.400đ, của `VTJ` ở 3.600đ, của `MGG` ở
    24.400đ ĐỀU khớp neo ngoài ⇒ chúng là bản VÁ ĐÚNG, không phải phantom. Lưới nhãn chỉ để ĐẾM và
    đối chiếu neo; mọi thay đổi dữ liệu phải qua neo (xem `analyse`).
    """
    import statistics as _st

    syms = [
        r[0]
        for r in conn.execute("SELECT DISTINCT symbol FROM daily_ohlcv WHERE source LIKE '%rescaled_vci%'")
    ]
    tagged = conn.execute(
        "SELECT COUNT(*), SUM(is_stale) FROM daily_ohlcv WHERE source LIKE '%rescaled_vci%'"
    ).fetchone()
    print(f"\n### LƯỚI THEO NHÃN rescaled_vci_x1000 (KHAI BÁO — không ghi): "
          f"{tagged[0]} dòng / {len(syms)} mã; trong đó is_stale=1: {tagged[1] or 0} "
          f"⇒ còn LIVE: {tagged[0] - (tagged[1] or 0)}")
    for sym in sorted(syms):
        t = [
            r[0]
            for r in conn.execute(
                f"SELECT close FROM daily_ohlcv WHERE symbol=? AND {VALID_ROW_SQL} AND source LIKE '%rescaled_vci%'",
                (sym,),
            )
        ]
        p = [
            r[0]
            for r in conn.execute(
                f"SELECT close FROM daily_ohlcv WHERE symbol=? AND {VALID_ROW_SQL}"
                " AND (source IS NULL OR source NOT LIKE '%rescaled_vci%')",
                (sym,),
            )
        ]
        if not t or not p:
            continue
        ratio = _st.median(t) / _st.median(p)
        if 300 <= ratio <= 3000:
            anc = EXTERNAL_ANCHOR.get(sym)
            med_t = _st.median(t)
            if not anc:
                note = "KHÔNG có neo ⇒ khai báo, cấm ghi"
            elif med_t >= anc[1] * 100:
                note = (f"neo {anc[0]:,.0f}–{anc[1]:,.0f}đ ⇒ dòng CÓ NHÃN lệch xa neo "
                        f"({med_t:,.0f}đ) ⇒ PHANTOM (đã/đang cách ly theo neo)")
            elif med_t * 100 <= anc[0]:
                note = (f"neo {anc[0]:,.0f}–{anc[1]:,.0f}đ ⇒ dòng CÓ NHÃN THIẾU ~1000× "
                        f"({med_t:,.0f}đ) ⇒ khai báo, cần quyết định ĐƠN VỊ")
            else:
                note = (f"neo {anc[0]:,.0f}–{anc[1]:,.0f}đ ⇒ dòng CÓ NHÃN KHỚP neo "
                        f"({med_t:,.0f}đ) ⇒ nhãn là bản VÁ ĐÚNG, KHÔNG được cách ly")
            print(f"   {sym:6s} tỉ lệ median(có nhãn/không)={ratio:>9.1f} n_có_nhãn={len(t):>5} "
                  f"n_không={len(p):>5}  ⇒ {note}")

def rows_to_quarantine(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Đếm dòng sẽ ghi. Chặn theo NGƯỠNG GIÁ (>= cut), KHÔNG theo cửa sổ ngày: cửa sổ của một đoạn
    phantom có thể chứa dòng hợp lệ XEN KẼ (BII xen 800.000 với 1.000 trong cùng khoảng thời gian)
    ⇒ lọc theo ngày sẽ cách ly oan dòng thật.
    """
    total = 0
    for r in records:
        n = conn.execute(
            f"SELECT COUNT(*) FROM daily_ohlcv WHERE symbol=? AND date>=? AND date<=? AND close>=?"
            f" AND {VALID_ROW_SQL}"
            " AND is_stale = 0 AND (source IS NULL OR source NOT LIKE '%' || ? || '%')",
            (r["symbol"], r["from"], r["to"], r["cut"], TAG),
        ).fetchone()[0]
        r["to_write"] = n
        total += n
    return total


def apply_quarantine(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Ghi cách ly (additive). Trả về số dòng đã ghi; chạy lại là no-op (idempotent)."""
    n = rows_to_quarantine(conn, records)
    if not n:
        return 0
    with conn:
        for r in records:
            conn.execute(
                f"UPDATE daily_ohlcv SET is_stale = 1,"
                f" source = COALESCE(source, '') || '|{TAG}'"
                f" WHERE symbol=? AND date>=? AND date<=? AND close>=? AND {VALID_ROW_SQL}"
                f" AND is_stale = 0 AND (source IS NULL OR source NOT LIKE '%' || ? || '%')",
                (r["symbol"], r["from"], r["to"], r["cut"], TAG),
            )
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Cách ly dòng trộn đơn vị trong daily_ohlcv")
    ap.add_argument("--apply", action="store_true", help="ghi thay đổi (mặc định chỉ xem trước)")
    args = ap.parse_args()

    db = Path(DB_PATH)
    conn = sqlite3.connect(str(db))
    quarantine, unresolved = analyse(conn)

    print(f"Kho: {db}")
    print(f"Đoạn bị cách ly: {len(quarantine)} | đoạn CÒN MỜ (không đụng): {len(unresolved)}")
    print(f"\n{'mã':6s} {'từ ngày':11s} {'đến ngày':11s} {'n':>5} {'median đoạn':>12}  phán quyết")
    for r in quarantine:
        print(f"{r['symbol']:6s} {r['from']:11s} {r['to']:11s} {r['n_rows']:>5} {r['median']:>12,.0f}"
              f"  {r['verdict']}")
    if unresolved:
        kinds = {}
        for r in unresolved:
            k = "THIẾU thang (~1000×)" if "THIẾU" in r["verdict"] else "chưa phân xử (không neo)"
            kinds[k] = kinds.get(k, 0) + 1
        print(f"\nKHAI BÁO (KHÔNG cách ly — {len(unresolved)} mục): " + ", ".join(f"{k}: {v}" for k, v in kinds.items()))
        for r in unresolved:
            tag = "  <== THIẾU thang (~1000×, đơn vị 'nghìn đồng')" if "THIẾU" in r["verdict"] else ""
            print(f"  {r['symbol']:6s} {r['from']}→{r['to']} n={r['n_rows']:<6} "
                  f"thang:[{r['bands']}]" + (f" neo={r['anchor']}" if r["anchor"] else "") + tag)

    audit_rescaled_rows(conn)
    total_rows = rows_to_quarantine(conn, quarantine)
    print(f"\nDòng sẽ gắn is_stale = 1 + nhãn '{TAG}': {total_rows}")

    if not args.apply:
        print("\nDRY-RUN: chưa ghi gì. Thêm --apply để thực thi (sẽ backup trước).")
        return 0

    if total_rows == 0:
        print("\nKhông có dòng nào cần ghi (idempotent).")
        return 0

    bak = db.with_name(f"{db.name}.bak_unitmix_{datetime.now():%Y%m%d_%H%M%S}")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(bak))
    src.backup(dst)
    src.close()
    dst.close()
    print(f"\n[BACKUP] {bak.name} ({bak.stat().st_size / 1e6:.0f} MB)")

    written = apply_quarantine(conn, quarantine)
    print(f"Đã ghi {written} dòng.")
    left = conn.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE is_stale = 0 AND source LIKE '%' || ? || '%'", (TAG,)
    ).fetchone()[0]
    print(f"Đã ghi. Còn dòng nào is_stale = 0 mang nhãn (kỳ vọng 0): {left}")
    print("\nKIỂM CHỨNG LẠI (chạy lại công cụ này — kỳ vọng 0 dòng cần ghi).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
