import sys, json, os
from pathlib import Path
from typing import Optional

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent
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

import src.config
import pandas as pd
import numpy as np
from src.database.db_core import get_connection

VINGROUP_SYMBOLS = {"VIC", "VHM", "VRE"}


def _doc(ten_file: str) -> Optional[dict]:
    paths = [
        Path(src.config.DATA_DIR) / "output" / ten_file,
        Path(src.config.DATA_DIR) / ten_file,
    ]
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _tinh_median_return(target_date: str) -> dict:
    """Tính weighted return vs median return từ dữ liệu daily."""
    end = pd.to_datetime(target_date)
    start = end - pd.Timedelta(days=60)
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT symbol, date, close, volume FROM daily_ohlcv "
            "WHERE symbol != 'VNINDEX' AND date >= ? AND date <= ? "
            "ORDER BY symbol, date",
            conn, params=(start.strftime('%Y-%m-%d'), target_date)
        )
        df_idx = pd.read_sql(
            "SELECT date, close as idx_close FROM daily_ohlcv "
            "WHERE symbol='VNINDEX' AND date <= ? ORDER BY date",
            conn, params=(target_date,)
        )
    if df.empty or df_idx.empty:
        return {"weighted_return": 0, "median_return": 0, "divergence": 0, "idx_return": 0}
    df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce')
    df = df.dropna(subset=['date'])
    target_dt = pd.to_datetime(target_date)
    latest = df[df['date'] == target_dt].copy()

    if latest.empty:
        dates = sorted(df['date'].unique())
        target_date = dates[-1] if dates else target_dt
        latest = df[df['date'] == target_dt].copy()
    if latest.empty:
        return {"weighted_return": 0, "median_return": 0, "divergence": 0, "idx_return": 0}

    latest = latest[latest['volume'] >= 10000].copy()
    if latest.empty or latest['close'].sum() == 0:
        return {"weighted_return": 0, "median_return": 0, "divergence": 0, "idx_return": 0}

    prev = df[df['date'] < target_dt].groupby('symbol').last().reset_index()
    merged = latest.merge(prev[['symbol', 'close']], on='symbol', suffixes=('', '_prev'))
    merged.loc[:, 'change_pct'] = (merged['close'] - merged['close_prev']) / merged['close_prev'].replace(0, np.nan)
    merged.loc[:, 'change_pct'] = merged['change_pct'].replace([np.inf, -np.inf], 0).fillna(0).clip(-0.2, 0.2)

    total_value = (merged['close'] * merged['volume'] * 1000).sum()
    if total_value == 0:
        return {"weighted_return": 0, "median_return": 0, "divergence": 0, "idx_return": 0}
    weights = (merged['close'] * merged['volume'] * 1000) / total_value
    weighted_return = float(np.average(merged['change_pct'], weights=weights))
    median_return = float(merged['change_pct'].median())
    divergence = round(weighted_return - median_return, 4)

    idx_latest = df_idx[df_idx['date'] == df_idx['date'].max()]
    idx_prev = df_idx[df_idx['date'] < df_idx['date'].max()].tail(1)
    idx_return = 0.0
    if not idx_latest.empty and not idx_prev.empty:
        idx_lc = float(idx_latest['idx_close'].iloc[0])
        idx_pc = float(idx_prev['idx_close'].iloc[0])
        if idx_pc > 0:
            idx_return = round((idx_lc - idx_pc) / idx_pc * 100, 2)

    return {
        "weighted_return": round(weighted_return * 100, 2),
        "median_return": round(median_return * 100, 2),
        "divergence": round(divergence * 100, 2),
        "idx_return": idx_return,
    }


def _tinh_vingroup_contribution(target_date: str) -> dict:
    """Tính mức đóng góp của nhóm Vingroup vào chỉ số."""
    end = pd.to_datetime(target_date)
    start = end - pd.Timedelta(days=60)
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT symbol, date, close, volume FROM daily_ohlcv "
            "WHERE symbol != 'VNINDEX' AND date >= ? AND date <= ? "
            "ORDER BY symbol, date",
            conn, params=(start.strftime('%Y-%m-%d'), target_date)
        )
    if df.empty:
        return {"vingroup_pct": 0, "vingroup_weight": 0, "top_symbols": []}

    df.loc[:, 'date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce')
    df = df.dropna(subset=['date'])
    recent = df[df['date'] >= end - pd.Timedelta(days=20)].copy()
    recent.loc[:, 'traded_value'] = recent['close'] * recent['volume']
    avg_value = recent.groupby('symbol', as_index=False)['traded_value'].mean()
    avg_value.columns = ['symbol', 'avg_value']
    total = avg_value['avg_value'].sum()
    if total == 0:
        return {"vingroup_pct": 0, "vingroup_weight": 0, "top_symbols": []}

    avg_value.loc[:, 'weight'] = avg_value['avg_value'] / total
    top15 = avg_value.nlargest(15, 'weight')
    vingroup_weight = top15[top15['symbol'].isin(VINGROUP_SYMBOLS)]['weight'].sum()

    latest = df[df['date'] == target_date]
    if latest.empty:
        dates = sorted(df['date'].unique())
        latest = df[df['date'] == dates[-1]]
    vg_latest = latest[latest['symbol'].isin(VINGROUP_SYMBOLS)]
    vg_value = (vg_latest['close'] * vg_latest['volume'] * 1000).sum()
    total_value = (latest['close'] * latest['volume'] * 1000).sum() if not latest.empty else 0
    vg_pct = round(vg_value / total_value * 100, 1) if total_value > 0 else 0

    return {
        "vingroup_weight": round(vingroup_weight * 100, 1),
        "vingroup_value_share": vg_pct,
        "vingroup_symbols": sorted(VINGROUP_SYMBOLS & set(top15['symbol'])),
    }


