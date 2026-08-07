"""
structural_healing.py — Máy dò chuyển trạng thái cấu trúc (Temporal Transition)

So sánh trạng thái cấu trúc giữa T-1 và T-0 để xác định:
  - DANG_VO: vẫn đang vỡ (VỠ→VỠ)
  - BAT_DAU_LANH: bắt đầu lành (VỠ→PHÂN_KỲ)
  - DANG_LANH: đang lành (VỠ/DANG_LANH→PHÂN_HÓA)
  - DA_LANH: đã lành hoàn toàn (→ĐỒNG_THUẬN)
  - TAI_PHAT_BENH: hồi phục thất bại (lành→VỠ lại)
"""

from src.database.db_core import get_connection


def _truoc_do(current_date: str) -> str | None:
    """Truy vấn DB lấy ngày giao dịch liền trước (bỏ T7/CN/lễ)."""
    try:
        with get_connection() as conn:
            df = __import__("pandas").read_sql(
                "SELECT DISTINCT date FROM daily_ohlcv WHERE date < ? AND symbol='VNINDEX' ORDER BY date DESC LIMIT 1",
                conn,
                params=(current_date,),
            )
            if not df.empty:
                return str(df.iloc[0]["date"])
    except Exception:
        pass
    return None


def phan_tich_hoi_phuc(target_date: str) -> dict:
    """So sánh cấu trúc T-1 vs T-0 → xác định healing status.

    Returns:
        dict với keys:
          - trang_thai_hoi_phuc: str (DANG_VO / BAT_DAU_LANH / DANG_LANH / DA_LANH / TAI_PHAT_BENH / CHUA_CO_DU_LIEU / KHONG_XAC_DINH)
          - chuyen_doi: str (vd: "VỠ CẤU TRÚC→PHÂN KỲ CẤU TRÚC")
          - so_tru_T0, so_tru_T1: int hoặc None
          - entropy_T0, entropy_T1: float hoặc None
    """
    from src.engine.structural_detector import detect_cau_truc

    cau_truc_T0 = detect_cau_truc(target_date=target_date)

    ngay_truoc = _truoc_do(target_date)
    if ngay_truoc is None:
        return {
            "trang_thai_hoi_phuc": "CHUA_CO_DU_LIEU",
            "chuyen_doi": "N/A",
            "so_tru_T0": cau_truc_T0.get("so_tru_ok", 0),
            "so_tru_T1": None,
            "entropy_T0": cau_truc_T0.get("entropy"),
            "entropy_T1": None,
        }

    cau_truc_T1 = detect_cau_truc(target_date=ngay_truoc)

    thai_T1 = cau_truc_T1.get("trang_thai", "N/A")
    thai_T0 = cau_truc_T0.get("trang_thai", "N/A")
    tru_T1 = cau_truc_T1.get("so_tru_ok", 0)
    tru_T0 = cau_truc_T0.get("so_tru_ok", 0)
    entropy_T1 = cau_truc_T1.get("entropy")
    entropy_T0 = cau_truc_T0.get("entropy")

    # Dùng số trụ (pillar count) thay vì state name để phát hiện
    # chuyển động tinh vi: VỠ(0→1 trụ) vẫn là BAT_DAU_LANH dù cùng tên VỠ
    if tru_T0 >= 3:
        trang_thai_hoi_phuc = "DA_LANH"
    elif tru_T0 >= 2:
        trang_thai_hoi_phuc = "DANG_LANH"
    elif tru_T0 > tru_T1:
        trang_thai_hoi_phuc = "BAT_DAU_LANH"
    elif tru_T0 < tru_T1:
        trang_thai_hoi_phuc = "TAI_PHAT_BENH"
    else:
        trang_thai_hoi_phuc = "DANG_VO"

    return {
        "trang_thai_hoi_phuc": trang_thai_hoi_phuc,
        "chuyen_doi": f"{thai_T1}→{thai_T0}",
        "so_tru_T0": tru_T0,
        "so_tru_T1": tru_T1,
        "entropy_T0": entropy_T0,
        "entropy_T1": entropy_T1,
    }
