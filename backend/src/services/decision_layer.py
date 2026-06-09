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


_PHAN_LOAI = [
    {
        "ma": "HOẢNG_LOẠN",
        "ten": "Hoảng loạn",
        "ky_hieu": "⚫",
        "mo_ta": "Thị trường đang trong trạng thái khủng hoảng. Nên đứng ngoài hoàn toàn.",
    },
    {
        "ma": "NGUY_HIỂM",
        "ten": "Nguy hiểm",
        "ky_hieu": "🔴",
        "mo_ta": "Thị trường đang xấu đi rõ rệt. Cần thận trọng cao độ.",
    },
    {
        "ma": "CẨN_THẬN",
        "ten": "Cẩn thận",
        "ky_hieu": "🟡",
        "mo_ta": "Thị trường có dấu hiệu bất ổn nhưng chưa rõ hướng. Quan sát thêm.",
    },
    {
        "ma": "AN_TOÀN",
        "ten": "An toàn",
        "ky_hieu": "🟢",
        "mo_ta": "Thị trường ổn định, có thể tham gia trong tầm kiểm soát.",
    },
]


def _tra_phan_loai(ma):
    for pl in _PHAN_LOAI:
        if pl["ma"] == ma:
            return pl
    return _PHAN_LOAI[-1]


def quyet_dinh_cuoi_cung(report: dict) -> dict:
    hanh_vi = report.get("ket_luan_hanh_vi", "Quan sát")
    canh_bao = report.get("canh_bao_som", {})
    xac_nhan = report.get("xac_nhan_chuyen_pha", {})
    tin_hieu = report.get("tin_hieu_dac_biet", [])
    rui_ro = report.get("rui_ro", {})

    cap_do_canh_bao = canh_bao.get("cap_do_ma", "BÌNH_THƯỜNG")
    ket_luan_xac_nhan = xac_nhan.get("ket_luan", "NHIỄU")
    so_nhom_xac_nhan = xac_nhan.get("so_nhom_dat", 0)
    xep_loai_rui_ro = rui_ro.get("xep_loai", "thấp")

    diem = 0

    # 1. Từ kết luận hành vi
    if hanh_vi == "Nên đứng ngoài":
        diem += 4
    elif hanh_vi == "Thận trọng":
        diem += 3
    elif hanh_vi == "Quan sát":
        diem += 1

    # 2. Từ cảnh báo sớm
    if cap_do_canh_bao == "RỦI_RO_HỆ_THỐNG":
        diem += 4
    elif cap_do_canh_bao == "CHUYỂN_PHA_MẠNH":
        diem += 3
    elif cap_do_canh_bao == "CHUYỂN_PHA_NHE":
        diem += 2

    # 3. Từ xác nhận chuyển pha
    if ket_luan_xac_nhan == "CHUYỂN_PHA_THẬT":
        diem += 4
    elif ket_luan_xac_nhan == "ĐANG_CHUYỂN_PHA":
        diem += 2

    # 4. Từ xếp loại rủi ro
    if xep_loai_rui_ro == "cao":
        diem += 2
    elif xep_loai_rui_ro == "trung bình":
        diem += 1

    # 5. Từ tín hiệu đặc biệt
    tin_hieu_cao = sum(1 for th in tin_hieu if th.get("muc_do") == "cao" and th.get("loai") != "thông tin")
    diem += tin_hieu_cao

    if diem >= 8:
        ma = "HOẢNG_LOẠN"
    elif diem >= 5:
        ma = "NGUY_HIỂM"
    elif diem >= 3:
        ma = "CẨN_THẬN"
    else:
        ma = "AN_TOÀN"

    pl = _tra_phan_loai(ma)
    return {
        "ma": ma,
        "ten": pl["ten"],
        "ky_hieu": pl["ky_hieu"],
        "mo_ta": pl["mo_ta"],
        "diem": diem,
    }
