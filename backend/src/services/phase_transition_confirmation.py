import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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


_KET_LUAN_MAP = {
    "NHIỄU": {
        "ten": "Nhiễu",
        "ky_hieu": "🔍",
        "mo_ta": "Cảnh báo chưa đủ cơ sở, có thể là nhiễu thị trường",
    },
    "ĐANG_CHUYỂN_PHA": {
        "ten": "Đang chuyển pha",
        "ky_hieu": "🟠",
        "mo_ta": "Đủ dấu hiệu cho thấy thị trường đang chuyển pha",
    },
    "CHUYỂN_PHA_THẬT": {
        "ten": "Chuyển pha thật",
        "ky_hieu": "🔴",
        "mo_ta": "Xác nhận thị trường đã thay đổi trạng thái thực sự",
    },
}


def xac_nhan_chuyen_pha(regime_data: Optional[dict] = None,
                         market_state: Optional[dict] = None,
                         gold_premium_pct: Optional[float] = None,
                         gold_premium_regime: Optional[str] = None) -> dict:
    nhom_dat = 0
    nhom_ket_qua = {}

    # =============================================
    # NHÓM 1: GIÁ VÀ XU HƯỚNG
    # =============================================
    diem_gia = 0
    ly_do_gia = []
    regime_status = (regime_data or {}).get("status", "UNKNOWN")
    regime_score = (regime_data or {}).get("regime_score", 0.0)
    rad = (regime_data or {}).get("rad", {})
    rad_activated = rad.get("activated", False)
    details = (regime_data or {}).get("details", {})
    delta_adx = rad.get("signals", {}).get("delta_adx")
    adx = details.get("adx")
    vnindex_vs_ma200 = details.get("vnindex_vs_ma200", "UNKNOWN")

    crisis_like = regime_status in ("CRISIS", "CRISIS_WARNING")
    if crisis_like:
        diem_gia += 2
        ly_do_gia.append("Thị trường đang ở trạng thái xấu")

    if regime_score is not None and regime_score < 0.35:
        diem_gia += 1
        ly_do_gia.append(f"Điểm thị trường xuống thấp ({regime_score:.2f})")

    if rad_activated:
        diem_gia += 2
        ly_do_gia.append("RAD phát hiện chuyển pha")

    if delta_adx is not None and delta_adx > 5:
        diem_gia += 1
        ly_do_gia.append(f"ADX tăng mạnh ({delta_adx:+.1f})")

    if adx is not None and adx > 30 and vnindex_vs_ma200 == "BELOW":
        diem_gia += 1
        ly_do_gia.append("Xu hướng mạnh nhưng dưới MA200")

    if vnindex_vs_ma200 == "BELOW":
        diem_gia += 1
        ly_do_gia.append("Chỉ số dưới trung bình 200 ngày")

    nhom_gia = diem_gia >= 2
    nhom_ket_qua["gia_va_xu_huong"] = {
        "dat": nhom_gia,
        "diem": diem_gia,
        "ly_do": ly_do_gia,
    }
    if nhom_gia:
        nhom_dat += 1

    # =============================================
    # NHÓM 2: DÒNG TIỀN
    # =============================================
    diem_tien = 0
    ly_do_tien = []
    flow_state = (market_state or {}).get("flow_state", {})
    breadth_state = (market_state or {}).get("breadth_state", {})
    risk_state = (market_state or {}).get("risk_state", {})

    flow_status = (flow_state.get("status") or "").upper()
    health_score = breadth_state.get("health_score")
    leading_sectors = flow_state.get("leading_sectors", [])
    governor = (risk_state.get("governor_state") or "").upper()

    if flow_status in ("THU_HẸP", "PHÂN_HÓA"):
        diem_tien += 2
        ly_do_tien.append("Dòng tiền đang co lại hoặc phân hóa")

    if health_score is not None and health_score < 35:
        diem_tien += 2
        ly_do_tien.append(f"Độ rộng thị trường xấu (sức khỏe: {health_score:.0f})")
    elif health_score is not None and health_score < 50:
        diem_tien += 1
        ly_do_tien.append(f"Độ rộng yếu ({health_score:.0f})")

    if flow_status in ("MỞ_RỘNG", "MỞ_RỘNG_TÍCH_CỰC") and len(leading_sectors) <= 2:
        diem_tien += 1
        ly_do_tien.append(f"Dòng tiền chỉ tập trung {len(leading_sectors)} nhóm")

    if governor == "LOCKDOWN":
        diem_tien += 2
        ly_do_tien.append("Bộ kiểm soát rủi ro đã khóa thị trường")

    nhom_tien = diem_tien >= 2
    nhom_ket_qua["dong_tien"] = {
        "dat": nhom_tien,
        "diem": diem_tien,
        "ly_do": ly_do_tien,
    }
    if nhom_tien:
        nhom_dat += 1

    # =============================================
    # NHÓM 3: HÀNH VI PHÒNG THỦ
    # =============================================
    diem_phong_thu = 0
    ly_do_phong_thu = []
    meta = (market_state or {}).get("meta_state", {})
    risk_appetite = (meta.get("risk_appetite") or "").upper()

    if gold_premium_pct is not None and gold_premium_pct > 5:
        diem_phong_thu += 2
        ly_do_phong_thu.append(f"Chênh lệch vàng ở mức cao ({gold_premium_pct:.1f}%)")
    elif gold_premium_pct is not None and gold_premium_pct > 3:
        diem_phong_thu += 1
        ly_do_phong_thu.append(f"Chênh lệch vàng hơi cao ({gold_premium_pct:.1f}%)")

    if gold_premium_regime:
        reg = gold_premium_regime.upper()
        if "SURGE" in reg:
            diem_phong_thu += 2
            ly_do_phong_thu.append("Vàng nội địa tăng nóng so với thế giới")
        elif "ELEVATED" in reg:
            diem_phong_thu += 1

    if "ĐÓNG" in risk_appetite or "THẬN_TRỌNG" in risk_appetite:
        diem_phong_thu += 1
        ly_do_phong_thu.append("Tâm lý thị trường chuyển sang phòng thủ")

    if flow_status in ("THU_HẸP", "PHÂN_HÓA"):
        diem_phong_thu += 1
        if not ly_do_phong_thu or "Tâm lý" not in " ".join(ly_do_phong_thu):
            ly_do_phong_thu.append("Dòng tiền rút khỏi rủi ro")

    nhom_phong_thu = diem_phong_thu >= 2
    nhom_ket_qua["hanh_vi_phong_thu"] = {
        "dat": nhom_phong_thu,
        "diem": diem_phong_thu,
        "ly_do": ly_do_phong_thu,
    }
    if nhom_phong_thu:
        nhom_dat += 1

    # =============================================
    # KẾT LUẬN
    # =============================================
    if nhom_dat >= 3:
        ket_luan = "CHUYỂN_PHA_THẬT"
    elif nhom_dat >= 2:
        ket_luan = "ĐANG_CHUYỂN_PHA"
    else:
        ket_luan = "NHIỄU"

    info = _KET_LUAN_MAP.get(ket_luan, _KET_LUAN_MAP["NHIỄU"])
    return {
        "ket_luan": ket_luan,
        "ten": info["ten"],
        "ky_hieu": info["ky_hieu"],
        "mo_ta": info["mo_ta"],
        "so_nhom_dat": nhom_dat,
        "chi_tiet": nhom_ket_qua,
    }
