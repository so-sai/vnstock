import logging
import sys
from datetime import datetime
from pathlib import Path

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


def _safe(val, default="không rõ"):
    if val is None:
        return default
    return val


def _xu_huong_label(status):
    mapping = {
        "TRENDING": "tăng",
        "MỞ_RỘNG_TÍCH_CỰC": "tăng mạnh",
        "CRISIS": "giảm",
        "CRISIS_WARNING": "giảm",
        "RANGING": "đi ngang",
    }
    return mapping.get(status, "đi ngang")


def _muc_do_xu_huong(adx):
    if adx is None:
        return "không rõ"
    if adx > 30:
        return "mạnh"
    if adx > 20:
        return "vừa"
    return "yếu"


def _tam_ly_label(risk_appetite):
    if not risk_appetite:
        return "không rõ"
    tamly = str(risk_appetite).upper()
    if "MỞ_RỘNG" in tamly:
        return "tích cực"
    if "ĐÓNG" in tamly or "THẬN_TRỌNG" in tamly:
        return "thận trọng"
    return "trung tính"


def _do_rong_label(health_score):
    if health_score is None:
        return "không rõ"
    if health_score > 60:
        return "tốt"
    if health_score > 35:
        return "trung bình"
    return "xấu"


def _bieu_dong_label(atr_ratio):
    if atr_ratio is None:
        return "trung bình"
    if atr_ratio > 1.8:
        return "cao"
    if atr_ratio > 1.3:
        return "trung bình"
    return "thấp"


def _lan_toa_label(flow_status, leading_count):
    if not flow_status:
        return "không rõ"
    trangthai = str(flow_status).upper()
    if trangthai in ("MỞ_RỘNG", "MỞ_RỘNG_TÍCH_CỰC") and leading_count >= 3:
        return "rộng"
    if trangthai in ("MỞ_RỘNG", "DUY_TRÌ") and leading_count >= 2:
        return "hẹp"
    return "hẹp"


def _gold_premium_label(premium_pct):
    if premium_pct is None:
        return "không rõ"
    if premium_pct > 5:
        return "cao bất thường"
    if premium_pct > 3:
        return "hơi cao"
    return "bình thường"


def _gold_premium_giai_thich(premium_pct, premium_regime):
    if premium_pct is None:
        return ""
    phan = []
    phan.append(f"Vàng trong nước đang cao hơn thế giới {premium_pct:.1f}%")
    if premium_pct > 5:
        phan.append("người người đang tìm nơi trú ẩn")
    elif premium_pct > 3:
        phan.append("tâm lý phòng thủ bắt đầu xuất hiện")
    return ", ".join(phan)


def _xep_loai_rui_ro(health_score, atr_ratio, risk_appetite, flow_status):
    diem = 0
    if health_score is not None and health_score < 35:
        diem += 2
    elif health_score is not None and health_score < 55:
        diem += 1
    if atr_ratio is not None and atr_ratio > 1.8:
        diem += 2
    elif atr_ratio is not None and atr_ratio > 1.3:
        diem += 1
    if risk_appetite:
        tamly = str(risk_appetite).upper()
        if "ĐÓNG" in tamly:
            diem += 2
        elif "THẬN_TRỌNG" in tamly:
            diem += 1
    if flow_status:
        trangthai = str(flow_status).upper()
        if "THU_HẸP" in trangthai:
            diem += 2
        elif "PHÂN_HÓA" in trangthai:
            diem += 1
    if diem >= 5:
        return "cao"
    if diem >= 3:
        return "trung bình"
    return "thấp"


