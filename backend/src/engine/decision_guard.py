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

  - He so giam ty trong (data_quality_multiplier):
      Là cơ chế điều chỉnh tỷ trọng giải ngân dựa trên chất lượng dữ liệu.
      Không phải binary block — là graduated risk scaling.
      1.0 = full position, 0.3 = 30% max position.
      Hàm suy giảm tuyến tính 2 pha:
        Pha 1 (24h→48h): 1.0→0.3 (suy giảm nhanh tuần đầu)
        Pha 2 (48h→168h): 0.3→0.1 (suy giảm chậm, duy trì probe position)
      INSUFFICIENT_DATA → 0.0 (không có dữ liệu, không giao dịch).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _he_so_tuoi_du_lieu(hours_stale: float) -> float:
    """Hàm suy giảm tuyến tính 2 pha dựa trên số giờ dữ liệu trễ.

    Grace period 24h: không suy giảm (he_so=1.0).
    Pha 1 (24→48h): suy giảm nhanh từ 1.0 xuống 0.3.
    Pha 2 (48→168h): suy giảm chậm từ 0.3 xuống 0.1.
    Trần 0.1 = absolute floor (giữ khả năng thăm dò tối thiểu).
    """
    if hours_stale <= 24:
        return 1.0
    elif hours_stale <= 48:
        progress = (hours_stale - 24) / 24
        return round(1.0 - 0.7 * progress, 2)
    else:
        progress = min((hours_stale - 48) / 120, 1.0)
        return round(max(0.1, 0.3 - 0.2 * progress), 2)

