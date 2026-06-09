import sys
import logging
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

from src.engine.universe import SECTOR_MAP

_LOCAL_SECTOR_MAP = {**SECTOR_MAP, "TLG": "CONSUMER"}

_ACTION_MAP = {
    "CÓ_THỂ_THAM_GIA": {"ten": "Có thể tham gia", "ky_hieu": "🟢"},
    "THEO_DÕI": {"ten": "Theo dõi", "ky_hieu": "🟡"},
    "HẠN_CHẾ_RỦI_RO": {"ten": "Hạn chế rủi ro", "ky_hieu": "🟠"},
    "TRÁNH_XA": {"ten": "Tránh xa / Thoát", "ky_hieu": "🔴"},
}

_SECTOR_VI = {
    "BANK": "Ngân hàng", "RE": "Bất động sản", "SEC": "Chứng khoán",
    "STEEL": "Thép", "CONSUMER": "Tiêu dùng", "TECH": "Công nghệ",
    "OIL": "Dầu khí", "TRANS": "Vận tải", "CONST": "Xây dựng",
    "FOOD": "Thực phẩm", "UTILITY": "Tiện ích / Điện",
}


def phan_loai_danh_muc(danh_sach: list, report: dict) -> list:
    canh_bao = report.get("canh_bao_som", {})
    xac_nhan = report.get("xac_nhan_chuyen_pha", {})
    quyet_dinh = report.get("quyet_dinh_cuoi_cung", {})
    rui_ro = report.get("rui_ro", {})
    dong_tien = report.get("dong_tien", {})

    cap_do_canh_bao = canh_bao.get("cap_do_ma", "BÌNH_THƯỜNG")
    ket_luan_xac_nhan = xac_nhan.get("ket_luan", "NHIỄU")
    ma_quyet_dinh = quyet_dinh.get("ma", "AN_TOÀN")
    xep_loai_rui_ro = rui_ro.get("xep_loai", "thấp")
    nhom_manh = dong_tien.get("nhom_manh_nhat", [])
    nhom_yeu = dong_tien.get("nhom_yeu_nhat", [])

    nhom_manh_sector = set(s for s in nhom_manh if s in _SECTOR_VI)

    ket_qua = []
    for symbol in danh_sach:
        sector_code = _LOCAL_SECTOR_MAP.get(symbol, "OTHER")
        sector_vi = _SECTOR_VI.get(sector_code, sector_code)

        s = symbol.upper()
        la_manh = any(s in sym for sym in nhom_manh)
        la_yeu = any(s in sym for sym in nhom_yeu)
        la_sector_manh = sector_code in nhom_manh_sector

        action = _quyet_dinh(sector_code, la_manh, la_yeu, la_sector_manh,
                             cap_do_canh_bao, ket_luan_xac_nhan,
                             ma_quyet_dinh, xep_loai_rui_ro)

        info = _ACTION_MAP[action]
        ket_qua.append({
            "symbol": symbol,
            "sector_vn": sector_vi,
            "sector_code": sector_code,
            "hanh_dong": info["ten"],
            "ky_hieu": info["ky_hieu"],
            "ma": action,
        })

    return ket_qua


def _quyet_dinh(sector_code, la_manh, la_yeu, la_sector_manh,
                cap_do_canh_bao, ket_luan_xac_nhan,
                ma_quyet_dinh, xep_loai_rui_ro):

    if ma_quyet_dinh == "HOẢNG_LOẠN":
        return "TRÁNH_XA"

    if ket_luan_xac_nhan == "CHUYỂN_PHA_THẬT":
        return "TRÁNH_XA"

    if cap_do_canh_bao in ("RỦI_RO_HỆ_THỐNG",):
        return "HẠN_CHẾ_RỦI_RO"

    if ma_quyet_dinh == "NGUY_HIỂM":
        if sector_code in ("BANK", "UTILITY", "FOOD") and la_sector_manh:
            return "THEO_DÕI"
        return "HẠN_CHẾ_RỦI_RO"

    if ma_quyet_dinh == "CẨN_THẬN":
        if la_manh or la_sector_manh:
            return "THEO_DÕI"
        if xep_loai_rui_ro == "cao":
            return "HẠN_CHẾ_RỦI_RO"
        return "THEO_DÕI"

    if ma_quyet_dinh == "AN_TOÀN":
        if la_manh or la_sector_manh:
            return "CÓ_THỂ_THAM_GIA"
        if sector_code in ("BANK", "TECH", "CONSUMER", "UTILITY"):
            return "CÓ_THỂ_THAM_GIA"
        if sector_code in ("STEEL", "OIL", "CONST", "TRANS", "RE"):
            return "THEO_DÕI"
        return "THEO_DÕI"

    return "THEO_DÕI"
