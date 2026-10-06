"""snapshot_price_board.py — Chụp vi cấu trúc sổ lệnh real-time qua VCI price_board.

WHY: `vci.history()` chỉ trả 6 cột (không tách thỏa thuận vs khớp lệnh) và KHÔNG
có sổ lệnh. `price_board()` cho snapshot real-time: bid/ask top-3, KL lũy kế,
ngoại mua/bán, room, giá sàn/trần/tham chiếu → đây là cách DUY NHẤT trong stack
hiện tại để tự kiểm chứng "dư bán sàn" và "ngoại ròng" trong phiên.

Giới hạn: chỉ real-time (không tham số ngày) → phải chụp TRONG phiên.

Hai chế độ:
  1) Watchlist (mặc định): chụp vài mã → ghi CSV vi cấu trúc.
  2) FORWARD-COLLECT FOREIGN (--universe --to-db): chụp TOÀN RỔ (~1.5k mã, chia
     chunk) lúc 14:46 sau ATC → ghi foreign vào `market_foreign_history`,
     BẮT BUỘC qua F3 guard (foreign_flow_guard) + cổng phủ tối thiểu.
     Đây là cách DUY NHẤT tích lũy chuỗi dữ liệu ngoại theo ngày, vì stack
     KHÔNG có API foreign lịch sử (probe 2026-10-05: `Trading.foreign_trade` →
     NotImplementedError cho cả kbs và vci; `foreign_buy/sell_volume` chỉ có
     trong schema price_board realtime).

Ghi nối tiếp vào backend/data/microstructure_snapshots.csv
Usage:
    python backend/scripts/snapshot_price_board.py --symbols PNJ,HPG,SSI [--tag M1]
    python backend/scripts/snapshot_price_board.py --universe --to-db --date 2026-10-06

TODO (chưa implement — chống silent failure):
    Hiện `match_price == 0` (mã không khớp lệnh) được ghi `net_vol = net_value = 0`.
    Đúng ngữ nghĩa cho "không giao dịch", NHƯNG gộp chung với "nguồn thật sự thiếu
    dữ liệu foreign" → 3 tháng sau không phân biệt được. Khi cần, thêm cột
    `flow_status` ∈ {'traded_no_foreign', 'no_trade', 'missing'} để tách bạch.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src", PROJECT_ROOT / "backend" / "libs" / "vnstock"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

OUT_CSV = PROJECT_ROOT / "backend" / "data" / "microstructure_snapshots.csv"
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
DEFAULT_SYMBOLS = ["PNJ", "HPG", "SSI", "VNM", "FPT", "MBB", "TCB", "MSN", "VIC", "SHB"]

# Cổng phủ tối thiểu cho forward-collect: fail-closed nếu nguồn trả quá ít mã
MIN_UNIVERSE_ROWS = 1200
UNIVERSE_CHUNK = 100
FOREIGN_SOURCE = "vci_price_board"

FIELDS = [
    "snapshot_at", "tag", "symbol", "match_price", "ref_price", "floor_price", "ceiling_price",
    "open_price", "highest", "lowest", "accumulated_volume", "accumulated_value",
    "bid_1_price", "bid_1_volume", "bid_2_price", "bid_2_volume", "bid_3_price", "bid_3_volume",
    "ask_1_price", "ask_1_volume", "ask_2_price", "ask_2_volume", "ask_3_price", "ask_3_volume",
    "floor_ask_queue", "ceiling_bid_queue", "foreign_buy_volume", "foreign_sell_volume",
    "foreign_net_volume", "total_buy_orders", "total_sell_orders", "current_room", "total_room",
]


def _flatten(df):
    df = df.copy()
    df.columns = ["_".join(str(x) for x in col if str(x) not in ("", "nan")) if isinstance(col, tuple) else str(col) for col in df.columns]
    return df


def _num(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _provider():
    from src.providers.vnstock_provider import VnstockProvider

    return VnstockProvider(source="vci")


def universe_symbols() -> list[str]:
    """Toàn bộ mã có trong store (bỏ chỉ số) — nguồn cho chế độ --universe."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol <> 'VNINDEX' ORDER BY symbol"
            )
        ]
    finally:
        conn.close()


