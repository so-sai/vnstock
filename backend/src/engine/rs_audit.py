"""
RS Audit — Bộ phân tích nguồn gốc sức mạnh Top RS

PHÁC THẢO KIẾN TRÚC MODULE 4 TRỤ
==================================
(Chưa code — chỉ design. Phase C.)

┌─────────────────────────────────────────────────────────────┐
│              BỘ PHÂN TÍCH NGUỒN GỐC SỨC MẠNH               │
├───────────┬───────────┬──────────┬──────────────────────────┤
│  TRỤ 1    │  TRỤ 2    │  TRỤ 3   │  TRỤ 4                   │
│ Sức mạnh  │ Sức mạnh  │ Sức mạnh │ Sức mạnh                │
│ giá       │ thanh     │ ngành    │ nền tảng                 │
│           │ khoản     │          │ (future)                 │
├───────────┼───────────┼──────────┼──────────────────────────┤
│ RS (1-99) │ avg_vol   │ breadth  │ doanh thu                │
│ raw RS    │ avg_value │ ngành %  │ lợi nhuận                │
│ change_1y │ rvol      │ group    │ ROE                      │
│           │           │ influence│ biên lợi nhuận           │
├───────────┼───────────┼──────────┼──────────────────────────┤
│ ĐÃ CÓ     │ ĐÃ CÓ     │ ĐÃ CÓ    │ CHƯA CÓ DỮ LIỆU         │
│ (rs_ranker│ (rs_ranker│ (audit)  │ (cần nguồn fundamental)  │
│ .py)      │ .py)      │          │                          │
└───────────┴───────────┴──────────┴──────────────────────────┘

CÔNG THỨC TỔNG HỢP (dự kiến):
  điểm_xác_nhận = trụ_giá * 0.25 + trụ_thanh_khoản * 0.20
                 + trụ_ngành * 0.35 + trụ_nền_tảng * 0.20

  Nếu chưa có trụ 4:
  điểm_xác_nhận = (trụ_giá * 0.25 + trụ_thanh_khoản * 0.25
                   + trụ_ngành * 0.50) / 0.75

PHÂN LOẠI NGUỒN GỐC SỨC MẠNH:
  ĐƯỢC XÁC NHẬN         : điểm >= 0.6 + trụ ngành > 0.4
  DẪN DẮT ĐƠN ĐỘC        : điểm >= 0.4 + trụ ngành < 0.2 + thanh khoản > 0.5
  TẬP TRUNG VỐN HÓA      : điểm >= 0.4 + vốn hóa lớn + ngành hẹp
  CHƯA ĐỦ DỮ LIỆU        : thanh khoản thấp hoặc thiếu dữ liệu

Lưu ý:
  - Module này CHƯA được thêm vào pipeline quyết định.
  - Dùng để quan sát, chưa để ra quyết định.
  - Cần ít nhất 3 tháng dữ liệu để kiểm định giá trị dự báo.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List


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
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd

import src.config
from src.database.db_core import get_connection
from src.engine.universe import SECTOR_MAP

# ── Mapping ICB → tên ngành rút gọn ──────────────────────────
ICB_ALIAS = {
    "Ngân hàng": "BANK", "Chứng khoán": "SEC", "Bất động sản": "RE",
    "Dầu khí": "OIL", "Xây dựng & Vật liệu": "CONST", "Xây dựng": "CONST",
    "Công nghệ Thông tin": "TECH", "Công nghệ": "TECH",
    "Thực phẩm & Đồ uống": "FOOD", "Thực phẩm": "FOOD", "Đồ uống": "FOOD",
    "Du lịch & Giải trí": "TRAVEL", "Vận tải": "TRANS",
    "Bán lẻ": "RETAIL", "Bảo hiểm": "INSUR", "Hóa chất": "CHEM",
    "Tài nguyên Cơ bản": "RESOURCE", "Điện, nước & xăng dầu": "UTIL",
    "Hàng & Dịch vụ Công nghiệp": "INDUST", "Dịch vụ tài chính": "FIN_SVC",
    "Ô tô & Phụ tùng": "AUTO", "Y tế": "HEALTH", "Viễn thông": "TELCO",
    "Truyền thông": "MEDIA", "Hàng cá nhân & Gia dụng": "PERSONAL",
}

# ── Hệ số cho điểm xác nhận thị trường ────────────────────────
# (dự kiến cho module 4 trụ Phase C)
TRONG_SO = {
    "gia": 0.25,
    "thanh_khoan": 0.25,
    "nganh": 0.50,  # Cao hơn vì chưa có trụ nền tảng
}

NGUONG = {
    "xac_nhan": 0.60,
    "dan_dat": 0.40,
    "nganh_rong": 0.40,
    "nganh_hep": 0.20,
    "thanh_khoan_cao": 0.50,
}


def _load_rs_data() -> List[dict]:
    path = os.path.join(src.config.DATA_DIR, "market_rs.json")
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_group_influence() -> dict:
    path = os.path.join(src.config.DATA_DIR, "group_influence_report.json")
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_icb_sectors() -> Dict[str, str]:
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
        return dict(zip(df['symbol'], df['icb_name2']))
    except Exception:
        return {}


def _resolve_sector(symbol: str, icb_map: dict) -> str:
    sec = SECTOR_MAP.get(symbol)
    if sec:
        return sec
    icb = icb_map.get(symbol, "OTHER")
    for keyword, alias in ICB_ALIAS.items():
        if keyword in icb:
            return alias
    return icb[:12] if icb != "OTHER" else "OTHER"


def _compute_sector_breadth(icb_map: dict) -> Dict[str, float]:
    with get_connection() as conn:
        latest = pd.read_sql("SELECT MAX(date) as d FROM daily_ohlcv WHERE symbol='VNINDEX'", conn)
        if latest.empty or latest.iloc[0]['d'] is None:
            return {}
        target_date = latest.iloc[0]['d']

        prev = pd.read_sql(
            "SELECT symbol, close FROM daily_ohlcv WHERE date = (SELECT MAX(date) FROM daily_ohlcv WHERE date < ?)",
            conn, params=(target_date,)
        )
        curr = pd.read_sql(
            "SELECT symbol, close FROM daily_ohlcv WHERE date = ?",
            conn, params=(target_date,)
        )

    merged = curr.merge(prev, on='symbol', how='inner', suffixes=('', '_prev'))
    merged = merged.copy()
    merged.loc[:, 'change_pct'] = ((merged['close'] - merged['close_prev']) / merged['close_prev']) * 100
    merged.loc[:, 'nganh'] = merged['symbol'].map(lambda s: _resolve_sector(s, icb_map))

    results = {}
    for nganh, group in merged.groupby('nganh'):
        up_count = (group['change_pct'] > 0.5).sum()
        results[nganh] = round(up_count / len(group) * 100, 1)

    return results


def _is_in_group(symbol: str, group_data: dict) -> str | None:
    for g in group_data.get("group_contributions", []):
        if symbol in g.get("symbols", []):
            return f"{g['label_vi']} ({g['total_market_cap_pct']:.1f}%)"
    return None


def _liquidity_tier(avg_value_20d: float, avg_vol_20d: float) -> str:
    if avg_value_20d >= 500 or avg_vol_20d >= 10_000_000:
        return "RẤT_CAO"
    elif avg_value_20d >= 100 or avg_vol_20d >= 2_000_000:
        return "CAO"
    elif avg_value_20d >= 20 or avg_vol_20d >= 500_000:
        return "TB"
    else:
        return "THẤP"


def _score_tru_gia(rs: int) -> float:
    """Trụ 1: Sức mạnh giá → điểm 0-1."""
    return rs / 100.0


def _score_tru_thanh_khoan(avg_value: float, avg_vol: float) -> float:
    """Trụ 2: Sức mạnh thanh khoản → điểm 0-1."""
    tier = _liquidity_tier(avg_value, avg_vol)
    mapping = {"RẤT_CAO": 1.0, "CAO": 0.7, "TB": 0.35, "THẤP": 0.1}
    return mapping.get(tier, 0.0)


def _score_tru_nganh(sector_breadth: float) -> float:
    """Trụ 3: Sức mạnh ngành → điểm 0-1."""
    if sector_breadth >= 50:
        return 1.0
    elif sector_breadth >= 40:
        return 0.8
    elif sector_breadth >= 30:
        return 0.6
    elif sector_breadth >= 20:
        return 0.4
    elif sector_breadth >= 10:
        return 0.2
    else:
        return 0.05


def _diem_xac_nhan(tru_gia: float, tru_thanh_khoan: float, tru_nganh: float) -> float:
    """Điểm xác nhận thị trường tổng hợp (0-1)."""
    raw = (tru_gia * TRONG_SO["gia"]
           + tru_thanh_khoan * TRONG_SO["thanh_khoan"]
           + tru_nganh * TRONG_SO["nganh"])
    return round(raw / sum(TRONG_SO.values()), 2)


def _phan_loai(tru_gia: float, tru_thanh_khoan: float, tru_nganh: float,
               diem: float, group_tag: str | None, is_large_cap: bool) -> str:
    """Phân loại nguồn gốc sức mạnh."""
    if tru_thanh_khoan < 0.2:
        return "CHƯA ĐỦ DỮ LIỆU"

    if diem >= NGUONG["xac_nhan"] and tru_nganh >= NGUONG["nganh_rong"]:
        return "ĐƯỢC XÁC NHẬN"

    if group_tag and tru_nganh < NGUONG["nganh_hep"] and tru_thanh_khoan >= NGUONG["thanh_khoan_cao"]:
        return "DẪN DẮT ĐƠN ĐỘC"

    if diem >= NGUONG["dan_dat"] and tru_thanh_khoan >= NGUONG["thanh_khoan_cao"]:
        if group_tag:
            return "DẪN DẮT ĐƠN ĐỘC"
        if tru_nganh < NGUONG["nganh_hep"]:
            return "DẪN DẮT ĐƠN ĐỘC"

    if is_large_cap and tru_nganh < NGUONG["nganh_hep"]:
        return "TẬP TRUNG VỐN HÓA"

    return "CHƯA ĐỦ DỮ LIỆU"


def run_rs_audit(top_n: int = 20) -> List[dict]:
    rs_data = _load_rs_data()
    if not rs_data:
        print("  Chưa có dữ liệu RS. Chạy 'python ptck.py scan' trước.")
        return []

    group_data = _load_group_influence()
    icb_map = _load_icb_sectors()
    sector_breadth = _compute_sector_breadth(icb_map)

    symbols_seen = set()
    results = []
    for item in rs_data:
        sym = item.get("symbol", "")
        price = item.get("price", 0)
        if sym == "VNINDEX" or price <= 0 or sym in symbols_seen:
            continue
        symbols_seen.add(sym)

        rs_rating = int(item.get("rs_rating", 0))
        avg_vol = item.get("avg_vol_20d", 0)
        avg_value = item.get("avg_value_20d", 0)
        change_1y = item.get("change_1y", 0)
        rvol = item.get("rvol", 0)

        sector = _resolve_sector(sym, icb_map)
        sec_breadth = sector_breadth.get(sector, 0)
        liq = _liquidity_tier(avg_value, avg_vol)
        group_tag = _is_in_group(sym, group_data)

        tru_gia = _score_tru_gia(rs_rating)
        tru_thanh_khoan = _score_tru_thanh_khoan(avg_value, avg_vol)
        tru_nganh = _score_tru_nganh(sec_breadth)
        diem = _diem_xac_nhan(tru_gia, tru_thanh_khoan, tru_nganh)

        is_large_cap = (price * avg_vol * 1_000_000) > 10_000_000_000_000
        ket_luan = _phan_loai(tru_gia, tru_thanh_khoan, tru_nganh, diem, group_tag, is_large_cap)

        results.append({
            "symbol": sym,
            "rs_rating": rs_rating,
            "price": price,
            "avg_vol_20d": int(avg_vol),
            "avg_value_20d_bn": round(avg_value, 2),
            "change_1y_pct": round(change_1y, 1),
            "rvol": rvol,
            "sector": sector,
            "sector_breadth_pct": sec_breadth,
            "liquidity": liq,
            "group_influence": group_tag or "—",
            "diem_xac_nhan": diem,
            "ket_luan": ket_luan,
        })

        if len(results) >= top_n:
            break

    return results


def in_bao_cao(results: List[dict]):
    if not results:
        print("  Không có dữ liệu.")
        return

    print()
    print("=" * 140)
    print("  BỘ PHÂN TÍCH NGUỒN GỐC SỨC MẠNH — RS AUDIT")
    print("=" * 140)

    header = (
        f"  {'#':<3} {'Mã':<6} {'RS':>3} {'Giá':>7} "
        f"{'Vol TB':>9} {'GT TB':>7} {'%1Y':>7} "
        f"{'Ngành':<10} {'Breadth':>7} {'TK':<8} {'Xác nhận':>8} "
        f"{'Nhóm ảnh hưởng':<22} {'Kết luận':<22}"
    )
    print(header)
    print("  " + "-" * 136)

    colors = {
        "ĐƯỢC XÁC NHẬN": "\033[92m",
        "DẪN DẮT ĐƠN ĐỘC": "\033[93m",
        "TẬP TRUNG VỐN HÓA": "\033[91m",
        "CHƯA ĐỦ DỮ LIỆU": "\033[90m",
    }
    reset = "\033[0m"

    for i, r in enumerate(results, 1):
        color = colors.get(r["ket_luan"], "")
        vol_str = f"{r['avg_vol_20d']/1000:.0f}K" if r['avg_vol_20d'] < 1_000_000 else f"{r['avg_vol_20d']/1_000_000:.1f}M"
        kl = r["ket_luan"]
        line = (
            f"  {i:<3} {r['symbol']:<6} {r['rs_rating']:>3} {r['price']:>7.1f} "
            f"{vol_str:>9} {r['avg_value_20d_bn']:>7.2f} {r['change_1y_pct']:>+6.1f} "
            f"{r['sector']:<10} {r['sector_breadth_pct']:>5.1f}% {r['liquidity']:<8} "
            f"{r['diem_xac_nhan']:>7.2f} "
            f"{r['group_influence']:<22} {color}{kl:<22}{reset}"
        )
        print(line)

    print("  " + "-" * 136)
    print()

    dem = {}
    for r in results:
        dem[r["ket_luan"]] = dem.get(r["ket_luan"], 0) + 1

    print("  Phân bố kết luận:")
    for kl, count in sorted(dem.items(), key=lambda x: -x[1]):
        print(f"    {kl:<22}: {count} mã")
    print(f"    {'TỔNG':<22}: {len(results)} mã")

    print()
    xac_nhan = dem.get("ĐƯỢC XÁC NHẬN", 0)
    doc_dao = dem.get("DẪN DẮT ĐƠN ĐỘC", 0)
    if xac_nhan <= 2:
        print(f"  ⚠ Rất ít mã RS cao được thị trường xác nhận ({xac_nhan}/{len(results)})")
    if doc_dao >= 5:
        print(f"  ⚠ Đa số RS cao do dẫn dắt đơn độc ({doc_dao}/{len(results)})")
    print()
    print("  Chú thích:")
    print("    ĐƯỢC XÁC NHẬN        : RS cao + ngành rộng + thanh khoản tốt")
    print("    DẪN DẮT ĐƠN ĐỘC       : RS cao + thanh khoản tốt + ngành hẹp")
    print("    TẬP TRUNG VỐN HÓA     : RS cao + vốn hóa lớn + ngành không xác nhận")
    print("    CHƯA ĐỦ DỮ LIỆU       : Thanh khoản thấp hoặc thiếu dữ liệu ngành")
    print("=" * 140)
