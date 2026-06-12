"""
orchestrator.py — Bộ quyết định cuối cùng

Thứ tự ưu tiên (CAO → THẤP):
  1. Cấu trúc thị trường (nếu VỠ CẤU TRÚC → DỪNG NGOÀI, không xét gì thêm)
  2. Cảnh báo sớm (nếu chuyển pha → hạ mức hành động)
  3. Trạng thái thị trường (regime — ngữ cảnh)
  4. Độ tin cậy → Lớp bảo vệ quyết định (có thể chặn nếu quá nhiễu)

Nguyên tắc dữ liệu:
  - Mọi tầng dùng chung 1 ảnh chụp thị trường duy nhất mỗi lần chạy
  - Ảnh chụp được tạo bởi market_snapshot.tao_anh_chup()
  - Không đọc regime từ file cache, không recompute regime giữa chừng
"""

import sys, os, json
from pathlib import Path
from datetime import datetime
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

import src.config


QUYET_DINH = ["THAM GIA", "THAM GIA DO", "QUAN SAT", "GIAM RUI RO", "DUNG NGOAI"]


def quyet_dinh_cuoi(target_date: Optional[str] = None) -> dict:
    """Trả về quyết định cuối cùng dựa trên 3 lớp phân tích.

    Luồng:
      1. Tạo ảnh chụp thị trường (regime + structural + cảnh báo sớm) — 1 lần
      2. Logic quyết định theo thứ tự ưu tiên
      3. Tính độ tin cậy — dùng chung ảnh chụp
      4. Lớp bảo vệ — kiểm tra an toàn cuối
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # ---- Bước 1: Ảnh chụp thị trường duy nhất ----
    from src.core.market_snapshot import tao_anh_chup
    anh_chup = tao_anh_chup(target_date)

    r = anh_chup.get("regime", {})
    c = anh_chup.get("cau_truc", {})
    ew = anh_chup.get("canh_bao_som", {})

    trang_thai_cau_truc = c.get("trang_thai", "N/A")
    so_tru_ok = c.get("so_tru", 0)
    entropy = c.get("entropy")
    regime_status = r.get("trang_thai", "N/A")
    early_warning = ew.get("co_canh_bao", False)
    # ---- Bước 2: Logic quyết định theo thứ tự ưu tiên ----
    ly_do = []

    # ⭐ Ưu tiên 1: Cấu trúc thị trường
    if trang_thai_cau_truc == "VỠ CẤU TRÚC":
        quyet_dinh = "DUNG NGOAI"
        ly_do = [
            "cấu trúc thị trường đang vỡ",
            "dòng tiền không lan tỏa hoặc chỉ số lệch nội bộ",
            "rủi ro hệ thống cao",
        ]
    elif trang_thai_cau_truc == "PHÂN KỲ CẤU TRÚC":
        # ⭐ Ưu tiên 2: Cảnh báo sớm (chỉ có hiệu lực khi cấu trúc chưa vỡ)
        if early_warning:
            quyet_dinh = "GIAM RUI RO"
            ly_do = [
                "thị trường phân kỳ cấu trúc",
                "dòng tiền tập trung hẹp, chỉ số không đại diện",
                "có tín hiệu phòng thủ từ cảnh báo sớm",
            ]
        else:
            quyet_dinh = "GIAM RUI RO"
            ly_do = [
                "thị trường phân kỳ cấu trúc",
                "chỉ số tăng nhưng nội bộ yếu",
                "cần giảm rủi ro danh mục",
            ]
    elif trang_thai_cau_truc == "PHÂN HÓA BÌNH THƯỜNG":
        quyet_dinh = "QUAN SAT"
        ly_do = [
            "thị trường phân hóa nhẹ nhưng ổn định",
            "chưa có xu hướng rõ ràng",
            "ưu tiên theo dõi thêm",
        ]
    elif trang_thai_cau_truc == "ĐỒNG THUẬN":
        if early_warning:
            quyet_dinh = "QUAN SAT"
            ly_do = [
                "cấu trúc thị trường đồng thuận",
                "nhưng có tín hiệu phòng thủ từ cảnh báo sớm",
                "cần xác nhận thêm trước khi tham gia",
            ]
        else:
            quyet_dinh = "THAM GIA"
            ly_do = [
                "thị trường đồng thuận cả 3 trụ",
                "dòng tiền lan rộng, các ngành cùng tăng",
                "chỉ số phản ánh đúng thị trường",
            ]
    else:
        quyet_dinh = "QUAN SAT"
        ly_do = ["chưa đủ dữ liệu để kết luận"]

    # ---- Bổ sung entropy nếu cao ----
    if entropy is not None and entropy > 0.8:
        ly_do.append(f"entropy thị trường cao ({entropy:.2f})")

    # ---- ⭐ Ưu tiên 3: Trạng thái thị trường (regime) ----
    if regime_status != "N/A" and regime_status != "TRENDING":
        if quyet_dinh != "DUNG NGOAI":
            ly_do.append(f"thị trường đang {regime_status}")

    # ---- ⭐ Index Reality: inject context vào pipeline ----
    ir = anh_chup.get("phan_tich_chi_so", {})
    do_lech_pha_ir = ir.get("do_lech_pha")
    diem_thi_truong_that_ir = ir.get("diem_thi_truong_that")
    nhan_dien_ir = ir.get("nhan_dien")

    if do_lech_pha_ir == "MANH_GIA_TAO":
        # Override: thị trường tăng giả tạo → dừng ngoài
        quyet_dinh = "DUNG NGOAI"
        ly_do = ["thị trường tăng giả tạo — chỉ số không đại diện"]
    elif nhan_dien_ir == "THI_TRUONG_AO" or (diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.2):
        quyet_dinh = "DUNG NGOAI"
        ly_do = ["thị trường ảo — chỉ số không phản ánh giá trị thực"]
    elif diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.4:
        if quyet_dinh == "THAM GIA":
            quyet_dinh = "QUAN SAT"
            ly_do.append("chất lượng chỉ số thấp — hạ mức hành động")
        elif quyet_dinh == "GIAM RUI RO":
            ly_do.append("thị trường méo — xác nhận phân kỳ")

    # ---- Chỉ giữ 3 lý do chính ----
    ly_do = ly_do[:3]

    ket_qua = {
        "ngay": target_date,
        "quyet_dinh": quyet_dinh,
        "ly_do": ly_do,
        "chi_tiet": {
            "cau_truc": trang_thai_cau_truc,
            "regime": regime_status,
            "canh_bao_som": "có" if early_warning else "không",
            "entropy": round(entropy, 3) if entropy is not None else None,
            "so_tru_cau_truc": so_tru_ok,
        },
    }

    # ---- Bước 3: Tự đánh giá độ tin cậy (dùng chung ảnh chụp) ----
    try:
        from src.engine.confidence_layer import đánh_giá_độ_tin_cậy
        đg = đánh_giá_độ_tin_cậy(anh_chup=anh_chup)
        ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
            "điểm_số": đg["điểm_tin_cậy"],
            "mức": đg["mức_đánh_giá"],
            "tạm_ngưng": đg["tạm_ngưng_kết_luận"],
            "lý_do_tạm_ngưng": đg["lý_do_tạm_ngưng"],
        }
    except Exception:
        ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"] = {
            "điểm_số": 0.5, "mức": "TRUNG_BINH", "tạm_ngưng": False,
        }

    # ---- Bước 4: Lớp bảo vệ quyết định (Guard) ----
    try:
        from src.engine.decision_guard import kiem_tra_an_toan
        guarded = kiem_tra_an_toan(
            quyet_dinh_de_xuat=ket_qua["quyet_dinh"],
            ly_do_de_xuat=ket_qua["ly_do"],
            do_tin_cay=ket_qua["độ_tin_cậy_sau_hiệu_chỉnh"],
            anh_chup=anh_chup,
        )
        ket_qua["quyet_dinh"] = guarded["quyet_dinh"]
        ket_qua["ly_do"] = guarded["ly_do"]
        ket_qua["bi_chặn_bởi_bảo_vệ"] = guarded["bi_chặn"]
        ket_qua["lý_do_chặn"] = guarded["ly_do_chặn"]
    except Exception:
        ket_qua["bi_chặn_bởi_bảo_vệ"] = False
        ket_qua["lý_do_chặn"] = None

    # ---- Bước 5: Recovery Override + Structural Healing ----
    # Hệ thống đang DỪNG NGOÀI → kiểm tra khả năng mở khóa
    if ket_qua.get("quyet_dinh") == "DUNG NGOAI":
        is_fake_market = (
            do_lech_pha_ir == "MANH_GIA_TAO"
            or nhan_dien_ir == "THI_TRUONG_AO"
            or (diem_thi_truong_that_ir is not None and diem_thi_truong_that_ir < 0.2)
        )
        if not is_fake_market:
            try:
                from src.engine.recovery_engine import evaluate_recovery_status
                from src.engine.structural_healing import phan_tich_hoi_phuc

                regime_details = anh_chup.get("_regime_details", {})
                velocity_5d = float(regime_details.get("breadth_momentum", 0))
                regime_data_for_recovery = {
                    "details": {
                        "breadth_pct": regime_details.get("breadth_pct", 0),
                        "atr_ratio": regime_details.get("atr_ratio", 0),
                        "breadth_std_10d": regime_details.get("breadth_std_10d", 0),
                    }
                }

                # 5a. Recovery Engine (xung lực giá/khối lượng)
                recovery = evaluate_recovery_status(
                    regime_data=regime_data_for_recovery,
                    velocity_5d=velocity_5d,
                    target_date=target_date,
                )
                ket_qua["recovery_status"] = recovery["status"]
                ket_qua["recovery_log"] = recovery.get("log", [])

                if recovery.get("is_recovery"):
                    ket_qua["quyet_dinh"] = "THAM GIA DO"
                    ket_qua["ly_do"] = [
                        "cấu trúc đã lành — recovery engine xác nhận",
                        "dòng tiền mồi quay lại (thrust/velocity)",
                        "mở lệnh thăm dò — giám sát chặt",
                    ]
                    ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                    ket_qua["lý_do_chặn"] = None
                else:
                    # 5b. Structural Healing (chuyển trạng thái cấu trúc T-1→T-0)
                    healing = phan_tich_hoi_phuc(target_date=target_date)
                    ket_qua["healing_status"] = healing["trang_thai_hoi_phuc"]
                    ket_qua["chuyen_doi_cau_truc"] = healing["chuyen_doi"]

                    hs = healing["trang_thai_hoi_phuc"]
                    if hs == "TAI_PHAT_BENH":
                        ket_qua["ly_do"] = [
                            "cấu trúc tiếp tục vỡ — hồi phục thất bại",
                            "cấm tuyệt đối bắt đáy — rủi ro sập lần 2",
                            "chờ tín hiệu lành thực sự",
                        ]
                    elif hs in ("BAT_DAU_LANH", "DANG_LANH"):
                        ket_qua["quyet_dinh"] = "QUAN SAT"
                        ket_qua["ly_do"] = [
                            f"cấu trúc đang lành ({hs})",
                            "máu đã ngừng chảy — hé mắt quan sát",
                            "chưa mua — chờ recovery hoặc đồng thuận",
                        ]
                        ket_qua["bi_chặn_bởi_bảo_vệ"] = False
                        ket_qua["lý_do_chặn"] = None
            except Exception:
                pass

    # ---- Lưu file ----
    out_dir = Path(src.config.DATA_DIR) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "final_decision.json", "w", encoding="utf-8") as f:
        json.dump(ket_qua, f, indent=2, ensure_ascii=False)

    return ket_qua


def in_bao_cao(kq: dict):
    icons = {"THAM GIA": "🟢", "THAM GIA DO": "🔵", "QUAN SAT": "🟡", "GIAM RUI RO": "🟠", "DUNG NGOAI": "🔴"}
    icon = icons.get(kq.get("quyet_dinh", ""), "⚪")
    print("\n" + "=" * 60)
    print("  BỘ QUYẾT ĐỊNH CUỐI CÙNG")
    print("=" * 60)
    print(f"  {icon} Quyết định: {kq.get('quyet_dinh', 'N/A')}")
    if kq.get("bi_chặn_bởi_bảo_vệ"):
        print(f"      ↳ Bị chặn bởi lớp bảo vệ")
    print()
    for i, ld in enumerate(kq.get("ly_do", []), 1):
        print(f"    {i}. {ld}")
    print()
    ct = kq.get("chi_tiet", {})
    print(f"  Cấu trúc:     {ct.get('cau_truc', 'N/A')} ({ct.get('so_tru_cau_truc', '?')}/3 trụ)")
    print(f"  Regime:       {ct.get('regime', 'N/A')}")
    print(f"  Cảnh báo sớm: {ct.get('canh_bao_som', 'N/A')}")
    print(f"  Entropy:      {ct.get('entropy', 'N/A')}")
    rl = kq.get("recovery_log", [])
    if rl:
        print(f"  Recovery:")
        for line in rl:
            print(f"    {line}")
    hs = kq.get("healing_status")
    cd = kq.get("chuyen_doi_cau_truc")
    if hs:
        icons_hs = {
            "TAI_PHAT_BENH": "🚨", "DANG_VO": "🔴",
            "BAT_DAU_LANH": "🟠", "DANG_LANH": "🟡", "DA_LANH": "🟢",
        }
        icon_hs = icons_hs.get(hs, "⚪")
        print(f"  Lành:         {icon_hs} {hs} ({cd})")
    đg = kq.get("độ_tin_cậy_sau_hiệu_chỉnh", {})
    if đg:
        icons_dg = {"CAO": "🟢", "TRUNG_BINH": "🟡", "THAP": "🔴"}
        icon_dg = icons_dg.get(đg.get("mức", ""), "⚪")
        print(f"  Tin cậy:      {icon_dg} {đg.get('điểm_số', 0):.0%} ({đg.get('mức', 'N/A')})")
        if đg.get("tạm_ngưng"):
            print(f"  ⚠ Tạm ngưng kết luận: {đg.get('lý_do_tạm_ngưng', '')}")
    print("=" * 60)


if __name__ == "__main__":
    kq = quyet_dinh_cuoi()
    in_bao_cao(kq)