def _capture_chunk(provider, symbols, tag, now):
    df = provider.price_board(symbols)
    if df is None or len(df) == 0:
        return []
    df = _flatten(df)
    records = []
    for _, row in df.iterrows():
        sym = str(row.get("listing_symbol") or row.get("symbol") or "?")
        floor_p = _num(row.get("match_floor_price") or row.get("listing_floor"))
        ceil_p = _num(row.get("match_ceiling_price") or row.get("listing_ceiling"))
        rec = {
            "snapshot_at": now,
            "tag": tag,
            "symbol": sym,
            "match_price": _num(row.get("match_match_price")),
            "ref_price": _num(row.get("match_reference_price") or row.get("listing_ref_price")),
            "floor_price": floor_p,
            "ceiling_price": ceil_p,
            "open_price": _num(row.get("match_open_price")),
            "highest": _num(row.get("match_highest")),
            "lowest": _num(row.get("match_lowest")),
            "accumulated_volume": _num(row.get("match_accumulated_volume")),
            "accumulated_value": _num(row.get("match_accumulated_value")),
            "bid_1_price": _num(row.get("bid_ask_bid_1_price")),
            "bid_1_volume": _num(row.get("bid_ask_bid_1_volume")),
            "bid_2_price": _num(row.get("bid_ask_bid_2_price")),
            "bid_2_volume": _num(row.get("bid_ask_bid_2_volume")),
            "bid_3_price": _num(row.get("bid_ask_bid_3_price")),
            "bid_3_volume": _num(row.get("bid_ask_bid_3_volume")),
            "ask_1_price": _num(row.get("bid_ask_ask_1_price")),
            "ask_1_volume": _num(row.get("bid_ask_ask_1_volume")),
            "ask_2_price": _num(row.get("bid_ask_ask_2_price")),
            "ask_2_volume": _num(row.get("bid_ask_ask_2_volume")),
            "ask_3_price": _num(row.get("bid_ask_ask_3_price")),
            "ask_3_volume": _num(row.get("bid_ask_ask_3_volume")),
            "foreign_buy_volume": _num(row.get("match_foreign_buy_volume")),
            "foreign_sell_volume": _num(row.get("match_foreign_sell_volume")),
            "total_buy_orders": _num(row.get("match_total_buy_orders")),
            "total_sell_orders": _num(row.get("match_total_sell_orders")),
            "current_room": _num(row.get("match_current_room")),
            "total_room": _num(row.get("match_total_room")),
        }
        # Dư bán SÀN = tổng KL ask ở đúng giá sàn (trong 3 mức nhìn thấy)
        faq = 0.0
        if floor_p:
            for i in (1, 2, 3):
                if rec.get(f"ask_{i}_price") == floor_p and rec.get(f"ask_{i}_volume"):
                    faq += rec[f"ask_{i}_volume"]
        rec["floor_ask_queue"] = faq or None
        # Dư mua TRẦN
        cbq = 0.0
        if ceil_p:
            for i in (1, 2, 3):
                if rec.get(f"bid_{i}_price") == ceil_p and rec.get(f"bid_{i}_volume"):
                    cbq += rec[f"bid_{i}_volume"]
        rec["ceiling_bid_queue"] = cbq or None
        rec["foreign_net_volume"] = (
            (rec.get("foreign_buy_volume") or 0.0) - (rec.get("foreign_sell_volume") or 0.0)
            if (rec.get("foreign_buy_volume") is not None or rec.get("foreign_sell_volume") is not None)
            else None
        )
        records.append(rec)
    return records


def capture(symbols, tag="", chunk=UNIVERSE_CHUNK):
    """Chụp 1 hay nhiều chunk (price_board chỉ nhận rổ nhỏ → chia lát)."""
    provider = _provider()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for i in range(0, len(symbols), chunk):
        batch = symbols[i : i + chunk]
        try:
            records.extend(_capture_chunk(provider, batch, tag, now))
        except Exception as e:  # noqa: BLE001 - một chunk lỗi không được giết cả lượt
            print(f"  ⚠️ chunk {i}-{i + len(batch) - 1} lỗi: {type(e).__name__}: {str(e)[:120]}")
    return records, now


def append_csv(records):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not OUT_CSV.exists()
    with OUT_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in records:
            w.writerow(r)


