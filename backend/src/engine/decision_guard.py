"""
decision_guard.py — Lớp bảo vệ quyết định cuối cùng

Vai trò:
  Không phải "tầng quyết định". Là "lớp kiểm tra an toàn".

  Nhiệm vụ duy nhất:
    Kiểm tra nếu Độ tin cậy quá thấp → chặn quyết định.
    Nếu không → giữ nguyên quyết định từ Bộ não chính.

Quy tắc:
  - Không tự đưa ra quyết định.
  - Chỉ có quyền phủ quyết (override → DỪNG NGOÀI).
  - Lý do phủ quyết phải rõ ràng, gắn với tín hiệu cụ thể.
"""
from typing import Optional

def kiem_tra_an_toan(
    quyet_dinh_de_xuat: str,
    ly_do_de_xuat: list,
    do_tin_cay: dict,
    anh_chup: Optional[dict] = None,
) -> dict:
    """Kiểm tra an toàn trước khi cho phép quyết định đi vào thực tế.

    Args:
        quyet_dinh_de_xuat: Quyết định từ Bộ quyết định cuối cùng
        ly_do_de_xuat: Lý do kèm theo
        do_tin_cay: Output từ Bộ tự đánh giá độ tin cậy
        anh_chup: Ảnh chụp thị trường (dùng để kiểm tra entropy + cấu trúc)

    Returns:
        dict: {
            "quyet_dinh": quyết định sau bảo vệ,
            "ly_do": lý do (có thể bổ sung nếu bị chặn),
            "bi_chặn": True/False,
            "ly_do_chặn": "..." hoặc None,
        }
    """
    quyet_dinh = quyet_dinh_de_xuat
    ly_do = list(ly_do_de_xuat)
    bi_chặn = False
    ly_do_chặn = None

    # Lấy thông tin từ Bộ tự đánh giá
    tam_ngung = do_tin_cay.get("tạm_ngưng_kết_luận", False)
    diem_tin_cay = do_tin_cay.get("điểm_tin_cậy", 0.5)

    # Lấy entropy, cấu trúc, và index reality từ ảnh chụp (nếu có)
    entropy = None
    so_tru = 0
    diem_thi_truong_that = None
    do_lech_pha = None
    nhan_dien = None
    if anh_chup:
        c = anh_chup.get("cau_truc", {})
        entropy = c.get("entropy")
        so_tru = c.get("so_tru", 0)
        ir = anh_chup.get("phan_tich_chi_so", {})
        diem_thi_truong_that = ir.get("diem_thi_truong_that")
        do_lech_pha = ir.get("do_lech_pha")
        nhan_dien = ir.get("nhan_dien")

    # ── Bước 1: Kiểm tra Index Reality (hard override — cơ chế veto cấu trúc) ──
    if do_lech_pha == "MANH_GIA_TAO":
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = "thị trường 'mạnh giả tạo' — chỉ số bị kéo, không đại diện"
        ly_do = ["thị trường tăng giả tạo — dừng ngoài"]
        bi_chặn = True
    elif diem_thi_truong_that is not None and diem_thi_truong_that < 0.2:
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = "thị trường ảo — chỉ số hoàn toàn mất kết nối với nội tại"
        ly_do = ["thị trường ảo — dừng mọi giao dịch"]
        bi_chặn = True

    # ── Bước 2: Kiểm tra Confidence Layer (tạm ngưng từ độ tin cậy) ──
    elif tam_ngung:
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = do_tin_cay.get(
            "lý_do_tạm_ngưng",
            "độ tin cậy xuống dưới ngưỡng an toàn",
        )
        ly_do = ["thị trường quá nhiễu — tạm ngưng kết luận"]
        bi_chặn = True

    return {
        "quyet_dinh": quyet_dinh,
        "ly_do": ly_do,
        "bi_chặn": bi_chặn,
        "ly_do_chặn": ly_do_chặn,
    }
