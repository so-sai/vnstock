import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
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


def _lay_du_lieu_lich_su(so_ngay=10):
    from src.database.timeline_manager import get_regime_history

    try:
        df = get_regime_history(limit=so_ngay + 5)
        if df.empty:
            return pd.DataFrame()
        return df.sort_values("date")
    except sqlite3.Error, TypeError, ValueError, AttributeError, KeyError, IndexError:
        return pd.DataFrame()


def _toc_do_thay_doi_adx(delta_adx) -> dict:
    if delta_adx is None:
        return {"cap_do": "KHÔNG_RÕ", "diem": 0}
    gia_tri = abs(delta_adx)
    if gia_tri > 10:
        return {"cap_do": "CAO", "diem": 3, "mo_ta": f"ADX đang thay đổi rất nhanh ({delta_adx:+.1f})"}
    if gia_tri > 5:
        return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": f"ADX đang thay đổi ({delta_adx:+.1f})"}
    if gia_tri > 2:
        return {"cap_do": "THẤP", "diem": 1, "mo_ta": f"ADX thay đổi nhẹ ({delta_adx:+.1f})"}
    return {"cap_do": "ỔN_ĐỊNH", "diem": 0, "mo_ta": "ADX ổn định"}


def _toc_do_thay_doi_do_rong(lich_su, breadth_pct_hien_tai) -> dict:
    if lich_su.empty or breadth_pct_hien_tai is None:
        return {"cap_do": "KHÔNG_RÕ", "diem": 0}
    try:
        cot = "breadth_pct"
        if cot not in lich_su.columns:
            return {"cap_do": "KHÔNG_RÕ", "diem": 0}
        gia_tri = lich_su[cot].dropna().tolist()
        if len(gia_tri) >= 3:
            toc_do_3ngay = breadth_pct_hien_tai - gia_tri[-1]
            toc_do_5ngay = breadth_pct_hien_tai - gia_tri[0] if len(gia_tri) >= 5 else toc_do_3ngay
            if toc_do_3ngay < -10:
                return {"cap_do": "CAO", "diem": 3, "mo_ta": f"Độ rộng đang co hẹp rất nhanh (3 ngày: {toc_do_3ngay:+.1f}%)"}
            if toc_do_5ngay < -15:
                return {"cap_do": "CAO", "diem": 3, "mo_ta": f"Độ rộng co hẹp mạnh trong 5 ngày ({toc_do_5ngay:+.1f}%)"}
            if toc_do_3ngay < -5:
                return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": f"Độ rộng đang giảm ({toc_do_3ngay:+.1f}%)"}
            if toc_do_3ngay > 10:
                return {"cap_do": "CAO", "diem": 2, "mo_ta": f"Độ rộng đang mở rộng nhanh ({toc_do_3ngay:+.1f}%)"}
            return {"cap_do": "ỔN_ĐỊNH", "diem": 0, "mo_ta": "Độ rộng ổn định"}
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as _e:
        logger.debug("Tính tốc độ thay đổi độ rộng thất bại (bỏ qua): %s", _e)
    return {"cap_do": "KHÔNG_RÕ", "diem": 0}


def _toc_do_thay_doi_diem_regime(lich_su, regime_score_hien_tai) -> dict:
    if lich_su.empty or regime_score_hien_tai is None:
        return {"cap_do": "KHÔNG_RÕ", "diem": 0}
    try:
        cot = "regime_score"
        if cot not in lich_su.columns:
            return {"cap_do": "KHÔNG_RÕ", "diem": 0}
        gia_tri = lich_su[cot].dropna().tolist()
        if len(gia_tri) >= 3:
            delta_3 = regime_score_hien_tai - gia_tri[-1]
            regime_score_hien_tai - gia_tri[0] if len(gia_tri) >= 5 else delta_3
            if abs(delta_3) > 0.2:
                return {"cap_do": "CAO", "diem": 3, "mo_ta": f"Điểm thị trường đang thay đổi mạnh ({delta_3:+.2f})"}
            if abs(delta_3) > 0.1:
                return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": f"Điểm thị trường thay đổi ({delta_3:+.2f})"}
            return {"cap_do": "ỔN_ĐỊNH", "diem": 0}
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as _e:
        logger.debug("Tính tốc độ thay đổi điểm regime thất bại (bỏ qua): %s", _e)
    return {"cap_do": "KHÔNG_RÕ", "diem": 0}


