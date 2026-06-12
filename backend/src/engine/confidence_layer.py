"""
Bộ tự đánh giá độ tin cậy — quyết định có đáng tin không?

Nhiệm vụ:
  Trước khi đưa ra khuyến nghị, hệ thống tự hỏi:
  "Mình có chắc với nhận định này không?"

Đầu vào:
  - Trạng thái thị trường (từ Bộ nhận diện trạng thái)
  - Cấu trúc thị trường (từ Bộ phát hiện lệch cấu trúc)
  - Cảnh báo sớm (từ Bộ cảnh báo sớm)
  - Mức đáng tin của tín hiệu (từ Sổ theo dõi uy tín)

Đầu ra:
  - Điểm tin cậy (0–100%)
  - Mức đánh giá: Cao / Trung bình / Thấp
  - Chi tiết từng yếu tố
  - Tạm ngưng kết luận nếu điểm quá thấp
"""

import sys, json
from pathlib import Path
from datetime import datetime
from typing import Optional


def _đường_dẫn_gốc():
    if getattr(sys, 'frozen', False):
        đường_dẫn_gốc = Path(sys.executable).resolve().parent
    else:
        hiện_tại = Path(__file__).resolve().parent
        đường_dẫn_gốc = hiện_tại
        while hiện_tại != hiện_tại.parent:
            if (hiện_tại / "AGENTS.md").exists() and (hiện_tại / "backend").is_dir():
                đường_dẫn_gốc = hiện_tại
                break
            hiện_tại = hiện_tại.parent
    for p in (đường_dẫn_gốc, đường_dẫn_gốc / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return đường_dẫn_gốc


ĐƯỜNG_DẪN_GỐC = _đường_dẫn_gốc()
if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config


# ── Ngưỡng đánh giá ─────────────────────────────────────
NGƯỠNG = {
    "CAO": 0.70,
    "TRUNG_BINH": 0.40,
    "THAP": 0.0,
}

TRỌNG_SỐ = {
    "thị_trường_rõ_ràng": 0.20,
    "cấu_trúc_lành_mạnh": 0.20,
    "tín_hiệu_đồng_thuận": 0.20,
    "chất_lượng_chỉ_số": 0.15,
    "biến_động_ổn_định": 0.15,
    "tín_hiệu_đáng_tin": 0.10,
}


def _đọc_json(tên_file: str) -> Optional[dict]:
    đường_dẫn = [
        Path(src.config.DATA_DIR) / "output" / tên_file,
        Path(src.config.DATA_DIR) / tên_file,
    ]
    for p in đường_dẫn:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _mức_đánh_giá(điểm: float) -> str:
    if điểm >= NGƯỠNG["CAO"]:
        return "CAO"
    if điểm >= NGƯỠNG["TRUNG_BINH"]:
        return "TRUNG_BINH"
    return "THAP"


def _mức_ra_chữ(mức: str) -> str:
    bảng = {"CAO": "Cao", "TRUNG_BINH": "Trung bình", "THAP": "Thấp"}
    return bảng.get(mức, "Không rõ")


# ── 6 yếu tố đánh giá ────────────────────────────────────

def _1_thị_trường_rõ_ràng(trạng_thái: str, điểm_số: float,
                            adx: Optional[float], rad_có_kích_hoạt: bool) -> dict:
    điểm = 0.50
    lý_do = []

    if trạng_thái == "TRENDING":
        if adx is not None and adx > 25:
            điểm = 0.85
            lý_do.append(f"thị trường đang có xu hướng rõ (ADX {adx:.0f})")
        elif adx is not None and adx > 20:
            điểm = 0.65
            lý_do.append("thị trường nghiêng về xu hướng (ADX trung bình)")
        else:
            điểm = 0.50
            lý_do.append("thị trường có xu hướng nhưng chưa rõ")
    elif trạng_thái == "RANGING":
        if điểm_số >= 0.45:
            điểm = 0.55
            lý_do.append("thị trường đi ngang khá ổn định")
        else:
            điểm = 0.40
            lý_do.append("thị trường đi ngang thiếu ổn định")
    elif trạng_thái in ("CRISIS", "CRISIS_WARNING"):
        điểm = 0.20
        lý_do.append("thị trường đang trong trạng thái nguy hiểm")

    if rad_có_kích_hoạt:
        điểm *= 0.6
        lý_do.append("hệ thống phát hiện thị trường sắp chuyển pha — điểm giảm")

    return {"điểm": round(điểm, 3), "lý_do": lý_do}


def _2_cấu_trúc_lành_mạnh(trạng_thái_cấu_trúc: str, số_trụ: int,
                            entropy: Optional[float]) -> dict:
    điểm = 0.50
    lý_do = []

    if trạng_thái_cấu_trúc == "ĐỒNG THUẬN":
        điểm = 0.90
        lý_do.append(f"cả {số_trụ}/3 trụ thị trường đang đồng thuận")
    elif trạng_thái_cấu_trúc == "PHÂN HÓA BÌNH THƯỜNG":
        điểm = 0.65
        lý_do.append("thị trường phân hóa nhẹ — vẫn trong vùng an toàn")
    elif trạng_thái_cấu_trúc == "PHÂN KỲ CẤU TRÚC":
        điểm = 0.35
        lý_do.append("cấu trúc thị trường đang bị phân kỳ")
    elif trạng_thái_cấu_trúc == "VỠ CẤU TRÚC":
        điểm = 0.15
        lý_do.append(f"cấu trúc thị trường bị vỡ ({số_trụ}/3 trụ còn hoạt động)")

    if entropy is not None:
        if entropy > 2.0:
            điểm *= 0.5
            lý_do.append("thị trường nhiễu rất mạnh — khó phân tích")
        elif entropy > 1.0:
            điểm *= 0.8
            lý_do.append("thị trường có dấu hiệu nhiễu")
        elif entropy < 0.3:
            điểm = min(1.0, điểm * 1.1)
            lý_do.append("thị trường tương đối ổn định")

    return {"điểm": round(điểm, 3), "lý_do": lý_do}


def _3_tín_hiệu_đồng_thuận(trạng_thái_thị_trường: str, trạng_thái_cấu_trúc: str,
                             mức_cảnh_báo: str, entropy: Optional[float]) -> dict:
    điểm = 0.50
    lý_do = []

    đang_khủng_hoảng = trạng_thái_thị_trường in ("CRISIS", "CRISIS_WARNING")
    cấu_trúc_vỡ = trạng_thái_cấu_trúc == "VỠ CẤU TRÚC"
    cảnh_báo_cao = mức_cảnh_báo in ("RỦI_RO_HỆ_THỐNG", "CHUYỂN_PHA_MẠNH")
    entropy_cao = entropy is not None and entropy > 2.0

    số_tín_hiệu_xấu = sum([đang_khủng_hoảng, cấu_trúc_vỡ, cảnh_báo_cao, entropy_cao])

    if số_tín_hiệu_xấu == 0:
        điểm = 0.90
        lý_do.append("tất cả tín hiệu đang đồng thuận tích cực")
    elif số_tín_hiệu_xấu == 1:
        điểm = 0.60
        lý_do.append("có 1 tín hiệu cảnh báo — cần theo dõi thêm")
    elif số_tín_hiệu_xấu == 2:
        điểm = 0.35
        lý_do.append("có 2 tín hiệu cảnh báo — tin cậy thấp")
    else:
        điểm = 0.15
        lý_do.append("nhiều tín hiệu cảnh báo cùng lúc — rủi ro cao")

    return {"điểm": round(điểm, 3), "lý_do": lý_do}


def _4_biến_động_ổn_định(tỷ_lệ_atr: Optional[float], điểm_v: Optional[float]) -> dict:
    điểm = 0.50
    lý_do = []

    if tỷ_lệ_atr is not None:
        if tỷ_lệ_atr > 1.5:
            điểm = 0.20
            lý_do.append("thị trường đang biến động quá mức bình thường")
        elif tỷ_lệ_atr > 1.2:
            điểm = 0.50
            lý_do.append("biến động có xu hướng tăng nhẹ")
        elif tỷ_lệ_atr < 0.8:
            điểm = 0.80
            lý_do.append("thị trường khá ổn định, biến động thấp")
        else:
            điểm = 0.70
            lý_do.append("mức biến động vẫn trong giới hạn bình thường")

    if điểm_v is not None:
        điểm = điểm * (0.5 + 0.5 * điểm_v)

    return {"điểm": round(min(1.0, điểm), 3), "lý_do": lý_do}


def _5_tín_hiệu_đáng_tin(trạng_thái: str) -> dict:
    điểm = 0.50
    lý_do = []
    try:
        from src.telemetry.driver_reputation import get_reputation_summary
        tóm_tắt = get_reputation_summary()
        các_driver = tóm_tắt.get("drivers", [])
        if các_driver:
            tốt_nhất = các_driver[0]
            độ_chính_xác = tốt_nhất.get("accuracy", 0)
            độ_trôi = tốt_nhất.get("performance_drift")
            tên_driver = tốt_nhất.get("driver", "N/A")
            điểm = độ_chính_xác
            if độ_trôi is not None:
                if độ_trôi < -0.05:
                    điểm *= 0.7
                    lý_do.append(f"tín hiệu {tên_driver} đang mất hiệu lực dần")
                elif độ_trôi > 0.05:
                    điểm *= 1.1
                    lý_do.append(f"tín hiệu {tên_driver} đang cải thiện độ chính xác")
            lý_do.append(f"tín hiệu chủ đạo ({tên_driver}) từng cho kết quả tốt (đúng {độ_chính_xác:.0%})")
        else:
            lý_do.append("chưa có dữ liệu để đánh giá độ tin cậy của tín hiệu")
    except Exception:
        điểm = 0.50
        lý_do.append("chưa có dữ liệu để đánh giá độ tin cậy của tín hiệu")

    return {"điểm": round(min(1.0, điểm), 3), "lý_do": lý_do}


def _6_chất_lượng_chỉ_số(
    breadth: Optional[float],
    dominant_contribution_pct: Optional[float],
    top_contribution_pts: Optional[float],
) -> dict:
    """Độ đại diện của VNINDEX cho toàn thị trường.

    Đo 3 khía cạnh:
      1. Độ tập trung (40%): Một nhóm duy nhất có kéo chỉ số không?
      2. Độ lan tỏa (30%): Breadth — bao nhiêu cổ phiếu cùng đi?
      3. Độ phụ thuộc Top10 (30%): Top 10 vốn hóa chi phối ra sao?
    """
    điểm = 0.50
    lý_do = []

    # ── 1. Độ tập trung: dominant_contribution_pct ──
    dc = dominant_contribution_pct if dominant_contribution_pct is not None else 0
    if dc < 20:
        đ_tập_trung = 1.0
        lý_do.append(f"nhóm lớn nhất chỉ đóng góp {dc:.0f}% — thị trường phân tán")
    elif dc < 35:
        đ_tập_trung = 0.8
        lý_do.append(f"nhóm lớn nhất đóng góp {dc:.0f}% — có phân hóa nhưng nhẹ")
    elif dc < 50:
        đ_tập_trung = 0.5
        lý_do.append(f"nhóm lớn nhất đóng góp {dc:.0f}% — thị trường đang bị nhóm trụ chi phối")
    else:
        đ_tập_trung = 0.2
        lý_do.append(f"nhóm lớn nhất đóng góp {dc:.0f}% — chỉ số phụ thuộc quá nhiều vào một nhóm")

    # ── 2. Độ lan tỏa: breadth ──
    br = abs(breadth) if breadth is not None else 0
    if br > 60:
        đ_lan_tỏa = 1.0
        lý_do.append(f"độ rộng {breadth:+.0f}% — thị trường lan tỏa tốt")
    elif br > 45:
        đ_lan_tỏa = 0.8
        lý_do.append(f"độ rộng {breadth:+.0f}% — lan tỏa khá")
    elif br > 30:
        đ_lan_tỏa = 0.5
        lý_do.append(f"độ rộng {breadth:+.0f}% — lan tỏa trung bình")
    else:
        đ_lan_tỏa = 0.2
        lý_do.append(f"độ rộng {breadth:+.0f}% — thị trường hẹp, ít cổ phiếu dẫn dắt")

    # ── 3. Độ phụ thuộc Top10 ──
    tp = top_contribution_pts if top_contribution_pts is not None else 0
    if tp < 10:
        đ_phụ_thuộc = 1.0
        lý_do.append(f"top10 đóng góp {tp:.1f} điểm — không chi phối chỉ số")
    elif tp < 25:
        đ_phụ_thuộc = 0.7
        lý_do.append(f"top10 đóng góp {tp:.1f} điểm — có ảnh hưởng")
    elif tp < 50:
        đ_phụ_thuộc = 0.4
        lý_do.append(f"top10 đóng góp {tp:.1f} điểm — chi phối mạnh chỉ số")
    else:
        đ_phụ_thuộc = 0.1
        lý_do.append(f"top10 đóng góp {tp:.1f} điểm — chỉ số hoàn toàn do top10 quyết định")

    điểm = 0.40 * đ_tập_trung + 0.30 * đ_lan_tỏa + 0.30 * đ_phụ_thuộc

    return {"điểm": round(min(1.0, điểm), 3), "lý_do": lý_do}


# ── Hàm chính ────────────────────────────────────────────

def đánh_giá_độ_tin_cậy(
    anh_chup: Optional[dict] = None,
    dữ_liệu_thị_trường: Optional[dict] = None,
    dữ_liệu_cấu_trúc: Optional[dict] = None,
    cảnh_báo_sớm: Optional[dict] = None,
    target_date: Optional[str] = None,
) -> dict:
    """Tự đánh giá độ tin cậy của quyết định hiện tại.

    Args:
        anh_chup: Ảnh chụp thị trường từ market_snapshot.tao_anh_chup()
                  Nếu có, các tham số khác bị bỏ qua.

    Trả về:
      dict với điểm tin cậy, mức đánh giá, chi tiết từng yếu tố,
      và trạng thái tạm ngưng nếu điểm quá thấp.
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    hôm_nay = target_date

    # ── Nếu có ảnh chụp, dùng nó làm nguồn duy nhất ──
    if anh_chup is not None:
        r = anh_chup.get("regime", {})
        c = anh_chup.get("cau_truc", {})
        ew = anh_chup.get("canh_bao_som", {})
        gi = anh_chup.get("nhom_anh_huong", {})

        trạng_thái = r.get("trang_thái", "N/A")
        điểm_số = r.get("diem_so", 0.5)
        adx = r.get("adx")
        tỷ_lệ_atr = r.get("ty_le_atr")
        điểm_v = r.get("diem_v")
        rad_kích_hoạt = r.get("rad_kich_hoat", False)

        trạng_thái_cấu_trúc = c.get("trang_thai", "N/A")
        số_trụ = c.get("so_tru", 0)
        entropy = c.get("entropy")

        mức_cảnh_báo = ew.get("cap_do", "BÌNH_THƯỜNG")

        # Độ đại diện chỉ số — từ Group Influence Engine
        breadth = gi.get("breadth")
        dominant_contribution_pct = gi.get("dominant_contribution_pct")
        top_contribution_pts = gi.get("top_contribution_pts")
        chất_lượng_chỉ_số_điểm = gi.get("chat_luong_chi_so")
    else:
        # ── Fallback: đọc riêng lẻ (cho standalone) ──
        if dữ_liệu_thị_trường is None:
            from src.engine.regime_engine import detect_regime
            dữ_liệu_thị_trường = detect_regime(target_date=target_date)

        if dữ_liệu_cấu_trúc is None:
            from src.engine.structural_detector import detect_cau_truc
            dữ_liệu_cấu_trúc = detect_cau_truc(target_date=target_date) or {}

        if cảnh_báo_sớm is None:
            from src.services.early_warning_engine import build_early_warning
            cảnh_báo_sớm = build_early_warning(regime_data=dữ_liệu_thị_trường)

        trạng_thái = dữ_liệu_thị_trường.get("status", "N/A")
        điểm_số = dữ_liệu_thị_trường.get("regime_score", 0.5)
        chi_tiết = dữ_liệu_thị_trường.get("details", {})
        adx = chi_tiết.get("adx")
        tỷ_lệ_atr = chi_tiết.get("atr_ratio")
        điểm_v = chi_tiết.get("v_score")
        rad = dữ_liệu_thị_trường.get("rad", {})
        rad_kích_hoạt = rad.get("activated", False)

        trạng_thái_cấu_trúc = dữ_liệu_cấu_trúc.get("trang_thai", "N/A")
        số_trụ = dữ_liệu_cấu_trúc.get("so_tru_ok", 0)
        entropy = dữ_liệu_cấu_trúc.get("entropy")

        mức_cảnh_báo = cảnh_báo_sớm.get("cap_do_ma", "BÌNH_THƯỜNG") if cảnh_báo_sớm else "BÌNH_THƯỜNG"

        # Fallback: đọc group influence từ file
        try:
            gi_fb = _đọc_json("group_influence_report.json") or {}
            breadth = gi_fb.get("real_market_breadth")
            dominant_contribution_pct = gi_fb.get("dominant_contribution_pct")
            vnindex_ex_top10 = gi_fb.get("vnindex_ex_top10")
            vnindex_actual = gi_fb.get("vnindex_actual")
            top_contribution_pts = (
                round(vnindex_actual - vnindex_ex_top10, 2)
                if vnindex_actual and vnindex_ex_top10 else None
            )
        except Exception:
            breadth = None
            dominant_contribution_pct = None
            top_contribution_pts = None

    # ── Đánh giá từng yếu tố ──
    yt_1 = _1_thị_trường_rõ_ràng(trạng_thái, điểm_số, adx, rad_kích_hoạt)
    yt_2 = _2_cấu_trúc_lành_mạnh(trạng_thái_cấu_trúc, số_trụ, entropy)
    yt_3 = _3_tín_hiệu_đồng_thuận(trạng_thái, trạng_thái_cấu_trúc, mức_cảnh_báo, entropy)
    yt_4 = _4_biến_động_ổn_định(tỷ_lệ_atr, điểm_v)
    yt_5 = _5_tín_hiệu_đáng_tin(trạng_thái)
    yt_6 = _6_chất_lượng_chỉ_số(breadth, dominant_contribution_pct, top_contribution_pts)

    # ── Tính điểm tổng hợp ──
    điểm_tin_cậy = (
        TRỌNG_SỐ["thị_trường_rõ_ràng"] * yt_1["điểm"]
        + TRỌNG_SỐ["cấu_trúc_lành_mạnh"] * yt_2["điểm"]
        + TRỌNG_SỐ["tín_hiệu_đồng_thuận"] * yt_3["điểm"]
        + TRỌNG_SỐ["chất_lượng_chỉ_số"] * yt_6["điểm"]
        + TRỌNG_SỐ["biến_động_ổn_định"] * yt_4["điểm"]
        + TRỌNG_SỐ["tín_hiệu_đáng_tin"] * yt_5["điểm"]
    )
    # ── Hệ số phạt cấu trúc (phi tuyến) ──
    he_so_phat_cau_truc = 1.0
    if trạng_thái_cấu_trúc == "VỠ CẤU TRÚC" and số_trụ <= 1:
        he_so_phat_cau_truc = 0.70
        điểm_tin_cậy *= he_so_phat_cau_truc

    điểm_tin_cậy = round(max(0.0, min(1.0, điểm_tin_cậy)), 3)
    mức_đánh_giá = _mức_đánh_giá(điểm_tin_cậy)

    # ── Quyết định tạm ngưng kết luận ──
    tạm_ngưng = False
    lý_do_tạm_ngưng = None
    if điểm_tin_cậy < 0.30:
        tạm_ngưng = True
        lý_do_tạm_ngưng = "độ tin cậy xuống dưới ngưỡng an toàn"
    elif entropy is not None and entropy > 2.5 and số_trụ <= 1:
        tạm_ngưng = True
        lý_do_tạm_ngưng = "thị trường biến động bất thường — cấu trúc quá yếu"
    elif yt_6["điểm"] < 0.2:
        tạm_ngưng = True
        lý_do_tạm_ngưng = "chỉ số không còn đại diện cho thị trường — điểm chất lượng chỉ số quá thấp"

    # ── Tổng hợp lý do ──
    tất_cả_lý_do = []
    for yt, tên in [(yt_1, "thị_trường"), (yt_2, "cấu_trúc"), (yt_3, "đồng_thuận"),
                    (yt_4, "biến_động"), (yt_5, "tín_hiệu"), (yt_6, "chất_lượng")]:
        for ld in yt["lý_do"]:
            if ld not in tất_cả_lý_do:
                tất_cả_lý_do.append(ld)

    if he_so_phat_cau_truc < 1.0:
        tất_cả_lý_do.append(
            f"cấu trúc vỡ — phạt phi tuyến (×{he_so_phat_cau_truc})"
        )

    # ── Kết luận bằng tiếng Việt ──
    if mức_đánh_giá == "CAO":
        kết_luận = "Các dấu hiệu khá thống nhất. Hệ thống tương đối tự tin với kết luận hiện tại."
    elif mức_đánh_giá == "TRUNG_BINH":
        kết_luận = "Hệ thống có thể đưa ra nhận định, nhưng mức chắc chắn chưa cao."
    else:
        kết_luận = "Thị trường quá nhiễu hoặc dữ liệu chưa đủ rõ. Không nên dựa hoàn toàn vào kết luận hiện tại."

    kết_quả = {
        "ngày": hôm_nay,
        "điểm_tin_cậy": điểm_tin_cậy,
        "mức_đánh_giá": mức_đánh_giá,
        "mức_đánh_giá_chữ": _mức_ra_chữ(mức_đánh_giá),
        "chi_tiết_yếu_tố": {
            "thị_trường_rõ_ràng": yt_1,
            "cấu_trúc_lành_mạnh": yt_2,
            "tín_hiệu_đồng_thuận": yt_3,
            "chất_lượng_chỉ_số": yt_6,
            "biến_động_ổn_định": yt_4,
            "tín_hiệu_đáng_tin": yt_5,
        },
        "trọng_số": TRỌNG_SỐ,
        "hệ_số_phạt_cấu_trúc": he_so_phat_cau_truc,
        "lý_do": tất_cả_lý_do,
        "tạm_ngưng_kết_luận": tạm_ngưng,
        "lý_do_tạm_ngưng": lý_do_tạm_ngưng,
        "kết_luận": kết_luận,
    }

    # ── Lưu file ──
    thư_mục_xuất = Path(src.config.DATA_DIR) / "output"
    thư_mục_xuất.mkdir(parents=True, exist_ok=True)
    with open(thư_mục_xuất / "danh_gia_tin_cay.json", "w", encoding="utf-8") as f:
        json.dump(kết_quả, f, indent=2, ensure_ascii=False)

    return kết_quả


# ── In báo cáo ──────────────────────────────────────────

def in_báo_cáo(kết_quả: dict):
    """In báo cáo độ tin cậy cho người dùng xem."""
    biểu_tượng = {"CAO": "🟢", "TRUNG_BINH": "🟡", "THAP": "🔴"}
    icon = biểu_tượng.get(kết_quả.get("mức_đánh_giá", ""), "⚪")

    print("\n" + "=" * 55)
    print("  ĐÁNH GIÁ ĐỘ TIN CẬY")
    print("=" * 55)
    print(f"\n  Độ tin cậy hiện tại: {kết_quả.get('điểm_tin_cậy', 0):.1%}")
    print(f"  Mức đánh giá:        {icon} {kết_quả.get('mức_đánh_giá_chữ', 'N/A')}")
    print("\n  Các yếu tố ảnh hưởng:")
    for lđ in kết_quả.get("lý_do", []):
        lđ_viết_hoa = lđ[0].upper() + lđ[1:] if lđ else ""
        print(f"    • {lđ_viết_hoa}")

    if kết_quả.get("tạm_ngưng_kết_luận"):
        print(f"\n  ⚠ Tạm ngưng kết luận")
        lý_do = kết_quả.get("lý_do_tạm_ngưng", "")
        if lý_do:
            lý_do_viết_hoa = lý_do[0].upper() + lý_do[1:]
            print(f"    Lý do: {lý_do_viết_hoa}")

    print(f"\n  Kết luận:")
    print(f"    {kết_quả.get('kết_luận', '')}")
    print("=" * 55)


if __name__ == "__main__":
    kq = đánh_giá_độ_tin_cậy()
    in_báo_cáo(kq)
