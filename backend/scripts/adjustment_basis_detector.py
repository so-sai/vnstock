"""adjustment_basis_detector.py — P2: phát hiện lệch CƠ SỞ GIÁ (corporate action).

WHY: SSI cho thấy store giữ **giá thô** trong khi provider trả **giá đã điều
chỉnh quyền** → chuỗi có một bước nhảy ảo (~-20%) tại ex-date, làm hỏng MA/RSI
của riêng mã đó. Ta chỉ tình cờ phát hiện SSI vì nó nằm trong 5 mã mẫu; không
ai biết còn mã nào khác.

Thiết kế 2 tầng (rẻ trước, đắt sau):
  TẦNG 1 — QUÉT CỤC BỘ (không cần mạng): tìm mọi cú nhảy giá 1 phiên vượt biên
    độ sàn. Không có cột sàn trong DB nên dùng 2 ngưỡng:
      >= 8%  : vượt biên HOSE (±7%)  → WARN  (UPCOM có thể hợp lệ tới ±15%)
      >= 16% : vượt cả UPCOM (±15%)  → SEVERE (gần như chắc chắn sự kiện quyền
               hoặc lỗi dữ liệu)
  TẦNG 2 — ĐỐI CHIẾU NGUỒN (--cross-check N): với N mã nặng nhất, so store với
    vci để PHÂN LOẠI: tỷ lệ store/(vci*1000) có BẬC nhảy qua ngày nghi ngờ
    ⇒ sự kiện quyền (store thô vs vci điều chỉnh); bằng phẳng ⇒ không phải.

Mặc định chỉ BÁO CÁO, không sửa gì.

Usage:
    python backend/scripts/adjustment_basis_detector.py
    python backend/scripts/adjustment_basis_detector.py --last 120
    python backend/scripts/adjustment_basis_detector.py --cross-check 8
    python backend/scripts/adjustment_basis_detector.py --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

WARN_PCT = 8.0  # vượt biên HOSE ±7%
SEVERE_PCT = 16.0  # vượt cả UPCOM ±15%


def _recent_dates(conn: sqlite3.Connection, last: int | None) -> list[str]:
    rows = [r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_ohlcv ORDER BY date")]
    return rows[-last:] if last else rows


def scan_jumps(
    conn: sqlite3.Connection,
    last: int | None = None,
    min_pct: float = WARN_PCT,
) -> list[dict]:
    """Cú nhảy 1 phiên vượt ngưỡng, trên chuỗi SẠCH (is_stale=0). Không gọi mạng."""
    dates = _recent_dates(conn, last)
    if not dates:
        return []
    min_date = dates[0]
    rows = conn.execute(
        """
        WITH s AS (
            SELECT symbol, date, close,
                   LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close
            FROM daily_ohlcv
            WHERE COALESCE(is_stale, 0) = 0 AND close IS NOT NULL AND close > 0
              AND date >= ?
        )
        SELECT symbol, date, prev_close, close,
               (close * 1.0 / prev_close - 1.0) * 100.0 AS pct
        FROM s
        WHERE prev_close IS NOT NULL AND prev_close > 0
          AND ABS(close * 1.0 / prev_close - 1.0) * 100.0 >= ?
        ORDER BY ABS(close * 1.0 / prev_close - 1.0) DESC
        """,
        (min_date, min_pct),
    ).fetchall()
    out = []
    for symbol, date, prev_close, close, pct in rows:
        factor = float(close) / float(prev_close) if prev_close else 0.0
        # Hai hiện tượng KHÁC HẲN nhau cùng tạo "cú nhảy", phải tách:
        #   SCALE_BREAK : lệch đơn vị (nghìn đồng vs đồng) → factor ~1000+ (F-a guard lo)
        #   PRICE_JUMP  : nhảy giá thật / sự kiện quyền → factor trong khoảng vài lần
        kind = "SCALE_BREAK" if (factor >= 100.0 or factor <= 0.01) else "PRICE_JUMP"
        out.append(
            {
                "symbol": symbol,
                "date": date,
                "prev_close": float(prev_close),
                "close": float(close),
                "pct": round(float(pct), 2),
                "factor": round(factor, 4),
                "kind": kind,
                "severity": "SEVERE" if abs(pct) >= SEVERE_PCT else "WARN",
            }
        )
    return out


def classify_with_provider(symbol: str, jump_date: str, conn: sqlite3.Connection) -> dict:
    """Đối chiếu 1 mã với vci quanh ngày nghi ngờ → phân loại lệch cơ sở."""
    from src.providers.vnstock_provider import VnstockProvider

    cal = _recent_dates(conn, None)
    idx = cal.index(jump_date) if jump_date in cal else None
    lo = cal[max(0, idx - 4)] if idx is not None else jump_date
    hi = cal[min(len(cal) - 1, idx + 4)] if idx is not None else jump_date

    provider = VnstockProvider(source="vci")
    df = provider.history(symbol, start=lo, end=hi)
    ratios: dict[str, float] = {}
    if df is not None and not df.empty:
        cols = [str(c).lower() for c in df.columns]
        dcol = "time" if "time" in cols else ("date" if "date" in cols else None)
        if dcol and "close" in cols:
            dc, cc = df.columns[cols.index(dcol)], df.columns[cols.index("close")]
            for _, row in df.iterrows():
                d = str(row[dc])[:10]
                v = float(row[cc]) * 1000.0  # vci trả nghìn đồng → VND
                st = conn.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=? AND COALESCE(is_stale,0)=0",
                    (symbol, d),
                ).fetchone()
                if st and v:
                    ratios[d] = round(float(st[0]) / v, 4)

    pre = [r for d, r in ratios.items() if d < jump_date]
    post = [r for d, r in ratios.items() if d >= jump_date]
    step = None
    if pre and post:
        step = round((sum(pre) / len(pre)) - (sum(post) / len(post)), 4)
    verdict = "UNKNOWN"
    if ratios:
        flat = max(ratios.values()) - min(ratios.values()) < 0.001
        verdict = "KHONG_LECH_CO_SO" if flat else "LECH_CO_SO (bac ngu nghia su kien quyen)"
    return {
        "symbol": symbol,
        "jump_date": jump_date,
        "ratios": ratios,
        "ratio_step_pre_minus_post": step,
        "verdict": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Adjustment-Basis Break Detector (P2)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--last", type=int, default=None, help="chỉ quét N phiên gần nhất")
    ap.add_argument("--min-pct", type=float, default=WARN_PCT)
    ap.add_argument("--limit", type=int, default=25, help="số dòng in ra")
    ap.add_argument("--cross-check", type=int, default=0, help="đối chiếu N mã nặng nhất với vci")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        jumps = scan_jumps(conn, last=args.last, min_pct=args.min_pct)
        severe = [j for j in jumps if j["severity"] == "SEVERE"]
        price_jumps = [j for j in jumps if j["kind"] == "PRICE_JUMP"]
        scale_breaks = [j for j in jumps if j["kind"] == "SCALE_BREAK"]
        by_symbol: dict[str, int] = {}
        for j in jumps:
            by_symbol[j["symbol"]] = by_symbol.get(j["symbol"], 0) + 1

        result: dict = {
            "scanned_last": args.last,
            "min_pct": args.min_pct,
            "total_jumps": len(jumps),
            "severe_jumps": len(severe),
            "price_jumps": len(price_jumps),
            "scale_breaks": len(scale_breaks),
            "distinct_symbols": len(by_symbol),
            "top_price_jumps": price_jumps[: args.limit],
            "top_scale_breaks": scale_breaks[:5],
            "cross_check": [],
        }

        if args.cross_check and price_jumps:
            top_syms: list[tuple[str, str]] = []
            seen: set[str] = set()
            for j in price_jumps:
                if j["symbol"] not in seen:
                    seen.add(j["symbol"])
                    top_syms.append((j["symbol"], j["date"]))
                if len(top_syms) >= args.cross_check:
                    break
            for sym, d in top_syms:
                try:
                    result["cross_check"].append(classify_with_provider(sym, d, conn))
                except Exception as e:  # noqa: BLE001 - chẩn đoán best-effort
                    result["cross_check"].append({"symbol": sym, "jump_date": d, "error": f"{type(e).__name__}: {e}"})

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        scope = f"{args.last} phiên gần nhất" if args.last else "toàn bộ lịch sử"
        print(f"=== ADJUSTMENT-BASIS BREAK DETECTOR — quét {scope} (chuỗi sạch is_stale=0) ===")
        print(f"    ngưỡng WARN ≥ {args.min_pct}% (vượt biên HOSE ±7%) · SEVERE ≥ {SEVERE_PCT}% (vượt UPCOM ±15%)")
        print(
            f"    → {len(jumps)} cú nhảy | tách 2 loại: "
            f"SCALE_BREAK (lệch đơn vị ×100+) = {len(scale_breaks)} | "
            f"PRICE_JUMP (nghi sự kiện quyền) = {len(price_jumps)}"
        )
        print(f"    mã liên quan: {len(by_symbol)}")
        if not jumps:
            print("    ✅ không có cú nhảy nào vượt ngưỡng")
        else:
            print(f"\n    --- PRICE_JUMP: ứng viên SỰ KIỆN QUYỀN (cần đối chiếu nguồn) ---")
            print(f"    {'mã':7}{'ngày':13}{'trước':>11}{'sau':>11}{'%':>9}{'factor':>9}  mức")
            for j in price_jumps[: args.limit]:
                print(
                    f"    {j['symbol']:7}{j['date']:13}{j['prev_close']:>11,.0f}{j['close']:>11,.0f}"
                    f"{j['pct']:>9.2f}{j['factor']:>9.3f}  {j['severity']}"
                )
            if scale_breaks:
                print(f"\n    --- SCALE_BREAK: lệch ĐƠN VỊ, không phải sự kiện quyền (5 dòng đầu) ---")
                for j in scale_breaks[:5]:
                    print(
                        f"    {j['symbol']:7}{j['date']:13}{j['prev_close']:>11,.2f}{j['close']:>11,.0f}"
                        f"{j['pct']:>9.0f}{j['factor']:>9.1f}x"
                    )

        if result["cross_check"]:
            print("\n    --- ĐỐI CHIẾU vci (phân loại) ---")
            for c in result["cross_check"]:
                if "error" in c:
                    print(f"    {c['symbol']:7} lỗi: {c['error'][:80]}")
                    continue
                print(
                    f"    {c['symbol']:7} jump={c['jump_date']} → {c['verdict']}"
                    f"  (bậc trước−sau = {c['ratio_step_pre_minus_post']})"
                )
        return 1 if severe else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