def _canh_bao_chuyen_pha(rad_activated, regime_status, lich_su) -> dict:
    if rad_activated:
        return {"cap_do": "CAO", "diem": 4, "mo_ta": "Hệ thống RAD phát hiện thị trường đang chuyển trạng thái"}
    trang_thai_nguy_hiem = ["CRISIS", "CRISIS_WARNING"]
    if regime_status in trang_thai_nguy_hiem:
        return {"cap_do": "CAO", "diem": 3, "mo_ta": f"Thị trường đang ở trạng thái {regime_status}"}
    if not lich_su.empty and "status" in lich_su.columns:
        ds_trang_thai = lich_su["status"].dropna().tolist()
        if len(ds_trang_thai) >= 3:
            ba_ngay_truoc = ds_trang_thai[-3:]
            if all(t not in trang_thai_nguy_hiem for t in ba_ngay_truoc) and regime_status in trang_thai_nguy_hiem:
                return {"cap_do": "CAO", "diem": 3, "mo_ta": "Thị trường vừa chuyển sang trạng thái nguy hiểm"}
    return {"cap_do": "BÌNH_THƯỜNG", "diem": 0, "mo_ta": ""}


def _mat_can_bang_dong_tien(flow_state, leading_sectors, health_score) -> dict:
    trang_thai = (flow_state.get("status") or "").upper() if flow_state else ""
    so_nganh_manh = len(leading_sectors)
    if trang_thai in ("MỞ_RỘNG", "MỞ_RỘNG_TÍCH_CỰC") and so_nganh_manh <= 1 and health_score is not None and health_score > 70:
        return {"cap_do": "CAO", "diem": 3, "mo_ta": "Dòng tiền chỉ tập trung vào 1 nhóm ngành duy nhất"}
    if trang_thai in ("MỞ_RỘNG", "MỞ_RỘNG_TÍCH_CỰC") and so_nganh_manh <= 2:
        return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": f"Dòng tiền tập trung hẹp ({so_nganh_manh} nhóm)"}
    if trang_thai in ("THU_HẸP", "PHÂN_HÓA"):
        return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": "Dòng tiền đang co lại hoặc phân hóa"}
    if trang_thai == "DUY_TRÌ":
        return {"cap_do": "THẤP", "diem": 1, "mo_ta": "Dòng tiền duy trì, chưa có dấu hiệu mở rộng"}
    return {"cap_do": "BÌNH_THƯỜNG", "diem": 0, "mo_ta": ""}


