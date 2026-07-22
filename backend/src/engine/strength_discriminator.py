"""
StrengthDiscriminator — Tầng Phân Tách Xung Lực
================================================
Bóc tách bản chất tăng giá của Top 20 RS:
- Sức mạnh Nội tại (Intrinsic): do nội lực doanh nghiệp, dòng tiền hữu cơ
- Sức mạnh Ép trụ (Pillar-driven): do dòng tiền điều tiết chỉ số
"""
import json
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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config


def _tai_top_rs(top_n: int = 20) -> List[dict]:
    """Đọc Top N RS từ market_rs.json."""
    rs_path = Path(src.config.DATA_DIR) / "market_rs.json"
    if not rs_path.exists():
        print("  [FAIL] Không tìm thấy market_rs.json. Chạy rs_ranker.py trước.")
        return []
    with open(rs_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not data:
        return []
    # LAW-DATA-001: Loại bỏ mã chỉ số rổ
    index_symbols = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPINDEX"}
    data = [d for d in data if d.get('symbol', '') not in index_symbols]
    data.sort(key=lambda x: x.get('rs_rating', 0), reverse=True)
    return data[:top_n]


def phân_tách_xung_lực(top_rs_data: List[dict], chi_tiet_top10: Dict[str, float]) -> List[dict]:
    """
    Thực thi phân tách xung lực cho từng mã trong top RS.

    Returns:
        Danh sách dict với các key:
        - ma_cp, rs_tho, rs_noi_tai, gt_giao_dich_ty, diem_gánh_chi_so,
          ban_chat, nhan_dien_chien_thuat
    """
    results: List[dict] = []

    for stock in top_rs_data:
        symbol = stock.get('symbol', '')
        rs_raw = stock.get('rs_rating', 0)
        avg_value = stock.get('avg_value_20d', 0.0)

        contribution_pts = chi_tiet_top10.get(symbol, 0.0)

        if abs(contribution_pts) > 1.5:
            dependence_tier = "CAO_DÙNG_ĐỂ_ĐIỀU_TIẾT"
            penalty = min(0.15, abs(contribution_pts) / 10.0)
            intrinsic_rs = max(0.0, round(rs_raw * (1.0 - penalty), 1))
        else:
            dependence_tier = "HỮU_CƠ_TỰ_NHIÊN"
            intrinsic_rs = float(rs_raw)

        if intrinsic_rs >= 90 and dependence_tier == "HỮU_CƠ_TỰ_NHIÊN":
            nature = "SIÊU_CHIẾN_BINH_HỮU_CƠ"
        elif rs_raw >= 95 and dependence_tier == "CAO_DÙNG_ĐỂ_ĐIỀU_TIẾT":
            nature = "VŨ_KHÍ_ĐIỀU_TIẾT_CHỈ_SỐ"
        else:
            nature = "THEO_DÕI_DÒNG_TIỀN"

        results.append({
            "ma_cp": symbol,
            "rs_tho": rs_raw,
            "rs_noi_tai": intrinsic_rs,
            "gt_giao_dich_ty": round(avg_value, 1),
            "diem_gánh_chi_so": round(contribution_pts, 2),
            "ban_chat": dependence_tier,
            "nhan_dien_chien_thuat": nature,
        })

    results.sort(key=lambda x: x['rs_noi_tai'], reverse=True)
    return results


def in_bao_cao(kq: List[dict]) -> None:
    """Xuất bản báo cáo phân tách xung lực."""
    print()
    print("=" * 100)
    print("  TANG PHAN TACH XUNG LUC NOI TAI — TOP {} RS KHOI LUONG THEP".format(len(kq)))
    print("=" * 100)
    print("  {:<6} {:>6} {:>10} {:>12} {:>12}  {:<22} {}".format(
        "Ma", "RS Tho", "RS Noi Tai", "GT GD (ty)", "Diem Ganh",
        "Ban Chat", "Nhan Dien Chien Thuat"
    ))
    print("  " + "-" * 98)
    for item in kq:
        print("  {:<6} {:>6} {:>10.1f} {:>12.1f} {:>12.2f}  {:<22} {}".format(
            item['ma_cp'],
            item['rs_tho'],
            item['rs_noi_tai'],
            item['gt_giao_dich_ty'],
            item['diem_gánh_chi_so'],
            item['ban_chat'],
            item['nhan_dien_chien_thuat'],
        ))
    print("=" * 100)
    print()

    # Phân tích tổng quan
    organic = [i for i in kq if i['ban_chat'] == 'HỮU_CƠ_TỰ_NHIÊN']
    pillar = [i for i in kq if i['ban_chat'] == 'CAO_DÙNG_ĐỂ_ĐIỀU_TIẾT']
    super_solid = [i for i in organic if i['rs_noi_tai'] >= 90]

    print("  PHAN TICH TONG QUAN:")
    print(f"    - Tổng số mã: {len(kq)}")
    print(f"    - Sức mạnh hữu cơ thực chất: {len(organic)} mã")
    if super_solid:
        print(f"    - Siêu chiến binh hữu cơ (RS nội tại >= 90): {', '.join(i['ma_cp'] for i in super_solid)}")
    if pillar:
        print(f"    - Vũ khí điều tiết chỉ số: {len(pillar)} mã")
        for p in pillar:
            print(f"      * {p['ma_cp']}: RS thô {p['rs_tho']} → RS nội tại {p['rs_noi_tai']} "
                  f"(gánh {p['diem_gánh_chi_so']} điểm chỉ số)")
    print()


def cmd_phân_tách(top_n: int = 20) -> None:
    """Entrypoint: load dữ liệu, phân tách, in báo cáo."""
    from src.engine.group_influence_engine import tinh_anh_huong_nhom

    top_rs = _tai_top_rs(top_n)
    if not top_rs:
        print("  [FAIL] Không có dữ liệu RS.")
        return

    mr = tinh_anh_huong_nhom()
    kq = phân_tách_xung_lực(top_rs, mr.chi_tiet_top10)
    in_bao_cao(kq)


if __name__ == "__main__":
    cmd_phân_tách()