def kiem_tra_an_toan(
    quyet_dinh_de_xuat: str,
    ly_do_de_xuat: list,
    do_tin_cay: dict,
    anh_chup: Optional[dict] = None,
    du_lieu_lien_ngan_hang: Optional[dict] = None,
) -> dict:
    """Kiểm tra an toàn trước khi cho phép quyết định đi vào thực tế.

    Args:
        quyet_dinh_de_xuat: Quyết định từ Bộ quyết định cuối cùng
        ly_do_de_xuat: Lý do kèm theo
        do_tin_cay: Output từ Bộ tự đánh giá độ tin cậy
        anh_chup: Ảnh chụp thị trường (dùng để kiểm tra entropy + cấu trúc)
        du_lieu_lien_ngan_hang: Output từ assess_interbank_risk()
            (dùng để kiểm tra stale_override + điều chỉnh tỷ trọng)

    Returns:
        dict: {
            "quyet_dinh": quyết định sau bảo vệ,
            "ly_do": lý do (có thể bổ sung nếu bị chặn),
            "bi_chặn": True/False,
            "ly_do_chặn": "..." hoặc None,
            "he_so_giam_ty_trong": 0.0 ~ 1.0 (mặc định 1.0),
        }
    """
    quyet_dinh = quyet_dinh_de_xuat
    ly_do = list(ly_do_de_xuat)
    bi_chặn = False
    ly_do_chặn = None

    # ── Bước 0: Data Quality Multiplier (graduated, không veto) ──
    he_so_giam_ty_trong = 1.0
    if du_lieu_lien_ngan_hang:
        ib_veto = du_lieu_lien_ngan_hang.get("veto", "NONE")
        stale = du_lieu_lien_ngan_hang.get("stale_override", False)
        hours_stale = du_lieu_lien_ngan_hang.get("hours_stale", 0)
        z_fast = du_lieu_lien_ngan_hang.get("zscore", {}).get("z_fast", 0)

        if ib_veto == "STRUCTURE_UNKNOWN":
            he_so_giam_ty_trong = 0.0
            logger.critical("[GUARD] SBV STRUCTURE UNKNOWN — sensor blind, he_so_giam_ty_trong=0.0")
        elif ib_veto == "INSUFFICIENT_DATA":
            he_so_giam_ty_trong = 0.0
            logger.warning("[GUARD] Khong du du lieu lien ngan hang, he_so_giam_ty_trong=0.0")
        elif stale:
            he_so_giam_ty_trong = _he_so_tuoi_du_lieu(hours_stale)
            logger.warning(
                "[GUARD] stale_override=True (Z_fast=%.1f, %gh), he_so_giam_ty_trong=%.2f",
                z_fast, hours_stale, he_so_giam_ty_trong,
            )
        elif ib_veto == "ACTIVE":
            he_so_giam_ty_trong = 0.0
            logger.warning("[GUARD] Interbank VETO ACTIVE, he_so_giam_ty_trong=0.0")

        # ── Cross-Layer Volatility Coupling (temporal + cooldown) ──
        on_rate = du_lieu_lien_ngan_hang.get("on_rate", 0.0)
        recovery_days = du_lieu_lien_ngan_hang.get("recovery_days", 0)
        is_lc = du_lieu_lien_ngan_hang.get("is_liquidity_crisis", False)

        from src.engine.partial_data_entropy import compute_temporal_penalty, update_crisis_cooldown, assess_crisis_unlock
        update_crisis_cooldown(on_rate, z_fast)

        if is_lc:
            he_so_giam_ty_trong = 0.0
            logger.critical("[GUARD] LIQUIDITY CRISIS (ON>=15%%) — he_so_giam_ty_trong=0.0")
        elif z_fast > 3.0:
            phi = compute_temporal_penalty(z_fast, is_liquidity_crisis=False)
            he_so_giam_ty_trong *= phi
            logger.info(
                "[GUARD] Temporal coupling: Z_fast=%.1f, Φ=%.3f, he_so=%.3f",
                z_fast, phi, he_so_giam_ty_trong,
            )

        # ── Crisis Cooldown Gate: chặn mở khóa nếu chưa đủ 3 phiên an toàn ──
        if he_so_giam_ty_trong > 0.0 and on_rate > 0:
            if not assess_crisis_unlock(on_rate, z_fast, recovery_days):
                he_so_giam_ty_trong = 0.0
                logger.warning("[GUARD] Crisis cooldown active — he_so_giam_ty_trong=0.0")

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
        so_tru = c.get("so_tru_ok", c.get("so_tru", 0))
        ir = anh_chup.get("phan_tich_chi_so", {})
        diem_thi_truong_that = ir.get("diem_thi_truong_that")
        do_lech_pha = ir.get("do_lech_pha")
        nhan_dien = ir.get("nhan_dien")

    # ── Bước 1: Kiểm tra Cấu trúc & Index Reality (hard override — veto bất chấp confidence) ──

    # 1a — SYSTEMIC LIQUIDITY SHOCK from interbank Z-Score
    for ld in ly_do:
        if "SYSTEMIC_LIQUIDITY_SHOCK" in ld:
            quyet_dinh = "DUNG NGOAI"
            ly_do_chặn = "lãi suất liên ngân hàng tăng đột biến — Systemic Liquidity Shock"
            bi_chặn = True
            logger.warning("[GUARD] INTERBANK Z-SCORE SHOCK: veto kich hoat")
            break

    # 1b — Cấu trúc thị trường
    if not bi_chặn and so_tru <= 1:
        if diem_thi_truong_that is not None and diem_thi_truong_that >= 0.2:
            logger.warning(
                "[GUARD] DIEM_THI_TRUONG_THAT=%.2f >= 0.2 nhung so_tru=%d/3 — "
                "phân kỳ cấu trúc, nghi vấn bẫy thanh khoản",
                diem_thi_truong_that, so_tru,
            )
        quyet_dinh = "DUNG NGOAI"
        ly_do_chặn = f"cấu trúc thị trường vỡ ({so_tru}/3 trụ) — thiếu nền tảng đồng thuận"
        ly_do = ["cấu trúc thị trường vỡ — dừng ngoài"]
        bi_chặn = True
    elif do_lech_pha == "MANH_GIA_TAO":
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

    # ── Nếu bị chặn, ghi đè he_so_giam_ty_trong = 0.0 ──
    if bi_chặn:
        he_so_giam_ty_trong = 0.0

    return {
        "quyet_dinh": quyet_dinh,
        "ly_do": ly_do,
        "bi_chặn": bi_chặn,
        "ly_do_chặn": ly_do_chặn,
        "he_so_giam_ty_trong": he_so_giam_ty_trong,
    }
