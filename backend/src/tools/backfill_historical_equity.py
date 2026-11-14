"""
backfill_historical_equity.py
=============================
Thực thi Bước 2 của Đề xuất (a) theo Nghị quyết số 20/NQ-HĐKT:
Bù đắp kế toán 22 phiên khuyết trong bảng `paper_equity_curve` (2026-07-10 -> 2026-10-02)
cho danh mục thực nghiệm SEL_PAPER_V1.

Quy tắc kế toán bất biến:
1. Danh mục gồm 5 mã: FPT, VCB, HPG, VNM, TCB (mở vị thế ngày 2026-07-06).
2. Tiền mặt:
   - Trước ngày 2026-07-27 (ngày nhận cổ tức VCB): 7.128.848,35đ
   - Từ ngày 2026-07-27 trở đi: 8.568.848,35đ (+1.440.000đ tiền mặt cổ tức VCB)
3. Số lượng cổ phiếu FPT:
   - Trước ngày 2026-09-21 (ngày GDKHQ thưởng cổ phiếu 10:1): 2.700 cp (giá vốn gốc 72.401,21đ)
   - Từ ngày 2026-09-21 trở đi: 2.970 cp (giá vốn sau điều chỉnh 65.918,01đ)
4. Số lượng các mã còn lại:
   - VCB: 3.200 cp (giá vốn gốc 61.644,72đ)
   - HPG: 8.600 cp (giá vốn gốc 23.435,53đ)
   - VNM: 3.600 cp (giá vốn gốc 55.084,36đ)
   - TCB: 5.900 cp (giá vốn gốc 33.651,24đ)
5. Giá hạch toán: Lấy cột close EOD chuẩn hóa từ `daily_ohlcv`.
6. Kiểm toán & Audit Trail:
   - Ghi nhận vào `eod_write_audit_ledger` với trạng thái BACKFILL_HISTORICAL_EQUITY, kèm input hash SHA-256.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if PROJECT_ROOT.name == "backend":
    PROJECT_ROOT = PROJECT_ROOT.parent
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

PORTFOLIO_ID = "SEL_PAPER_V1"

# Danh mục vị thế bất biến
BASE_POSITIONS = {
    "VCB": {"qty": 3200, "cost_basis": 61644.71526904999},
    "HPG": {"qty": 8600, "cost_basis": 23435.530745150005},
    "VNM": {"qty": 3600, "cost_basis": 55084.364692849995},
    "TCB": {"qty": 5900, "cost_basis": 33651.23835565},
}


def get_accounting_rules(date_iso: str):
    """
    Trả về (settled_cash, positions_dict, total_cost_basis) tại mốc ngày date_iso.
    """
    # 1. Tiền mặt (Cash)
    if date_iso < "2026-07-27":
        settled_cash = 7128848.346689969
    else:
        settled_cash = 8568848.34668997

    # 2. Vị thế FPT
    if date_iso < "2026-09-21":
        fpt_qty = 2700
        fpt_cost = 72401.20530000001
    else:
        fpt_qty = 2970
        fpt_cost = 65918.00646177272

    positions = {
        "FPT": {"qty": fpt_qty, "cost_basis": fpt_cost},
        **BASE_POSITIONS,
    }

    total_cost_basis = sum(pos["qty"] * pos["cost_basis"] for pos in positions.values())
    return settled_cash, positions, total_cost_basis


def compute_daily_equity(conn: sqlite3.Connection, date_iso: str):
    """
    Tính toán toàn bộ các trường của paper_equity_curve tại date_iso.
    """
    settled_cash, positions, total_cost_basis = get_accounting_rules(date_iso)
    cur = conn.cursor()

    market_value = 0.0
    symbol_prices = {}
    for sym, pos in positions.items():
        row = cur.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol = ? AND date = ? AND close > 0",
            (sym, date_iso),
        ).fetchone()
        if not row:
            raise ValueError(f"Thiếu nến OHLCV cho mã {sym} tại ngày {date_iso}")
        close_price = float(row[0])
        symbol_prices[sym] = close_price
        market_value += pos["qty"] * close_price

    unrealized_pnl = market_value - total_cost_basis
    total_equity = settled_cash + market_value

    record = {
        "portfolio_id": PORTFOLIO_ID,
        "date": date_iso,
        "settled_cash": settled_cash,
        "pending_cash_in": 0.0,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl_cum": 0.0,
        "total_equity": total_equity,
        "unsettled_qty_val": 0.0,
        "buying_power": settled_cash,
        "hdr_limit": 0.0,
        "created_at": datetime.now().isoformat(),
        "_symbol_prices": symbol_prices,
    }
    return record


def get_missing_dates(conn: sqlite3.Connection):
    """
    Tìm danh sách các ngày giao dịch có trong daily_ohlcv (2026-07-06 -> 2026-10-02)
    nhưng chưa có trong paper_equity_curve.
    """
    cur = conn.cursor()
    ohlcv_dates = set(
        r[0]
        for r in cur.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date >= '2026-07-06' AND date <= '2026-10-02'"
        ).fetchall()
    )
    equity_dates = set(
        r[0]
        for r in cur.execute(
            "SELECT DISTINCT date FROM paper_equity_curve WHERE date >= '2026-07-06' AND date <= '2026-10-02'"
        ).fetchall()
    )
    return sorted(ohlcv_dates - equity_dates)


def run_backfill(dry_run: bool = False, fix_anomaly_2026_08_12: bool = True):
    print("=" * 80)
    print("  HỘI ĐỒNG KIẾN TRÚC & ĐỊNH LƯỢNG PTCK_VNSTOCK — BÙ ĐẮP PAPER_EQUITY_CURVE")
    print(f"  Chế độ: {'DRY-RUN (Xem trước, không ghi DB)' if dry_run else 'APPLY (Ghi trực tiếp vào CSDL)'}")
    print("=" * 80)

    conn = sqlite3.connect(DB_PATH)
    missing_dates = get_missing_dates(conn)
    print(f"[*] Phát hiện {len(missing_dates)} phiên khuyết trong dải 2026-07-06 -> 2026-10-02:")
    print("   ", missing_dates)

    records = []
    for d in missing_dates:
        rec = compute_daily_equity(conn, d)
        records.append(rec)

    # In bảng đối soát
    print("\n" + "=" * 105)
    print(
        f"{'Ngày':12s} | {'Tiền mặt (VND)':16s} | {'FPT CP':8s} | "
        f"{'Giá trị DM (VND)':18s} | {'Lãi/Lỗ chưa chốt':18s} | {'Tổng NAV (VND)':18s}"
    )
    print("-" * 105)
    for r in records:
        fpt_qty = 2700 if r["date"] < "2026-09-21" else 2970
        print(
            f"{r['date']:12s} | {r['settled_cash']:16,.0f} | {fpt_qty:8d} | "
            f"{r['market_value']:18,.0f} | {r['unrealized_pnl']:18,.0f} | {r['total_equity']:18,.0f}"
        )
    print("=" * 105)

    if fix_anomaly_2026_08_12:
        cur = conn.cursor()
        anom_row = cur.execute(
            "SELECT market_value, total_equity FROM paper_equity_curve WHERE date = '2026-08-12'"
        ).fetchone()
        if anom_row and anom_row[0] < 500_000_000.0:
            rec_fixed = compute_daily_equity(conn, "2026-08-12")
            print("\n[!] Phát hiện dòng lỗi dữ liệu cũ tại phiên 2026-08-12:")
            print(f"    - Giá trị cũ trong DB: MV = {anom_row[0]:,.0f} | Equity = {anom_row[1]:,.0f}")
            print(
                f"    - Chuẩn hóa toán học:   MV = {rec_fixed['market_value']:,.0f} | "
                f"Equity = {rec_fixed['total_equity']:,.0f}"
            )
            records.append(rec_fixed)

    if dry_run:
        print("\n[i] Chế độ DRY-RUN: Hoàn tất đối soát. Không có thay đổi nào được ghi vào CSDL.")
        conn.close()
        return

    # Thực hiện ghi CSDL
    cur = conn.cursor()
    insert_sql = """
        INSERT OR REPLACE INTO paper_equity_curve (
            portfolio_id, date, settled_cash, pending_cash_in, market_value,
            unrealized_pnl, realized_pnl_cum, total_equity, unsettled_qty_val,
            buying_power, hdr_limit, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    payload_for_hash = []
    for r in records:
        payload_for_hash.append((r["date"], r["total_equity"], r["market_value"]))
        cur.execute(
            insert_sql,
            (
                r["portfolio_id"],
                r["date"],
                r["settled_cash"],
                r["pending_cash_in"],
                r["market_value"],
                r["unrealized_pnl"],
                r["realized_pnl_cum"],
                r["total_equity"],
                r["unsettled_qty_val"],
                r["buying_power"],
                r["hdr_limit"],
                r["created_at"],
            ),
        )

    # Tính toán SHA-256 hash của tập dữ liệu vừa nạp
    hash_str = json.dumps(payload_for_hash, sort_keys=True)
    input_hash = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()

    # Ghi nhận vào eod_write_audit_ledger
    audit_sql = """
        INSERT INTO eod_write_audit_ledger (
            run_timestamp, as_of_date, caller_module, target_table,
            parameters, input_hash, output_score, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    cur.execute(
        audit_sql,
        (
            datetime.now().isoformat(),
            "2026-10-02",
            "backfill_historical_equity.py",
            "paper_equity_curve",
            f"rows={len(records)}, scope=2026-07-10..2026-10-02",
            input_hash,
            1.0,
            "BACKFILL_HISTORICAL_EQUITY",
            "Nghi quyet so 20/NQ-HDKT: Bu dap 22 phien khuyet va sua loi du lieu ngay 2026-08-12",
        ),
    )

    conn.commit()
    conn.close()
    print(f"\n[+] ĐÃ APPLY THÀNH CÔNG {len(records)} BẢN GHI VÀO `paper_equity_curve`.")
    print(f"[+] ĐÃ GHI NHẬN AUDIT TRAIL VÀO `eod_write_audit_ledger` (Input Hash: {input_hash[:16]}...).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical paper equity curve")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử nghiệm không ghi CSDL")
    parser.add_argument("--apply", action="store_true", help="Thực thi ghi trực tiếp vào CSDL")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Vui lòng chỉ định --dry-run hoặc --apply.")
        sys.exit(1)

    run_backfill(dry_run=args.dry_run)
