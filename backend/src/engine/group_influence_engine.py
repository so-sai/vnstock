"""
Group Influence Engine — Bộ đo ảnh hưởng nhóm trụ

Tính toán ảnh hưởng của từng nhóm cổ phiếu lên VNINDEX:
- Điểm ảnh hưởng chỉ số
- Thị trường thật (không nhóm trụ)
- Phân tích cụm vốn hóa
"""
import sys, io, json, os
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict

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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config
import pandas as pd
import numpy as np
from src.database.db_core import get_connection

# ── Cụm cổ phiếu ──────────────────────────────────────────
GROUPS = {
    "VIN": {
        "symbols": ["VIC", "VHM", "VRE", "VPL", "VIF", "VNF", "VOS", "VGI"],
        "label_vi": "Cụm Vingroup",
    },
    "NGAN_HANG": {
        "symbols": ["VCB", "BID", "CTG", "MBB", "TCB", "VPB", "HDB", "ACB",
                     "STB", "SSB", "SHB", "LPB", "TPB", "MSB", "NVB", "VAB",
                     "BAB", "BVB", "PGB", "ABBank", "KLB", "OCB", "EIB", "SEA"],
        "label_vi": "Ngân hàng",
    },
    "CHUNG_KHOAN": {
        "symbols": ["SSI", "VND", "HCM", "VCI", "VCSC", "MBS", "SHS", "BSI",
                     "VIX", "WSS", "FTS", "PSI", "AGR", "BMS", "IVS", "TVS"],
        "label_vi": "Chứng khoán",
    },
    "DAU_KHI": {
        "symbols": ["PLX", "PVS", "PVD", "GAS", "PVT", "PVG", "PET", "PVC",
                     "BSR", "OIL", "POW"],
        "label_vi": "Dầu khí",
    },
    "VIEN_THONG": {
        "symbols": ["FPT", "VNM", "MSN", "MWG", "PNJ", "VRE", "SAB"],
        "label_vi": "Công nghệ - Tiêu dùng",
    },
    "BAT_DONG_SAN": {
        "symbols": ["VIC", "VHM", "VRE", "NVL", "PDR", "KDH", "DXG", "NLG",
                     "CEO", "CGV", "SCR", "VCG", "CII", "HBC"],
        "label_vi": "Bất động sản",
    },
}


@dataclass
class GroupInfluence:
    """Kết quả ảnh hưởng của một nhóm cổ phiếu."""
    group_name: str
    label_vi: str
    symbols: List[str]
    active_symbols: int
    total_market_cap_pct: float
    index_contribution_pts: float
    avg_change_pct: float
    breadth_contribution: float
    is_dominant: bool = False


@dataclass
class MarketReality:
    """Thị trường thật vs thị trường ảo."""
    vnindex_actual: float
    vnindex_ex_group: Dict[str, float]
    vnindex_ex_top10: float
    real_market_breadth: float
    artificial_market: bool
    total_change_pct: float
    dominant_contribution_pct: float
    dominant_group: str
    group_contributions: List[GroupInfluence]
    chi_tiet_top10: Dict[str, float] = field(default_factory=dict)