def phan_tich_chi_so(target_date: Optional[str] = None) -> dict:
    if target_date is None:
        from datetime import datetime
        target_date = datetime.now().strftime("%Y-%m-%d")

    ms = _doc("market_structure.json") or {}
    struct_state = _doc("structural_state.json") or {}

    median_data = _tinh_median_return(target_date)
    vingroup_data = _tinh_vingroup_contribution(target_date)

    vnindex_pct = median_data.get("idx_return", ms.get("vnindex_pct", 0))
    weighted_ret = median_data.get("weighted_return", ms.get("sbmi_pct", 0))
    median_ret = median_data.get("median_return", ms.get("ewmi_pct", 0))
    divergence_pct = median_data.get("divergence", 0)
    idx_ret = median_data.get("idx_return", 0)

    lcr = ms.get("lcr_pct", 0)
    top_n = ms.get("top_n", [])
    bdi_signal = ms.get("bdi_signal", "CAN_BANG")

    sbmi_pct = weighted_ret
    ewmi_pct = median_ret
    bdi_pct = round(vnindex_pct - sbmi_pct, 2)

    vg_weight = vingroup_data.get("vingroup_weight", 0)
    vg_value_share = vingroup_data.get("vingroup_value_share", 0)
    vg_symbols = vingroup_data.get("vingroup_symbols", [])

    bdi_level = abs(bdi_pct)
    if bdi_level < 3:
        chenh_lech_status = "DONG_PHA"
    elif bdi_level < 7:
        chenh_lech_status = "PHAN_KY_NHE"
    elif bdi_level < 12:
        chenh_lech_status = "PHAN_KY_MANH"
    else:
        chenh_lech_status = "BI_KEO_CHI_SO"

    if lcr > 70:
        concentration_level = "RAT_CAO"
    elif lcr > 50:
        concentration_level = "CAO"
    elif lcr > 30:
        concentration_level = "TRUNG_BINH"
    else:
        concentration_level = "THAP"

    signals = []
    signals.append(1.0 if bdi_level < 3 else (0.6 if bdi_level < 7 else (0.3 if bdi_level < 12 else 0.1)))
    signals.append(1.0 if concentration_level == "THAP" else (0.7 if concentration_level == "TRUNG_BINH" else (0.4 if concentration_level == "CAO" else 0.1)))
    signals.append(1.0 if abs(divergence_pct) < 0.5 else (0.5 if abs(divergence_pct) < 2 else 0.2))
    signals.append(1.0 if vg_weight < 15 else (0.6 if vg_weight < 30 else 0.3))
    market_quality = round(sum(signals) / len(signals), 2)

    if market_quality >= 0.8:
        quality_label = "THI_TRUONG_THAT"
    elif market_quality >= 0.5:
        quality_label = "MEO_NHE"
    elif market_quality >= 0.2:
        quality_label = "MEO_MANH"
    else:
        quality_label = "THI_TRUONG_AO"

    if bdi_signal == "PHAN_KY_DUONG" and abs(bdi_pct) > 5:
        regime_bias = "MANH_GIA_TAO" if abs(divergence_pct) > 1 else "LECH_PHA_DUONG"
    elif bdi_signal == "PHAN_KY_AM" and abs(bdi_pct) > 5:
        regime_bias = "YEU_THAT" if abs(divergence_pct) > 1 else "LECH_PHA_AM"
    else:
        regime_bias = "CAN_BANG"

    structural_status = struct_state.get("trang_thai", "N/A")

    interpretation_parts = []
    if quality_label in ("THI_TRUONG_AO", "MEO_MANH"):
        interpretation_parts.append("Thi truong dang bi meo cau truc")
    elif quality_label == "MEO_NHE":
        interpretation_parts.append("Thi truong co dau hieu phan ky nhe")
    else:
        interpretation_parts.append("Thi truong phan anh dung mat bang chung")

    if bdi_level > 5:
        direction = "lon" if bdi_pct > 0 else "nho"
        interpretation_parts.append(f"Chi so bi nhom von hoa {direction} lam lech {bdi_level:.1f}%")

    if vg_weight > 15:
        interpretation_parts.append(f"Nhom Vingroup chiem {vg_weight:.0f}% gia tri giao dich")

    if concentration_level == "RAT_CAO":
        interpretation_parts.append("Dong tien tap trung cuc hep - ruid ro dao chieu cao")

    result = {
        "ngay": target_date,
        "chi_so_cong_bo": vnindex_pct,
        "chi_so_noi_tai": sbmi_pct,
        "do_lech_bdi": bdi_pct,
        "chenh_lech": {
            "pct": round(abs(vnindex_pct - sbmi_pct), 2),
            "status": chenh_lech_status,
        },
        "do_rong": {
            "ewmi_pct": ewmi_pct,
            "median_return": median_ret,
            "weighted_return": weighted_ret,
            "divergence_value_weighted": divergence_pct,
        },
        "tap_trung": {
            "lcr_pct": lcr,
            "level": concentration_level,
            "top_symbols": top_n,
        },
        "nhom_vingroup": {
            "weight_pct": vg_weight,
            "value_share_pct": vg_value_share,
            "symbols": vg_symbols,
        },
        "do_lech_index_return": {
            "idx_pct": idx_ret,
            "median_pct": median_ret,
            "gap": round(idx_ret - median_ret, 2),
        },
        "thuc_trang_cau_truc": structural_status,
        "diem_thi_truong_that": market_quality,
        "nhan_dien": quality_label,
        "do_lech_pha": regime_bias,
        "dien_giai": "; ".join(interpretation_parts),
    }

    out_path = Path(src.config.DATA_DIR) / "output" / "index_reality.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result