def _canh_bao_tai_san_tru_an(gold_premium_pct, gold_premium_regime, market_state, regime_data) -> dict:
    try:
        from src.services.safe_haven_sentiment import phan_vung_tam_ly

        vung = phan_vung_tam_ly(
            gold_premium_pct=gold_premium_pct,
            gold_premium_regime=gold_premium_regime,
            market_state=market_state,
            regime_data=regime_data,
        )
        vung.get("diem", 0.0)
        zone = vung.get("vung", "BÌNH_THƯỜNG")
        cac_ly_do = vung.get("ly_do", [])

        if zone == "HOẢNG_LOẠN":
            return {"cap_do": "CAO", "diem": 4, "mo_ta": "; ".join(cac_ly_do) if cac_ly_do else "Hoảng loạn trú ẩn"}
        if zone == "PHÒNG_THỦ_RÕ_RỆT":
            return {"cap_do": "CAO", "diem": 3, "mo_ta": "; ".join(cac_ly_do) if cac_ly_do else "Phòng thủ rõ rệt"}
        if zone == "HƠI_THẬN_TRỌNG":
            return {"cap_do": "TRUNG_BÌNH", "diem": 2, "mo_ta": "; ".join(cac_ly_do) if cac_ly_do else "Hơi thận trọng"}
        return {"cap_do": "BÌNH_THƯỜNG", "diem": 0, "mo_ta": "Tâm lý trú ẩn bình thường"}
    except (ImportError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Không chạy được phân vùng tâm lý: %s", e)
        return {"cap_do": "KHÔNG_RÕ", "diem": 0, "mo_ta": ""}


def _cap_do_tong_hop(tong_diem) -> str:
    if tong_diem >= 10:
        return "RỦI_RO_HỆ_THỐNG"
    if tong_diem >= 7:
        return "CHUYỂN_PHA_MẠNH"
    if tong_diem >= 4:
        return "CHUYỂN_PHA_NHE"
    return "BÌNH_THƯỜNG"


def _cap_do_ky_hieu(cap_do) -> str:
    ma = {
        "RỦI_RO_HỆ_THỐNG": "⚫",
        "CHUYỂN_PHA_MẠNH": "🔴",
        "CHUYỂN_PHA_NHE": "🟠",
        "BÌNH_THƯỜNG": "🟡",
    }
    return ma.get(cap_do, "🟡")


def _cap_do_tieng_viet(cap_do) -> str:
    ma = {
        "RỦI_RO_HỆ_THỐNG": "Rủi ro hệ thống cao",
        "CHUYỂN_PHA_MẠNH": "Đang chuyển pha mạnh",
        "CHUYỂN_PHA_NHE": "Bắt đầu chuyển pha",
        "BÌNH_THƯỜNG": "Bình thường",
    }
    return ma.get(cap_do, "Bình thường")


def build_early_warning(
    regime_data: dict | None = None,
    market_state: dict | None = None,
    gold_premium_pct: float | None = None,
    gold_premium_regime: str | None = None,
) -> dict:
    lich_su = _lay_du_lieu_lich_su(so_ngay=10)

    regime_status = (regime_data or {}).get("status", "UNKNOWN")
    regime_score = (regime_data or {}).get("regime_score")
    rad = (regime_data or {}).get("rad", {})
    rad_activated = rad.get("activated", False)
    delta_adx = rad.get("signals", {}).get("delta_adx")
    rad.get("signals", {}).get("v_breadth")
    breadth_pct = (regime_data or {}).get("details", {}).get("breadth_pct")

    flow_state = (market_state or {}).get("flow_state", {})
    leading_sectors = flow_state.get("leading_sectors", [])
    (market_state or {}).get("meta_state", {})
    health_score = (market_state or {}).get("breadth_state", {}).get("health_score")

    ca1 = _toc_do_thay_doi_adx(delta_adx)
    ca2 = _toc_do_thay_doi_do_rong(lich_su, breadth_pct)
    ca3 = _toc_do_thay_doi_diem_regime(lich_su, regime_score)
    ca4 = _canh_bao_chuyen_pha(rad_activated, regime_status, lich_su)
    ca5 = _mat_can_bang_dong_tien(flow_state, leading_sectors, health_score)
    ca6 = _canh_bao_tai_san_tru_an(gold_premium_pct, gold_premium_regime, market_state, regime_data)

    tong_diem = sum(x.get("diem", 0) for x in [ca1, ca2, ca3, ca4, ca5, ca6])
    cap_do = _cap_do_tong_hop(tong_diem)

    cac_canh_bao_hien = []
    for ten, ca in [
        ("tốc độ ADX", ca1),
        ("tốc độ độ rộng", ca2),
        ("tốc độ điểm thị trường", ca3),
        ("chuyển pha", ca4),
        ("mất cân bằng dòng tiền", ca5),
        ("tài sản trú ẩn", ca6),
    ]:
        if ca.get("diem", 0) >= 2:
            mo_ta = ca.get("mo_ta", "")
            if mo_ta:
                cac_canh_bao_hien.append(mo_ta)

    return {
        "cap_do_ma": cap_do,
        "cap_do_ky_hieu": _cap_do_ky_hieu(cap_do),
        "cap_do_tieng_viet": _cap_do_tieng_viet(cap_do),
        "tong_diem": tong_diem,
        "chi_tiet": {
            "toc_do_adx": ca1,
            "toc_do_do_rong": ca2,
            "toc_do_diem_regime": ca3,
            "canh_bao_chuyen_pha": ca4,
            "mat_can_bang_dong_tien": ca5,
            "canh_bao_tai_san_tru_an": ca6,
        },
        "canh_bao": cac_canh_bao_hien,
    }
