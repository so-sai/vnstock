"""
structural_detector.py — Bộ phát hiện lệch cấu trúc thị trường

Đo 3 trụ:
  1. Lan tỏa dòng tiền (breadth + LCR)
  2. Đồng thuận ngành (sector diffusion)
  3. Chỉ số vs nội bộ (BDI + RS dispersion)

Output: 1 trong 4 trạng thái cấu trúc + vector nguyên nhân
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path

PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import numpy as np
import pandas as pd

import src.config
from src.database.db_core import get_connection


def _get_historical_data(target_date: str) -> dict:
    """
    Truy vấn dữ liệu lịch sử tại target_date từ DB.
    Trả về dict chứa các chỉ số cấu trúc.
    """
    from src.engine.breadth_engine import run_breadth_analysis

    # Lấy breadth stats tại target_date
    try:
        pulse = run_breadth_analysis(target_date=target_date)
        health = pulse.get("health_score_ma20") if pulse else None
    except Exception:
        health = None

    # Lấy VNINDEX và dữ liệu cổ phiếu tại target_date
    with get_connection() as conn:
        df_idx = pd.read_sql(
            "SELECT close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date=?",
            conn, params=(target_date,)
        )
        vnindex = float(df_idx.iloc[0]['close']) if not df_idx.empty else None

        df_stocks = pd.read_sql(
            "SELECT symbol, close, volume FROM daily_ohlcv WHERE date=? AND symbol!='VNINDEX' AND close>0",
            conn, params=(target_date,)
        )

        # Lấy giá trước đó cho từng symbol riêng (max date < target_date)
        df_prev = pd.read_sql(
            "SELECT a.symbol, a.close as close_prev FROM daily_ohlcv a "
            "INNER JOIN ("
            "  SELECT symbol, MAX(date) as max_date FROM daily_ohlcv "
            "  WHERE date<? AND symbol!='VNINDEX' AND close>0 GROUP BY symbol"
            ") b ON a.symbol=b.symbol AND a.date=b.max_date",
            conn, params=(target_date,)
        )

        df_idx_prev = pd.read_sql(
            "SELECT close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date = "
            "(SELECT MAX(date) FROM daily_ohlcv WHERE date<? AND symbol='VNINDEX')",
            conn, params=(target_date,)
        )

    if df_stocks.empty:
        return {}

    # Merge previous close
    merged = df_stocks.merge(df_prev, on='symbol', how='left')
    merged['change_pct'] = np.where(
        merged['close_prev'] > 0,
        (merged['close'] - merged['close_prev']) / merged['close_prev'] * 100,
        0
    )

    # Weight proxy (market cap = close * volume)
    merged['weight'] = merged['close'] * merged['volume']
    total_weight = merged['weight'].sum()
    if total_weight > 0:
        merged['weight_pct'] = merged['weight'] / total_weight * 100
    else:
        merged['weight_pct'] = 1.0 / len(merged)

    # LCR: % tổng weight từ top 10
    top10 = merged.nlargest(10, 'weight')
    lcr_pct = round(top10['weight_pct'].sum(), 1) if len(top10) > 0 else None

    # BDI: chênh lệch % thay đổi VNINDEX vs % stock tăng
    idx_change = None
    if vnindex is not None and not df_idx_prev.empty:
        idx_prev = float(df_idx_prev.iloc[0]['close'])
        if idx_prev > 0:
            idx_change = (vnindex - idx_prev) / idx_prev * 100

    pct_up = (merged['change_pct'] > 0.5).sum() / len(merged) * 100 if len(merged) > 0 else 0
    bdi_pct = round((idx_change or 0) - pct_up, 1)

    if bdi_pct is not None:
        if abs(bdi_pct) < 5:
            bdi_signal = "CHỈ_SỐ_PHẢN_ÁNH_THỊ_TRƯỜNG"
        elif bdi_pct > 0:
            bdi_signal = "CHỈ_SỐ_MẠNH_HƠN_NỘI_BỘ"
        else:
            bdi_signal = "NỘI_BỘ_MẠNH_HƠN_CHỈ_SỐ"
    else:
        bdi_signal = "CHƯA_CÓ_DỮ_LIỆU"

    # Sector heatmap
    ind_map = _get_industry_map()
    merged['sector'] = merged['symbol'].map(ind_map)
    sector_groups = merged[merged['sector'].notna()].groupby('sector')['change_pct']
    sector_changes = []
    for sector_name, group in sector_groups:
        avg_ch = group.mean()
        sector_changes.append({"sector": sector_name, "avg_change": round(float(avg_ch), 2)})
    sector_changes.sort(key=lambda x: abs(x['avg_change']), reverse=True)

    return {
        "health_score_ma20": health,
        "lcr_pct": lcr_pct,
        "bdi_pct": bdi_pct,
        "bdi_signal": bdi_signal,
        "sectors": sector_changes,
        "pct_up": round(pct_up, 1),
        "idx_change": round(idx_change, 2) if idx_change is not None else None,
        "vnindex": vnindex,
    }


def _get_industry_map() -> dict:
    """Lấy map symbol → icb_name2 từ DB."""
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
        return dict(zip(df['symbol'], df['icb_name2']))
    except Exception:
        return {}


def _tru_lan_toa(health: Optional[float], lcr: Optional[float]) -> dict:
    """Trụ 1: Dòng tiền có lan rộng không?"""
    nguyen_nhan = []
    ok = False

    if health is not None:
        he_so = 1.0
        if lcr is not None and lcr > 40:
            he_so = 0.7
        elif lcr is not None and lcr > 30:
            he_so = 0.85
        hieu_chinh = health * he_so
        if hieu_chinh > 50:
            ok = True
            nguyen_nhan.append(f"dòng tiền lan rộng (breadth={health:.0f}%)")
        elif hieu_chinh > 30:
            nguyen_nhan.append(f"dòng tiền trung bình (breadth={health:.0f}%, LCR={lcr}%)" if lcr else f"dòng tiền trung bình (breadth={health:.0f}%)")
        else:
            nguyen_nhan.append(f"dòng tiền co hẹp (breadth={health:.0f}%, LCR={lcr}%)" if lcr else f"dòng tiền co hẹp (breadth={health:.0f}%)")
    else:
        nguyen_nhan.append("chưa có dữ liệu độ rộng thị trường")

    return {"ok": ok, "do_rong": round(health, 1) if health is not None else None, "lcr": lcr, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tru_dong_thuan_nganh(sectors: list) -> dict:
    """Trụ 2: Các ngành có đồng thuận không?"""
    nguyen_nhan = []
    ok = False

    if sectors:
        tong = len(sectors)
        tang = sum(1 for s in sectors if s.get("avg_change", 0) > 0)
        if tong > 0:
            ty_le = (tang / tong) * 100
            if ty_le > 60:
                ok = True
                nguyen_nhan.append(f"{tang}/{tong} ngành tăng ({ty_le:.0f}%)")
            elif ty_le > 40:
                nguyen_nhan.append(f"{tang}/{tong} ngành tăng ({ty_le:.0f}%) — phân hóa nhẹ")
            else:
                nguyen_nhan.append(f"chỉ {tang}/{tong} ngành tăng ({ty_le:.0f}%) — thiếu đồng thuận")
        else:
            nguyen_nhan.append("không có dữ liệu ngành")
    else:
        nguyen_nhan.append("không có dữ liệu đồng thuận ngành")

    return {"ok": ok, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tru_index_vs_noi_bo(bdi_pct: Optional[float], bdi_signal: Optional[str]) -> dict:
    """Trụ 3: Chỉ số có phản ánh đúng thị trường không?"""
    nguyen_nhan = []
    ok = False

    if bdi_pct is not None:
        abs_bdi = abs(bdi_pct)
        if abs_bdi < 5:
            ok = True
            nguyen_nhan.append(f"chỉ số phản ánh đúng thị trường (BDI={bdi_pct:+.1f}%)")
        elif abs_bdi < 10:
            nguyen_nhan.append(f"chỉ số lệch nhẹ (BDI={bdi_pct:+.1f}%, {bdi_signal})")
        else:
            nguyen_nhan.append(f"chỉ số lệch nội bộ mạnh (BDI={bdi_pct:+.1f}%, {bdi_signal})")
    else:
        nguyen_nhan.append("không có dữ liệu BDI")

    return {"ok": ok, "bdi": bdi_pct, "bdi_signal": bdi_signal, "nguyen_nhan": "; ".join(nguyen_nhan)}


def _tinh_entropy(health: Optional[float], lcr: Optional[float]) -> Optional[float]:
    """Tính entropy từ breadth và LCR."""
    try:
        from src.engine.driver_normalizer import driver_state_from_engine_outputs
        driver = driver_state_from_engine_outputs(
            breadth_health=health,
            lcr_pct=lcr,
            flow_bias=0.5,
            v_score=0.5,
            t_score=1.0,
        )
        if driver and hasattr(driver, "entropy"):
            return float(driver.entropy)
    except Exception:
        pass
    return None


def _tao_nguyen_nhan(trang_thai: str, tru_1: dict, tru_2: dict, tru_3: dict, entropy: Optional[float]) -> list:
    nguyen_nhan = []
    for t in [tru_1, tru_2, tru_3]:
        nn = t.get("nguyen_nhan", "")
        if nn:
            nguyen_nhan.append(nn)
    if entropy is not None and entropy > 0.8:
        nguyen_nhan.append(f"entropy cao ({entropy:.2f}) — thị trường mất ổn định")
    if trang_thai == "VỠ CẤU TRÚC":
        nguyen_nhan.append("cấu trúc thị trường không còn đồng bộ")
    return nguyen_nhan


def detect_cau_truc(target_date: Optional[str] = None) -> dict:
    """Phát hiện lệch cấu trúc thị trường dựa trên 3 trụ."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ---- Truy vấn dữ liệu lịch sử từ DB ----
    hd = _get_historical_data(target_date)

    health = hd.get("health_score_ma20")
    lcr = hd.get("lcr_pct")
    bdi_pct = hd.get("bdi_pct")
    bdi_signal = hd.get("bdi_signal")
    sectors = hd.get("sectors", [])

    # ---- 3 trụ ----
    tru_1 = _tru_lan_toa(health, lcr)
    tru_2 = _tru_dong_thuan_nganh(sectors)
    tru_3 = _tru_index_vs_noi_bo(bdi_pct, bdi_signal)

    # ---- Entropy ----
    entropy = _tinh_entropy(health, lcr)

    # ---- Đếm số trụ OK ----
    tru_ok = sum([1 if tru_1.get("ok") else 0, 1 if tru_2.get("ok") else 0, 1 if tru_3.get("ok") else 0])

    # ---- Áp dụng luật entropy cho VỠ CẤU TRÚC ----
    if entropy is not None and entropy > 0.8 and tru_ok <= 1:
        trang_thai = "VỠ CẤU TRÚC"
    else:
        trang_thai = {
            3: "ĐỒNG THUẬN",
            2: "PHÂN HÓA BÌNH THƯỜNG",
            1: "PHÂN KỲ CẤU TRÚC",
            0: "VỠ CẤU TRÚC",
        }[tru_ok]

    ket_qua = {
        "ngay": target_date,
        "trang_thai": trang_thai,
        "so_tru_ok": tru_ok,
        "tong_so_tru": 3,
        "tru": {"lan_toa_dong_tien": tru_1, "dong_thuan_nganh": tru_2, "index_vs_noi_bo": tru_3},
        "entropy": round(entropy, 3) if entropy is not None else None,
        "nguyen_nhan": _tao_nguyen_nhan(trang_thai, tru_1, tru_2, tru_3, entropy),
        "metadata": {
            "nguon": "DB (historical replay)" if target_date != datetime.now().strftime("%Y-%m-%d") else "DB (live)",
            "ngay_phan_tich": target_date,
        }
    }

    # ---- Lưu file (chỉ khi live) ----
    if target_date == datetime.now().strftime("%Y-%m-%d"):
        out_dir = Path(src.config.DATA_DIR) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "structural_state.json", "w", encoding="utf-8") as f:
            json.dump(ket_qua, f, indent=2, ensure_ascii=False)

    return ket_qua