def in_bao_cao(kq: dict):
    vnindex = kq.get("chi_so_cong_bo", 0)
    sbmi = kq.get("chi_so_noi_tai", 0)
    bdi = kq.get("do_lech_bdi", 0)
    gap = vnindex - sbmi
    chenh = kq.get("chenh_lech", {})
    dr = kq.get("do_rong", {})
    tc = kq.get("tap_trung", {})
    vg = kq.get("nhom_vingroup", {})
    dr_idx = kq.get("do_lech_index_return", {})
    quality = kq.get("diem_thi_truong_that", 0)
    label = kq.get("nhan_dien", "")
    struct = kq.get("thuc_trang_cau_truc", "N/A")

    label_map = {
        "THI_TRUONG_THAT": "XANH - Thi truong that",
        "MEO_NHE": "VANG - Meo nhe",
        "MEO_MANH": "CAM - Meo manh",
        "THI_TRUONG_AO": "DO - Thi truong ao",
    }
    label_vn = label_map.get(label, label)

    print("\n" + "=" * 65)
    print("  PHAN TICH CHI SO THI TRUONG THONG NHAT")
    print("=" * 65)
    print(f"  Ngay: {kq.get('ngay', 'N/A')}")
    print()
    print(f"  Chi so cong bo (VN-Index): {vnindex:+.2f}%")
    print(f"  Thi truong that (ex-top):   {sbmi:+.2f}%")
    print(f"  Do lech BDI:                {bdi:+.2f}%")
    print(f"  Chenh lech chi so:          {chenh.get('pct', 0):.1f}% ({chenh.get('status', 'N/A')})")
    print(f"  Thuc trang cau truc:        {struct}")
    print()
    print(f"  --- Do rong ---")
    print(f"  EWMI (binh quan):           {dr.get('ewmi_pct', 0):+.2f}%")
    print(f"  Weighted return:            {dr.get('weighted_return', 0):+.2f}%")
    print(f"  Median return:              {dr.get('median_return', 0):+.2f}%")
    print(f"  Divergence (W - M):         {dr.get('divergence_value_weighted', 0):+.2f}%")
    print()
    print(f"  --- Tap trung ---")
    print(f"  LCR:                        {tc.get('lcr_pct', 0):.1f}% ({tc.get('level', 'N/A')})")
    if vg.get("symbols"):
        print(f"  Nhom Vingroup:               {vg.get('weight_pct', 0):.1f}% GTGD ({', '.join(vg['symbols'])})")
    print()
    print(f"  --- Do lech Index vs Median ---")
    print(f"  VN-Index hom nay:           {dr_idx.get('idx_pct', 0):+.2f}%")
    print(f"  Co phieu TB (median):       {dr_idx.get('median_pct', 0):+.2f}%")
    print(f"  Khoang cach:                {dr_idx.get('gap', 0):+.2f}%")
    print()
    status_icon = "XANH" if quality >= 0.8 else ("VANG" if quality >= 0.5 else ("CAM" if quality >= 0.2 else "DO"))
    print(f"  DIEM THI TRUONG THAT: {quality:.2f}/1.00 ({label_vn})")
    print(f"  Bien pha: {kq.get('do_lech_pha', 'N/A')}")
    print()
    print(f"  DIEN GIAI: {kq.get('dien_giai', '')}")
    print("=" * 65)


if __name__ == "__main__":
    kq = phan_tich_chi_so()
    in_bao_cao(kq)