def write_foreign_to_db(records, session_date, source=FOREIGN_SOURCE, min_rows=MIN_UNIVERSE_ROWS, dry_run=False):
    """Ghi foreign của 1 phiên vào market_foreign_history qua F3 guard.

    Trả (exit_code, số dòng ghi được). Fail-closed: guard từ chối hoặc phủ < ngưỡng
    → KHÔNG ghi gì (missing > fake).
    """
    import pandas as pd

    from src.database.foreign_flow_guard import ForeignFlowViolation, guard_foreign_frame

    rows = []
    skipped_no_foreign = 0
    flat_no_trade = 0
    for r in records:
        fb, fs, mp = r.get("foreign_buy_volume"), r.get("foreign_sell_volume"), r.get("match_price")
        # CHỈ loại khi nguồn KHÔNG trả cặp ngoại (None). match_price == 0 nghĩa là mã
        # không khớp lệnh phiên này → net ngoại = 0 là SỰ THẬT, không phải suy diễn
        # (khớp legacy: A32 2026-09-29 = foreign_vol 0, net_vol 0).
        if fb is None or fs is None:
            skipped_no_foreign += 1
            continue
        price = float(mp) if mp else 0.0
        if price <= 0:
            flat_no_trade += 1
        net_vol = int(fb) - int(fs)
        rows.append(
            {
                "symbol": r["symbol"],
                "foreign_vol": int(fb) + int(fs),
                "net_vol": net_vol,
                # net_value dùng CHÍNH match_price chụp cùng thời điểm (không mượn close nguồn khác)
                "net_value": net_vol * price / 1e9,
                "close": price,
            }
        )
    print(
        f"[F3] dựng {len(rows)} dòng (bỏ {skipped_no_foreign} mã nguồn không trả cặp ngoại; "
        f"trong đó {flat_no_trade} mã không khớp lệnh → net=0)"
    )
    if not rows:
        print("❌ không có dòng nào → KHÔNG ghi")
        return 1, 0

    try:
        clean, report = guard_foreign_frame(pd.DataFrame(rows), session_date)
    except ForeignFlowViolation as e:
        print(f"❌ F3 GUARD TỪ CHỐI GHI: {e}")
        return 1, 0
    print(
        f"[F3] kept={report['kept']} dropped_bound={report['dropped_bound']} "
        f"dropped_unit={report['dropped_unit']} Σnet_value={report['session_total_ty']:.1f} tỷ"
    )

    if len(clean) < min_rows:
        print(f"❌ CỔNG PHỦ: {len(clean)} < {min_rows} mã → KHÔNG ghi (fail-closed)")
        return 1, 0

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = [
        (
            str(row["symbol"]),
            session_date,
            int(row.get("foreign_vol") or 0),
            int(row.get("net_vol") or 0),
            float(row.get("net_value") or 0.0),
            source,
            fetched_at,
        )
        for _, row in clean.iterrows()
    ]
    if dry_run:
        print(f"[dry-run] sẽ INSERT OR REPLACE {len(payload)} dòng vào market_foreign_history")
        return 0, len(payload)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO market_foreign_history "
            "(symbol, date, foreign_vol, net_vol, net_value, flow_kind, source, fetched_at, is_verified) "
            "VALUES (?, ?, ?, ?, ?, 'daily', ?, ?, 1)",
            payload,
        )
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM market_foreign_history WHERE date=? AND flow_kind='daily'", (session_date,)
        ).fetchone()[0]
    finally:
        conn.close()
    print(
        f"✅ ghi {len(payload)} dòng foreign phiên {session_date} (source={source}, is_verified=1) "
        f"| tổng daily của phiên trong DB: {n}"
    )
    return 0, len(payload)


def _fmt(v, suffix=""):
    if v is None:
        return "-"
    return f"{v:,.0f}{suffix}" if abs(v) >= 100 else f"{v:,.2f}{suffix}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--tag", default="")
    ap.add_argument("--universe", action="store_true", help="chụp toàn bộ mã có trong store")
    ap.add_argument("--chunk", type=int, default=UNIVERSE_CHUNK)
    ap.add_argument("--to-db", action="store_true", help="ghi foreign vào market_foreign_history (qua F3 guard)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="phiên ghi vào DB")
    ap.add_argument("--min-rows", type=int, default=MIN_UNIVERSE_ROWS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args()

    if args.universe:
        symbols = universe_symbols()
        print(f"=== FORWARD-COLLECT FOREIGN — toàn rổ {len(symbols)} mã, chunk {args.chunk} ===")
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    records, now = capture(symbols, args.tag, chunk=args.chunk)
    if not records:
        print(f"[{now}] SNAPSHOT RỖNG — price_board không trả dữ liệu (ngoài giờ / nguồn lỗi)")
        return 1
    print(f"[{now}] chụp được {len(records)}/{len(symbols)} mã (tag={args.tag or '-'})")

    rc = 0
    if not args.no_csv:
        append_csv(records)
        print(f"→ CSV: {OUT_CSV}")
    if args.to_db:
        rc, _ = write_foreign_to_db(records, args.date, min_rows=args.min_rows, dry_run=args.dry_run)

    if not args.universe:
        print(f"{'sym':6s} {'khớp':>8s} {'sàn':>8s} {'KL lũy kế':>14s} {'ngoại mua':>11s} {'ngoại bán':>11s} {'ngoại ròng':>12s}")
        for r in records:
            print(
                f"{r['symbol']:6s} {_fmt(r['match_price']):>8s} {_fmt(r['floor_price']):>8s} "
                f"{_fmt(r['accumulated_volume']):>14s} {_fmt(r['foreign_buy_volume']):>11s} "
                f"{_fmt(r['foreign_sell_volume']):>11s} {_fmt(r['foreign_net_volume']):>12s}"
            )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