# ============================================================
# BILINGUAL VI→EN — bảng dịch cho renderer báo cáo hằng ngày
# WHY "VI (EN)": end-user đọc Việt trước, EN tham khảo cho dev/log.
# ============================================================
_BI = {
    # Section titles
    "TỔNG QUAN THỊ TRƯỜNG": "MARKET OVERVIEW",
    "DÒNG TIỀN ĐANG ĐI ĐÂU": "WHERE IS THE MONEY FLOWING",
    "MỨC ĐỘ RỦI RO THỊ TRƯỜNG": "MARKET RISK LEVEL",
    "TÍN HIỆU ĐẶC BIỆT": "SPECIAL SIGNALS",
    "KẾT LUẬN HÀNH VI": "ACTION CONCLUSION",
    "CẢNH BÁO SỚM CHUYỂN PHA": "EARLY PHASE-SHIFT WARNING",
    "XÁC NHẬN CHUYỂN PHA THẬT": "PHASE-SHIFT CONFIRMATION",
    "HÔM NAY": "TODAY",
    "PHÂN LOẠI DANH MỤC CỔ PHIẾU": "PORTFOLIO CLASSIFICATION",
    # Field labels
    "Xu hướng chung": "Overall trend",
    "Mức độ rõ xu hướng": "Trend clarity",
    "Tâm lý thị trường": "Market sentiment",
    "Điểm regime": "Regime score",
    "Nhóm mạnh nhất": "Strongest groups",
    "Nhóm yếu nhất": "Weakest groups",
    "Tập trung vài nhóm": "Concentrated",
    "Trạng thái": "Status",
    "Độ rộng thị trường": "Market breadth",
    "Dòng tiền lan tỏa": "Flow breadth",
    "Biến động giá": "Price volatility",
    "Sức khỏe độ rộng": "Breadth health",
    "Xếp loại": "Risk rating",
    "Hành vi khuyến nghị": "Recommended action",
    "Cảnh báo": "Warning",
    "Mức độ": "Level",
    "Kết luận": "Conclusion",
    "Số nhóm xác nhận": "Groups confirmed",
    "Ngành": "Sectors",
    # Value words
    "tăng": "rising",
    "tăng mạnh": "strongly rising",
    "giảm": "falling",
    "đi ngang": "sideways",
    "mạnh": "strong",
    "vừa": "moderate",
    "yếu": "weak",
    "tích cực": "positive",
    "thận trọng": "cautious",
    "trung tính": "neutral",
    "tốt": "good",
    "trung bình": "average",
    "xấu": "poor",
    "cao": "high",
    "thấp": "low",
    "rộng": "broad",
    "hẹp": "narrow",
    "Có": "Yes",
    "Không": "No",
    "không rõ": "unknown",
    # Behavior / conclusions
    "Có thể tham gia": "Can participate",
    "Quan sát": "Watch",
    "Thận trọng": "Caution",
    "Nên đứng ngoài": "Stay out",
    "Bình thường": "Normal",
    "Nhiễu": "Noise",
    "An toàn": "Safe",
}


def _resolve_lang_mode(lang_mode: str) -> str:
    """Resolve 'auto' → annotated/compact dựa trên terminal width."""
    if lang_mode == "auto":
        try:
            from src.core.canonical_output_adapter import _detect_lang_mode
            return _detect_lang_mode("auto")
        except Exception:
            return "annotated"
    return lang_mode


def _bi(vi: str, lang_mode: str = "annotated") -> str:
    """Localize 1 label/value: full=VI, compact=EN, annotated/auto="VI (EN)"."""
    mode = _resolve_lang_mode(lang_mode)
    en = _BI.get(vi)
    if not en or en == vi:
        return vi
    if mode == "compact":
        return en
    if mode == "full":
        return vi
    return f"{vi} ({en})"


def _ket_luan_hanh_vi(regime_status, xep_loai, health_score, risk_appetite):
    tamly = str(risk_appetite).upper() if risk_appetite else ""
    if "CRISIS" in str(regime_status).upper():
        return "Nên đứng ngoài"
    if xep_loai == "cao":
        return "Thận trọng"
    if "ĐÓNG" in tamly:
        return "Nên đứng ngoài"
    if "MỞ_RỘNG" in tamly and health_score is not None and health_score > 55:
        return "Có thể tham gia"
    if "THẬN_TRỌNG" in tamly:
        return "Quan sát"
    if xep_loai == "trung bình":
        return "Quan sát"
    return "Quan sát"