def _get_latest_data(target_date: str = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Lấy dữ liệu giá và chỉ số gần nhất."""
    with get_connection() as conn:
        if target_date is None:
            target_date = pd.read_sql(
                "SELECT MAX(date) as d FROM daily_ohlcv WHERE symbol='VNINDEX'",
                conn
            ).iloc[0]['d']
            if target_date is None:
                return pd.DataFrame(), pd.DataFrame()

        df_stocks = pd.read_sql(
            "SELECT d.symbol, d.close, d.volume, d.open, d.high, d.low "
            "FROM daily_ohlcv d "
            "INNER JOIN ("
            "  SELECT symbol, MAX(date) as max_date "
            "  FROM daily_ohlcv WHERE date <= ? "
            "  GROUP BY symbol"
            ") m ON d.symbol = m.symbol AND d.date = m.max_date "
            "WHERE d.symbol != 'VNINDEX' AND d.close > 0",
            conn, params=(target_date,)
        )

        df_prev = pd.read_sql(
            "SELECT d.symbol, d.close as close_prev "
            "FROM daily_ohlcv d "
            "INNER JOIN ("
            "  SELECT symbol, MAX(date) as max_date "
            "  FROM daily_ohlcv WHERE date < ? "
            "  GROUP BY symbol"
            ") m ON d.symbol = m.symbol AND d.date = m.max_date "
            "WHERE d.symbol != 'VNINDEX' AND d.close > 0",
            conn, params=(target_date,)
        )

        df_idx = pd.read_sql(
            "SELECT close FROM daily_ohlcv "
            "WHERE symbol='VNINDEX' AND date=?",
            conn, params=(target_date,)
        )

    if df_stocks.empty or df_idx.empty:
        return pd.DataFrame(), pd.DataFrame()

    vnindex = float(df_idx.iloc[0]['close'])

    merged = df_stocks.merge(df_prev, on='symbol', how='left').copy()
    merged.loc[:, 'change_pct'] = np.where(
        merged['close_prev'] > 0,
        (merged['close'] - merged['close_prev']) / merged['close_prev'] * 100,
        0
    )
    merged.loc[:, 'change_pct'] = merged['change_pct'].clip(-10, 10)

    # Estimate market cap weight (using close * volume as proxy)
    merged.loc[:, 'weight'] = merged['close'] * merged['volume']
    total_weight = merged['weight'].sum()
    if total_weight > 0:
        merged.loc[:, 'weight_pct'] = merged['weight'] / total_weight * 100
    else:
        merged.loc[:, 'weight_pct'] = 1.0 / len(merged)

    return merged, pd.DataFrame({'vnindex': [vnindex]})


def _get_industry_map() -> Dict[str, str]:
    """Lấy map symbol -> icb_name2 từ DB."""
    with get_connection() as conn:
        df = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
    return dict(zip(df['symbol'], df['icb_name2']))


def tinh_anh_huong_nhom(target_date: str = None) -> MarketReality:
    """
    Tính ảnh hưởng của từng nhóm cổ phiếu lên VNINDEX.

    Returns:
        MarketReality với đầy đủ thông tin ảnh hưởng nhóm trụ
    """
    merged, idx_df = _get_latest_data(target_date)
    if merged.empty or idx_df.empty:
        return MarketReality(
            vnindex_actual=0, vnindex_ex_group={}, vnindex_ex_top10=0,
            real_market_breadth=0, artificial_market=False,
            total_change_pct=0, dominant_contribution_pct=0,
            dominant_group="UNKNOWN", group_contributions=[],
            chi_tiet_top10={}
        )

    vnindex = float(idx_df.iloc[0]['vnindex'])

    # Tính breadth thực tế
    up = (merged['change_pct'] > 0.5).sum()
    down = (merged['change_pct'] < -0.5).sum()
    total = len(merged)
    breadth = (up - down) / total * 100 if total > 0 else 0

    # Tính ảnh hưởng từng nhóm
    contributions = []
    vnindex_ex_group = {}

    for group_name, group_def in GROUPS.items():
        group_symbols = set(group_def["symbols"]) & set(merged['symbol'])
        if not group_symbols:
            continue

        group_data = merged[merged['symbol'].isin(group_symbols)]
        other_data = merged[~merged['symbol'].isin(group_symbols)]

        # Điểm ảnh hưởng = weight * change_pct của nhóm
        group_weight = group_data['weight_pct'].sum()
        group_avg_change = group_data['change_pct'].mean()
        group_contribution = group_weight * group_avg_change / 100

        # VNINDEX nếu không có nhóm này
        if group_weight > 0 and len(other_data) > 0:
            # Giả lập: bỏ nhóm này, giữ nguyên weight phần còn lại
            other_weight_sum = other_data['weight_pct'].sum()
            if other_weight_sum > 0:
                other_avg_change = other_data['change_pct'].mean()
                # Công thức: new_index = old_index * (1 - group_contribution/100)
                vnindex_ex = vnindex * (1 - group_contribution / 100)
            else:
                vnindex_ex = vnindex
        else:
            vnindex_ex = vnindex

        vnindex_ex_group[group_name] = round(vnindex_ex, 2)

        is_dominant = abs(group_contribution) > 0.5  # > 0.5% đóng góp vào biến động chỉ số

        contributions.append(GroupInfluence(
            group_name=group_name,
            label_vi=group_def["label_vi"],
            symbols=list(group_symbols),
            active_symbols=len(group_symbols),
            total_market_cap_pct=round(group_weight, 2),
            index_contribution_pts=round(group_contribution, 2),
            avg_change_pct=round(group_avg_change, 2),
            breadth_contribution=round(
                (group_data['change_pct'] > 0.5).sum() / len(group_data) * 100
                if len(group_data) > 0 else 0, 2
            ),
            is_dominant=is_dominant,
        ))

    # Sắp xếp theo ảnh hưởng giảm dần
    contributions.sort(key=lambda x: abs(x.index_contribution_pts), reverse=True)

    # Xác định nhóm主导
    dominant = contributions[0].group_name if contributions else "UNKNOWN"

    # Tính VNINDEX ex-large (bỏ top 10 vốn hóa)
    # Công thức đúng: contribution = Σ(weight_pct * change_pct) / 100
    # vnindex_ex = vnindex * (1 - contribution / 100)
    top10 = merged.nlargest(10, 'weight')
    top10_symbols = set(top10['symbol'])
    remaining = merged[~merged['symbol'].isin(top10_symbols)]
    if len(top10) > 0:
        top10_contribution = (top10['weight_pct'] * top10['change_pct']).sum() / 100
        vnindex_ex_large = vnindex * (1 - top10_contribution / 100)
    else:
        top10_contribution = 0
        vnindex_ex_large = vnindex

    chi_tiet = {}
    for _, row in top10.iterrows():
        sym = row['symbol']
        contribution = (row['weight_pct'] * row['change_pct']) / 100
        chi_tiet[sym] = round(contribution, 2)

    # Tính thị trường ảo: chỉ số xanh nhưng đa số cổ phiếu đỏ
    # Dấu hiệu: VNINDEX tăng nhưng breadth âm = vài cổ phiếu trụ kéo chỉ số
    total_change_pct = (merged['weight_pct'] * merged['change_pct']).sum() / 100
    top_contrib = contributions[0].index_contribution_pts if contributions else 0
    artificial = (
        total_change_pct > 0 and          # chỉ số xanh
        breadth < -5 and                   # đa số cổ phiếu đỏ
        top_contrib > 0 and               # nhóm trụ đang kéo lên
        top_contrib / total_change_pct > 0.5 if total_change_pct != 0 else False  # >50% từ 1 nhóm
    )

    return MarketReality(
        vnindex_actual=vnindex,
        vnindex_ex_group=vnindex_ex_group,
        vnindex_ex_top10=round(vnindex_ex_large, 2),
        real_market_breadth=round(breadth, 2),
        artificial_market=artificial,
        total_change_pct=round(total_change_pct, 2),
        dominant_contribution_pct=round(
            top_contrib / total_change_pct * 100 if total_change_pct != 0 else 0, 2
        ),
        dominant_group=dominant,
        group_contributions=contributions,
        chi_tiet_top10=chi_tiet,
    )


def in_bao_cao(mr: MarketReality) -> None:
    """In báo cáo ảnh hưởng nhóm trụ."""
    print()
    print("=" * 70)
    print("  BO DO ANH HUONG NHOM TRU")
    print("=" * 70)
    print()
    print("  VNINDEX hien tai: {:,.2f}".format(mr.vnindex_actual))
    print("  Bien dong VNINDEX uoc tinh: {:+.2f}%".format(mr.total_change_pct))
    print("  Breadth thi truong: {:+.2f}%".format(mr.real_market_breadth))
    print("  Thi truong ao: {}".format("CO" if mr.artificial_market else "KHONG"))
    print()

    # Bảng đóng góp
    print("  {:<20} {:>10} {:>12} {:>10} {:>10}".format(
        "Nhom", "Von hoa %", "Anh huong", "Thay doi %", "Breadth %"
    ))
    print("  " + "-" * 65)

    for g in mr.group_contributions:
        marker = " *" if g.is_dominant else ""
        print("  {:<20} {:>9.1f}% {:>+11.2f} {:>+9.2f}% {:>9.2f}%{}".format(
            g.label_vi, g.total_market_cap_pct,
            g.index_contribution_pts, g.avg_change_pct,
            g.breadth_contribution, marker
        ))

    print("  " + "-" * 65)
    print("  {} = Nhom dan dat (>0.5% dong gop vao bien dong chi so)".format("*"))

    # VNINDEX khong co nhom
    print()
    print("  VNINDEX neu khong co nhom:")
    print("  {:<25} {:>12}".format("Nhom", "VNINDEX"))
    print("  " + "-" * 40)
    for g in mr.group_contributions:
        ex_val = mr.vnindex_ex_group.get(g.group_name, mr.vnindex_actual)
        diff = ex_val - mr.vnindex_actual
        print("  Khong co {:<15} {:>10.2f} ({:+.2f})".format(
            g.label_vi, ex_val, diff
        ))

    print("  " + "-" * 40)
    print("  VNINDEX ex-top10 (chi so noi tai): {:>10.2f}".format(mr.vnindex_ex_top10))

    print()
    print("  Quy tac doc:")
    print("  - 'Anh huong' > 0.5: Nhom co anh huong dang ke len bien dong VNINDEX hom nay")
    print("  - 'Thi truong ao': Chi so xanh (+%) nhung da so co phieu do (breadth < -5%)")
    print("    va >50% bien dong den tu 1 nhom duy nhat = nhom tru keo gia tao")
    print("  - 'VNINDEX ex-top10': Uoc tinh VNINDEX neu bo 10 co phieu von hoa lon nhat")
    print()
    print("=" * 70)


def xuat_json(mr: MarketReality, duong_dan: str = None) -> dict:
    """Xuất báo cáo ra JSON."""
    if duong_dan is None:
        duong_dan = os.path.join(src.config.DATA_DIR, "group_influence_report.json")

    report = {
        "vnindex_actual": mr.vnindex_actual,
        "total_change_pct": mr.total_change_pct,
        "dominant_contribution_pct": mr.dominant_contribution_pct,
        "real_market_breadth": mr.real_market_breadth,
        "artificial_market": mr.artificial_market,
        "dominant_group": mr.dominant_group,
        "vnindex_ex_top10": mr.vnindex_ex_top10,
        "chi_tiet_top10": mr.chi_tiet_top10,
        "group_contributions": [asdict(g) for g in mr.group_contributions],
        "vnindex_ex_group": mr.vnindex_ex_group,
    }

    with open(duong_dan, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print("  Bao cao da luu: {}".format(duong_dan))
    return report


if __name__ == "__main__":
    mr = tinh_anh_huong_nhom()
    in_bao_cao(mr)
    xuat_json(mr)
