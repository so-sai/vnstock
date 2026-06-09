import sys
from pathlib import Path

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
import json
import os
import src.config
from src.database.db_core import get_connection

# ── Ngưỡng chất lượng dữ liệu ──────────────────────────────
MIN_SESSIONS_1Y = 200   # Đủ để tính RS 1 năm tin cậy
MIN_SESSIONS_BASIC = 50 # Đủ để tính RS 3 tháng
MIN_SESSIONS_RAW = 20   # Đủ để không bị rs_ranker skip ngay

# ── Nhãn đánh giá ──────────────────────────────────────────
NHAN_CAO = "CAO"
NHAN_TRUNG_BINH = "TRUNG_BINH"
NHAN_THIEU_DL = "⚠ Thiếu dữ liệu lịch sử"


def danh_gia_chat_luong_du_lieu() -> pd.DataFrame:
    """
    Đánh giá độ tin cậy dữ liệu cho từng mã cổ phiếu.
    
    Returns:
        DataFrame với các cột: symbol, so_phien, ngay_min, ngay_max,
        du_1_nam, muc_tin_cay, nhan
    """
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT symbol, date, close
            FROM daily_ohlcv
            ORDER BY symbol, date ASC
        """, conn)

    if df.empty:
        print("  ⚠ Không có dữ liệu trong DB.")
        return pd.DataFrame()

    df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed')

    stats = []
    for symbol, group in df.groupby('symbol'):
        group = group.sort_values('date')
        so_phien = len(group)
        ngay_min = group['date'].iloc[0]
        ngay_max = group['date'].iloc[-1]
        du_1_nam = so_phien >= MIN_SESSIONS_1Y
        du_basic = so_phien >= MIN_SESSIONS_BASIC
        du_raw = so_phien >= MIN_SESSIONS_RAW

        if so_phien >= MIN_SESSIONS_1Y:
            muc_tin_cay = NHAN_CAO
            nhan = ""
        elif so_phien >= MIN_SESSIONS_BASIC:
            muc_tin_cay = NHAN_TRUNG_BINH
            nhan = "⚠ Dữ liệu chưa đủ 1 năm"
        else:
            muc_tin_cay = NHAN_THIEU_DL
            nhan = "⚠ Thiếu dữ liệu lịch sử — chưa đủ điều kiện xếp hạng"

        stats.append({
            'symbol': symbol,
            'so_phien': so_phien,
            'ngay_min': ngay_min.strftime('%Y-%m-%d'),
            'ngay_max': ngay_max.strftime('%Y-%m-%d'),
            'du_1_nam': du_1_nam,
            'du_basic': du_basic,
            'muc_tin_cay': muc_tin_cay,
            'nhan': nhan,
        })

    kq = pd.DataFrame(stats)
    kq = kq.sort_values('so_phien', ascending=False).reset_index(drop=True)
    return kq


def loc_bo_bang_tin_cay(symbols: list = None,
                        toi_thieu_phien: int = MIN_SESSIONS_1Y) -> list:
    """
    Lọc danh sách symbols, chỉ giữ lại những mã đủ dữ liệu.
    
    Args:
        symbols: Danh sách cần lọc (None = tất cả)
        toi_thieu_phien: Số phiên tối thiểu (mặc định 200)
    
    Returns:
        Danh sách symbols đạt chuẩn dữ liệu
    """
    dg = danh_gia_chat_luong_du_lieu()
    if dg.empty:
        return symbols or []

    dat_chuan = set(dg.loc[dg['so_phien'] >= toi_thieu_phien, 'symbol'])

    if symbols is None:
        result = sorted(dat_chuan)
    else:
        result = [s for s in symbols if s in dat_chuan]

    loai_bo = [s for s in (symbols or []) if s not in dat_chuan]
    if loai_bo:
        print(f"  ⚠ {len(loai_bo)} mã bị tạm hoãn xếp hạng — thiếu dữ liệu lịch sử (cần ≥{toi_thieu_phien} phiên):")
        for s in loai_bo:
            print(f"     - {s}")

    return result


def xuat_bao_cao(duong_dan: str = None) -> dict:
    """
    Xuất báo cáo chất lượng dữ liệu ra màn hình và file JSON.
    
    Returns:
        Dict chứa thống kê
    """
    dg = danh_gia_chat_luong_du_lieu()
    if dg.empty:
        return {}

    if duong_dan is None:
        duong_dan = os.path.join(src.config.DATA_DIR, "data_quality_report.json")

    tong = len(dg)
    cao = int(dg['muc_tin_cay'].eq(NHAN_CAO).sum())
    tb = int(dg['muc_tin_cay'].eq(NHAN_TRUNG_BINH).sum())
    thap = int(dg['muc_tin_cay'].eq(NHAN_THIEU_DL).sum())

    thieu_du_lieu = dg[dg['so_phien'] < MIN_SESSIONS_1Y].copy()
    danh_sach_thieu = thieu_du_lieu[['symbol', 'so_phien']].to_dict(orient='records')

    bao_cao = {
        'tong_ma': tong,
        'dat_chuan_cao': cao,
        'trung_binh': tb,
        'thap': thap,
        'tyle_dat_chuan': round(cao / tong * 100, 1) if tong else 0,
        'nguong_phien': MIN_SESSIONS_1Y,
        'danh_sach_thieu_du_lieu': danh_sach_thieu,
    }

    with open(duong_dan, 'w', encoding='utf-8') as f:
        json.dump(bao_cao, f, ensure_ascii=False, indent=2)

    # ── In báo cáo ──────────────────────────────────────────
    print("\n  ═══ BỘ ĐÁNH GIÁ ĐỘ TIN CẬY DỮ LIỆU (TẦNG 0) ═══")
    print(f"  Tổng số mã:      {tong}")
    print(f"  Đủ dữ liệu (≥{MIN_SESSIONS_1Y} phiên): {cao} ({bao_cao['tyle_dat_chuan']}%)")
    print(f"  Thiếu một phần:   {tb}")
    print(f"  Thiếu lịch sử:    {thap}")
    print(f"  Ngưỡng tối thiểu: {MIN_SESSIONS_1Y} phiên giao dịch")
    print()

    # Bảng chi tiết
    print(f"  {'Mã':<8} {'Số phiên':>9} {'Ngày đầu':>12} {'Ngày cuối':>12} {'Tình trạng':>30}")
    print(f"  {'─'*8} {'─'*9} {'─'*12} {'─'*12} {'─'*30}")
    for _, row in dg.head(25).iterrows():
        label = row['nhan'] if row['nhan'] else "✔ Đủ dữ liệu"
        print(f"  {row['symbol']:<8} {row['so_phien']:>9} {row['ngay_min']:>12} {row['ngay_max']:>12} {label:>30}")

    if len(dg) > 25:
        print(f"  ... ({len(dg) - 25} mã khác)")

    # Danh sách thiếu dữ liệu
    if danh_sach_thieu:
        print(f"\n  ⚠ CÁC MÃ THIẾU DỮ LIỆU LỊCH SỬ:")
        print(f"  {'Mã':<8} {'Số phiên':>9}")
        print(f"  {'─'*8} {'─'*9}")
        for item in danh_sach_thieu:
            print(f"  {item['symbol']:<8} {item['so_phien']:>9}")
        print(f"\n  → Các mã này chưa đủ điều kiện xếp hạng — cần bổ sung dữ liệu lịch sử.")

    print(f"\n  Báo cáo đã lưu: {duong_dan}")
    print(f"  ═══ KẾT THÚC ĐÁNH GIÁ ═══\n")

    return bao_cao


if __name__ == "__main__":
    xuat_bao_cao()