def build_daily_report():
    result = {
        "timestamp": datetime.now().isoformat(),
        "title": "BÁO CÁO THỊ TRƯỜNG HẰNG NGÀY",
    }

    # 1. Regime
    try:
        from src.engine.regime_engine import detect_regime
        regime = detect_regime()
    except Exception as e:
        logger.warning("Không đọc được regime: %s", e)
        regime = {}

    regime_status = regime.get("status", "UNKNOWN")
    regime_score = regime.get("regime_score", 0.0)
    details = regime.get("details", {})
    rad = regime.get("rad", {})
    breadth_pct = details.get("breadth_pct")
    adx = details.get("adx")
    atr_ratio = details.get("atr_ratio")
    delta_adx = rad.get("signals", {}).get("delta_adx")
    v_breadth = rad.get("signals", {}).get("v_breadth")
    gold_premium_from_rad = rad.get("signals", {}).get("gold_premium")
    rad_activated = rad.get("activated", False)

    # 2. Market state
    try:
        from src.core.market_state_coordinator import build_market_state
        state = build_market_state()
    except Exception as e:
        logger.warning("Không đọc được market state: %s", e)
        state = {}

    meta = state.get("meta_state", {})
    flow = state.get("flow_state", {})
    breadth_state = state.get("breadth_state", {})
    risk_state = state.get("risk_state", {})

    market_phase = meta.get("market_phase", "KHÔNG_XÁC_ĐỊNH")
    risk_appetite = meta.get("risk_appetite", "TRUNG_TÍNH")
    dominant_flow = meta.get("dominant_flow", "KHÔNG_RÕ")
    liquidity_condition = meta.get("liquidity_condition", "TRUNG_TÍNH")
    health_score = breadth_state.get("health_score")
    leading_sectors = flow.get("leading_sectors", [])
    lagging_sectors = flow.get("lagging_sectors", [])

    # 3. Gold
    gold_premium_pct = gold_premium_from_rad
    gold_premium_regime = None
    try:
        from core.macro.gold_spread_engine import analyze_domestic_premium
        gp = analyze_domestic_premium()
        if isinstance(gp, dict):
            gold_premium_pct = gold_premium_pct or gp.get("premium_pct")
            gold_premium_regime = gp.get("premium_regime", "PREMIUM_NORMAL")
    except Exception as e:
        logger.warning("Không đọc được gold premium: %s", e)

    try:
        from src.services.macro.gold_world_service import fetch_world_gold_live
        world_gold = fetch_world_gold_live()
    except Exception:
        world_gold = None

    # ========================================
    # PHẦN 1: TỔNG QUAN THỊ TRƯỜNG
    # ========================================
    xu_huong = _xu_huong_label(regime_status)
    muc_do = _muc_do_xu_huong(adx)
    tam_ly = _tam_ly_label(risk_appetite)

    tong_quan = {
        "xu_huong_chung": xu_huong,
        "muc_do_ro_xu_huong": muc_do,
        "tam_ly_thi_truong": tam_ly,
        "diem_regime": round(regime_score, 2),
        "adx": adx,
        "ket_luan": f"Thị trường hôm nay đang ở trạng thái: {xu_huong}, tâm lý {tam_ly}",
    }

    # ========================================
    # PHẦN 2: DÒNG TIỀN ĐANG ĐI ĐÂU
    # ========================================
    nhom_manh = leading_sectors[:3] if leading_sectors else []
    nhom_yeu = lagging_sectors[:3] if lagging_sectors else []
    tap_trung = "Có" if len(nhom_manh) <= 2 else "Không"

    dong_tien = {
        "nhom_manh_nhat": nhom_manh,
        "nhom_yeu_nhat": nhom_yeu,
        "tap_trung_vai_nhom": tap_trung,
        "trang_thai_dong_tien": flow.get("status", "KHÔNG_RÕ"),
        "ket_luan": f"Dòng tiền đang tập trung vào: {', '.join(nhom_manh) if nhom_manh else 'không rõ'}",
    }

    # ========================================
    # PHẦN 3: MỨC ĐỘ RỦI RO
    # ========================================
    do_rong = _do_rong_label(health_score)
    lan_toa = _lan_toa_label(flow.get("status"), len(leading_sectors))
    bien_dong = _bieu_dong_label(atr_ratio)
    xep_loai = _xep_loai_rui_ro(health_score, atr_ratio, risk_appetite, flow.get("status"))

    rui_ro = {
        "do_rong_thi_truong": do_rong,
        "dong_tien_lan_toa": lan_toa,
        "bien_dong_gia": bien_dong,
        "xep_loai": xep_loai,
        "diem_suc_khoe_do_rong": health_score,
        "ket_luan": f"Rủi ro hiện tại: {xep_loai}",
    }

    # ========================================
    # PHẦN 4: TÍN HIỆU ĐẶC BIỆT
    # ========================================
    tin_hieu = []
    try:
        from src.services.safe_haven_sentiment import phan_vung_tam_ly
        vung_tam_ly = phan_vung_tam_ly(
            gold_premium_pct=gold_premium_pct,
            gold_premium_regime=gold_premium_regime,
            market_state=state,
            regime_data=regime,
        )
        if vung_tam_ly["vung"] != "BÌNH_THƯỜNG":
            tin_hieu.append({
                "loai": "tâm lý trú ẩn",
                "noi_dung": f"{vung_tam_ly['ky_hieu']} {vung_tam_ly['ten']}: {vung_tam_ly['mo_ta']}",
                "muc_do": "cao" if vung_tam_ly["vung"] in ("HOẢNG_LOẠN", "PHÒNG_THỦ_RÕ_RỆT") else "trung bình",
            })
            for ld in vung_tam_ly["ly_do"][:2]:
                tin_hieu.append({"loai": "nguyên nhân", "noi_dung": f"▸ {ld}", "muc_do": "thông tin"})
    except Exception:
        if gold_premium_pct is not None and gold_premium_pct > 3:
            tin_hieu.append({
                "loai": "vàng",
                "noi_dung": _gold_premium_giai_thich(gold_premium_pct, gold_premium_regime),
                "muc_do": "cao" if gold_premium_pct > 5 else "trung bình",
            })
    if rad_activated:
        tin_hieu.append({
            "loai": "rad",
            "noi_dung": "Hệ thống phát hiện thị trường đang chuyển trạng thái",
            "muc_do": "cao",
        })
    if delta_adx is not None and abs(delta_adx) > 5:
        tin_hieu.append({
            "loai": "biến động",
            "noi_dung": f"Biến động thị trường đang thay đổi nhanh (ΔADX = {delta_adx:+.1f})",
            "muc_do": "trung bình",
        })
    if v_breadth is not None and v_breadth < -3:
        tin_hieu.append({
            "loai": "độ rộng",
            "noi_dung": "Số cổ phiếu tăng đang giảm nhanh",
            "muc_do": "cao",
        })
    if world_gold and isinstance(world_gold, dict):
        giatri = world_gold.get("price")
        thaydoi = world_gold.get("change_pct")
        if giatri is not None:
            gold_text = f"Vàng thế giới: {giatri:.0f} USD"
            if thaydoi is not None:
                gold_text += f" ({thaydoi:+.2f}%)"
            tin_hieu.append({
                "loai": "vàng thế giới",
                "noi_dung": gold_text,
                "muc_do": "thông tin",
            })

    # ========================================
    # PHẦN 5: KẾT LUẬN HÀNH VI
    # ========================================
    hanh_vi = _ket_luan_hanh_vi(regime_status, xep_loai, health_score, risk_appetite)

    # ========================================
    # PHẦN 6: CẢNH BÁO SỚM CHUYỂN PHA
    # ========================================
    try:
        from src.services.early_warning_engine import build_early_warning
        canh_bao = build_early_warning(
            regime_data=regime,
            market_state=state,
            gold_premium_pct=gold_premium_pct,
            gold_premium_regime=gold_premium_regime,
        )
    except Exception as e:
        logger.warning("Không tạo được cảnh báo sớm: %s", e)
        canh_bao = {
            "cap_do_ma": "BÌNH_THƯỜNG",
            "cap_do_ky_hieu": "🟡",
            "cap_do_tieng_viet": "Bình thường",
            "tong_diem": 0,
            "canh_bao": [],
        }

    # ========================================
    # PHẦN 7: XÁC NHẬN CHUYỂN PHA THẬT
    # ========================================
    try:
        from src.services.phase_transition_confirmation import xac_nhan_chuyen_pha
        xac_nhan = xac_nhan_chuyen_pha(
            regime_data=regime,
            market_state=state,
            gold_premium_pct=gold_premium_pct,
            gold_premium_regime=gold_premium_regime,
        )
    except Exception as e:
        logger.warning("Không xác nhận được chuyển pha: %s", e)
        xac_nhan = {
            "ket_luan": "NHIỄU",
            "ten": "Nhiễu",
            "ky_hieu": "🔍",
            "so_nhom_dat": 0,
        }

    # ========================================
    # PHẦN 8: QUYẾT ĐỊNH CUỐI CÙNG
    # ========================================
    quyet_dinh = {}
    try:
        from src.services.decision_layer import quyet_dinh_cuoi_cung
        quyet_dinh = quyet_dinh_cuoi_cung({
            "ket_luan_hanh_vi": hanh_vi,
            "canh_bao_som": canh_bao,
            "xac_nhan_chuyen_pha": xac_nhan,
            "tin_hieu_dac_biet": tin_hieu,
            "rui_ro": rui_ro,
        })
    except Exception as e:
        logger.warning("Không tạo được quyết định cuối cùng: %s", e)

    # ========================================
    # PHẦN 9: PHÂN LOẠI DANH MỤC CỔ PHIẾU
    # ========================================
    danh_sach_mac_dinh = ["HPG", "MBB", "GMD", "STB", "VTO", "REE", "DP3",
                          "VTP", "MWG", "SSI", "BSR", "QNS", "TLG", "SBT",
                          "FPT", "DGC", "VGI", "VIB", "TCB", "ACB"]
    phan_loai = []
    try:
        from src.services.portfolio_decision_layer import phan_loai_danh_muc
        phan_loai = phan_loai_danh_muc(danh_sach_mac_dinh, {
            "canh_bao_som": canh_bao,
            "xac_nhan_chuyen_pha": xac_nhan,
            "quyet_dinh_cuoi_cung": quyet_dinh,
            "rui_ro": rui_ro,
            "dong_tien": dong_tien,
        })
    except Exception as e:
        logger.warning("Không phân loại được danh mục: %s", e)

    # Gop
    result["tong_quan"] = tong_quan
    result["dong_tien"] = dong_tien
    result["rui_ro"] = rui_ro
    result["tin_hieu_dac_biet"] = tin_hieu
    result["ket_luan_hanh_vi"] = hanh_vi
    result["canh_bao_som"] = canh_bao
    result["xac_nhan_chuyen_pha"] = xac_nhan
    result["quyet_dinh_cuoi_cung"] = quyet_dinh
    result["phan_loai_danh_muc"] = phan_loai
    result["_gold_premium_raw"] = gold_premium_pct

    return result