def in_bao_cao(ket_qua: dict):
    """In báo cáo console."""
    print("\n" + "=" * 60)
    print("  BỘ PHÁT HIỆN LỆCH CẤU TRÚC THỊ TRƯỜNG")
    print("=" * 60)
    tt = ket_qua.get("trang_thai", "N/A")
    icons = {"ĐỒNG THUẬN": "🟢", "PHÂN HÓA BÌNH THƯỜNG": "🟡", "PHÂN KỲ CẤU TRÚC": "🟠", "VỠ CẤU TRÚC": "🔴"}
    icon = icons.get(tt, "⚪")
    print(f"  {icon} Trạng thái: {tt} ({ket_qua.get('so_tru_ok', 0)}/3 trụ)")
    print(f"  Entropy: {ket_qua.get('entropy', 'N/A')}")
    meta = ket_qua.get("metadata", {})
    if meta.get("nguon"):
        print(f"  Nguồn: {meta['nguon']}")
    print()
    for ten_tru, tru in ket_qua.get("tru", {}).items():
        ok_icon = "✔" if tru.get("ok") else "✘"
        print(f"    {ok_icon} {ten_tru}: {tru.get('nguyen_nhan', '')}")
    print()
    nn = ket_qua.get("nguyen_nhan", [])
    if nn:
        print("  Nguyên nhân:")
        for n in nn:
            print(f"    • {n}")
    print("=" * 60)


if __name__ == "__main__":
    kq = detect_cau_truc()
    in_bao_cao(kq)