def in_bao_cao(report, lang_mode: str = "annotated"):
    """In báo cáo hằng ngày ra console.

    lang_mode: 'full'=Tiếng Việt, 'compact'=English, 'annotated'/'auto'
    = song ngữ Việt-Anh "VI (EN)" (mặc định).
    """
    gach = "=" * 60

    print()
    print(gach)
    print(f"  {report['title']}")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(gach)

    # 1. TỔNG QUAN
    tq = report["tong_quan"]
    print(f"\n  1. {_bi('TỔNG QUAN THỊ TRƯỜNG', lang_mode)}")
    print("  --")
    print(f"  {_bi('Xu hướng chung', lang_mode):{24}s} {_bi(tq['xu_huong_chung'], lang_mode)}")
    print(f"  {_bi('Mức độ rõ xu hướng', lang_mode):{24}s} {_bi(tq['muc_do_ro_xu_huong'], lang_mode)}")
    print(f"  {_bi('Tâm lý thị trường', lang_mode):{24}s} {_bi(tq['tam_ly_thi_truong'], lang_mode)}")
    if tq.get("diem_regime") is not None:
        print(f"  {_bi('Điểm regime', lang_mode):{24}s} {tq['diem_regime']}")
    if tq.get("adx") is not None:
        print(f"  ADX:                  {tq['adx']}")
    print(f"  → {tq['ket_luan']}")

    # 2. DÒNG TIỀN
    dt = report["dong_tien"]
    print(f"\n  2. {_bi('DÒNG TIỀN ĐANG ĐI ĐÂU', lang_mode)}")
    print("  --")
    if dt["nhom_manh_nhat"]:
        print(f"  {_bi('Nhóm mạnh nhất', lang_mode):{24}s} {', '.join(dt['nhom_manh_nhat'])}")
    else:
        print(f"  {_bi('Nhóm mạnh nhất', lang_mode):{24}s} {_bi('không rõ', lang_mode)}")
    if dt["nhom_yeu_nhat"]:
        print(f"  {_bi('Nhóm yếu nhất', lang_mode):{24}s} {', '.join(dt['nhom_yeu_nhat'])}")
    print(f"  {_bi('Tập trung vài nhóm', lang_mode):{24}s} {_bi(dt['tap_trung_vai_nhom'], lang_mode)}")
    print(f"  {_bi('Trạng thái', lang_mode):{24}s} {dt['trang_thai_dong_tien']}")
    print(f"  → {dt['ket_luan']}")

    # 3. RỦI RO
    rr = report["rui_ro"]
    print(f"\n  3. {_bi('MỨC ĐỘ RỦI RO THỊ TRƯỜNG', lang_mode)}")
    print("  --")
    print(f"  {_bi('Độ rộng thị trường', lang_mode):{24}s} {_bi(rr['do_rong_thi_truong'], lang_mode)}")
    print(f"  {_bi('Dòng tiền lan tỏa', lang_mode):{24}s} {_bi(rr['dong_tien_lan_toa'], lang_mode)}")
    print(f"  {_bi('Biến động giá', lang_mode):{24}s} {_bi(rr['bien_dong_gia'], lang_mode)}")
    if rr.get("diem_suc_khoe_do_rong") is not None:
        print(f"  {_bi('Sức khỏe độ rộng', lang_mode):{24}s} {rr['diem_suc_khoe_do_rong']}")
    print(f"  {_bi('Xếp loại', lang_mode):{24}s} {_bi(rr['xep_loai'], lang_mode)}")
    print(f"  → {rr['ket_luan']}")

    # 4. TÍN HIỆU ĐẶC BIỆT
    tin_hieu = report["tin_hieu_dac_biet"]
    print(f"\n  4. {_bi('TÍN HIỆU ĐẶC BIỆT', lang_mode)}")
    print("  --")
    if tin_hieu:
        for th in tin_hieu:
            muc = th.get("muc_do", "")
            if muc == "cao":
                ky_hieu = "⚠"
            elif muc == "trung bình":
                ky_hieu = "▸"
            else:
                ky_hieu = "•"
            print(f"  {ky_hieu} {th['noi_dung']}")
    else:
        print(f"  {_bi('Không có tín hiệu đặc biệt nào.', lang_mode)}")

    # 5. KẾT LUẬN HÀNH VI
    print(f"\n  5. {_bi('KẾT LUẬN HÀNH VI', lang_mode)}")
    print("  --")
    hanh_vi = report["ket_luan_hanh_vi"]
    ky_hieu_map = {
        "Có thể tham gia": "🟢",
        "Quan sát": "🟡",
        "Thận trọng": "🟠",
        "Nên đứng ngoài": "🔴",
    }
    kh = ky_hieu_map.get(hanh_vi, "•")
    print(f"  {kh} {_bi('Hành vi khuyến nghị', lang_mode)}: {_bi(hanh_vi, lang_mode)}")

    # 6. CẢNH BÁO SỚM
    cb = report.get("canh_bao_som", {})
    print(f"\n  6. {_bi('CẢNH BÁO SỚM CHUYỂN PHA', lang_mode)}")
    print("  --")
    cap_do = cb.get("cap_do_tieng_viet", "Bình thường")
    ky_hieu_cb = cb.get("cap_do_ky_hieu", "🟡")
    print(f"  {ky_hieu_cb} {_bi('Cảnh báo', lang_mode)}: {_bi(cap_do, lang_mode)}")
    cac_canh_bao = cb.get("canh_bao", [])
    if cac_canh_bao:
        for c in cac_canh_bao:
            print(f"  ▸ {c}")
    print(f"  → {_bi('Mức độ', lang_mode)}: {_bi(cap_do, lang_mode)}")

    # 7. XÁC NHẬN CHUYỂN PHA
    xn = report.get("xac_nhan_chuyen_pha", {})
    print(f"\n  7. {_bi('XÁC NHẬN CHUYỂN PHA THẬT', lang_mode)}")
    print("  --")
    print(f"  {xn.get('ky_hieu', '🔍')} {_bi('Kết luận', lang_mode)}: {_bi(xn.get('ten', 'Nhiễu'), lang_mode)}")
    print(f"  {_bi('Số nhóm xác nhận', lang_mode)}: {xn.get('so_nhom_dat', 0)}/3")
    chi_tiet = xn.get("chi_tiet", {})
    for ten_nhom, tt in chi_tiet.items():
        dat = "✔" if tt.get("dat") else "✘"
        diem = tt.get("diem", 0)
        ten_hien = {"gia_va_xu_huong": "Giá và xu hướng",
                     "dong_tien": "Dòng tiền",
                     "hanh_vi_phong_thu": "Hành vi phòng thủ"}.get(ten_nhom, ten_nhom)
        print(f"  {dat} {_bi(ten_hien, lang_mode)} (điểm: {diem})")
        for ld in tt.get("ly_do", [])[:2]:
            print(f"    ▸ {ld}")
    print(f"  → {xn.get('mo_ta', '')}")

    # 8. QUYẾT ĐỊNH CUỐI CÙNG
    qd = report.get("quyet_dinh_cuoi_cung", {})
    print(f"\n{gach}")
    print(f"  {qd.get('ky_hieu', '🟢')}  {_bi('HÔM NAY', lang_mode)}: {_bi(qd.get('ten', 'An toàn'), lang_mode)}")
    print(f"  {qd.get('mo_ta', '')}")
    print(gach)

    # 9. PHÂN LOẠI DANH MỤC
    pl = report.get("phan_loai_danh_muc", [])
    if pl:
        print(f"\n  9. {_bi('PHÂN LOẠI DANH MỤC CỔ PHIẾU', lang_mode)}")
        print("  --")
        nhom_theo_ma = {}
        for item in pl:
            ma = item.get("ma", "THEO_DÕI")
            nhom_theo_ma.setdefault(ma, []).append(item)
        thu_tu = ["CÓ_THỂ_THAM_GIA", "THEO_DÕI", "HẠN_CHẾ_RỦI_RO", "TRÁNH_XA"]
        for ma in thu_tu:
            ds = nhom_theo_ma.get(ma, [])
            if ds:
                kh = ds[0].get("ky_hieu", "•")
                ten = ds[0].get("hanh_dong", "")
                symbols = [x["symbol"] for x in ds]
                sectors = ", ".join(sorted(set(x["sector_vn"] for x in ds)))
                print(f"  {kh} {_bi(ten, lang_mode)}: {', '.join(symbols)}")
                print(f"     {_bi('Ngành', lang_mode)}: {sectors}")
        print(f"\n{gach}")


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        if isinstance(sys.stdout, io.TextIOWrapper):
            if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
                try:
                    sys.stdout.reconfigure(encoding='utf-8')
                except Exception:
                    pass
        elif hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    bao_cao = build_daily_report()
    in_bao_cao(bao_cao)
