"""localization.py — Bộ quản lý song ngữ tập trung cho CLI

Usage:
  from src.utils.localization import translate, localize_state, localize_phase, detect_terminal_utf8

  label = translate("state", lang="vi")
  state_vi = localize_state("RANGING")
  phase_vi = localize_phase("MONITORING")
  can_utf8 = detect_terminal_utf8()
"""
import io
import sys
import warnings

# ============================================================
# TRANSLATIONS DICTIONARY
# ============================================================
TRANSLATIONS = {
    "vi": {
        # Macro Governor
        "state": "Trạng thái",
        "macro_risk": "Rủi ro Vĩ mô",
        "governor_conf": "Độ tin cậy",
        "hdr_global": "HDR Toàn cầu",
        "fx_risk_premium": "Phần bù Rủi ro Tỷ giá",
        "tier1_gov": "Governor Cấp 1",

        # Per-Symbol Absorption
        "symbol": "Mã CP",
        "target_date": "Ngày phân tích",
        "b_phase": "Pha Trạng thái",
        "hdr_label": "HDR",
        "status": "Trạng thái",
        "distribution": "Phân phối",

        # VQA
        "classification": "Phân loại VQA",
        "er_label": "ER",
        "vsi_label": "VSI",
        "volume_zscore": "Z-Score KL",
        "aq_label": "AQ",
        "price_change": "Biến động giá",
        "slippage": "Slippage",
        "vpoc_distance": "Khoảng cách VPOC",
        "domestic_absorb": "Hấp thụ nội",
        "foreign_net": "GT ròng NN",

        # HDR Unlock
        "hdr_current": "HDR Hiện tại",
        "hdr_target": "HDR Mục tiêu",
        "hdr_phase_req": "Pha yêu cầu",

        # Governor states
        "LIQUIDATION_CASCADE": "THANH LÝ THÁC ĐỔ",
        "TURBULENT": "NHIỄU ĐỘNG",
        "RANGING": "DAO ĐỘNG",
        "ACCUMULATION": "TÍCH LŨY",

        # Phases
        "PANIC": "HOẢNG LOẠN",
        "ABSORPTION_ACTIVE": "HẤP THỤ NỘI",
        "EQUILIBRIUM": "CÂN BẰNG",
        "MONITORING": "GIÁM SÁT",
        "MONITORING_VQA_OVERRIDE": "GIÁM SÁT (VQA ghi đè)",
        "UNKNOWN": "CHƯA XÁC ĐỊNH",

        # VQA classifications
        "CASCADE_LIQUIDATION": "THANH LÝ THÁC ĐỔ",
        "MARGIN_AVERAGE_DOWN": "CƯA CHÂN BÀN",
        "INSTITUTIONAL_ACCUMULATION": "TÍCH LŨY TỔ CHỨC",
        "MARKET_MAKING_CHURN": "NHIỄU TẠO LẬP",
        "LOW_LIQUIDITY_NOISE": "NHIỄU THANH KHOẢN",

        # Misc
        "yes": "CÓ",
        "no": "KHÔNG",
        "locked": "Khóa",
        "open": "Mở",
        "cash_only": "Cash-only",
        "unlock": "Mở khóa",
        "phase_required": "Yêu cầu pha",
        "current": "Hiện tại",
        "target": "Mục tiêu",
    },
    "en": {
        # Macro Governor
        "state": "State",
        "macro_risk": "Macro Risk",
        "governor_conf": "Governor Conf",
        "hdr_global": "HDR Global",
        "fx_risk_premium": "FX Risk Premium",
        "tier1_gov": "Tier 1 Governor",

        # Per-Symbol Absorption
        "symbol": "Symbol",
        "target_date": "Target Date",
        "b_phase": "Phase",
        "hdr_label": "HDR",
        "status": "Status",
        "distribution": "Distribution",

        # VQA
        "classification": "VQA Class",
        "er_label": "ER",
        "vsi_label": "VSI",
        "volume_zscore": "Vol Z-Score",
        "aq_label": "AQ",
        "price_change": "Price Change",
        "slippage": "Slippage",
        "vpoc_distance": "VPOC Distance",
        "domestic_absorb": "Dom Absorb",
        "foreign_net": "Foreign Net",

        # HDR Unlock
        "hdr_current": "HDR Current",
        "hdr_target": "HDR Target",
        "hdr_phase_req": "Phase Required",

        # Governor states
        "LIQUIDATION_CASCADE": "LIQUIDATION CASCADE",
        "TURBULENT": "TURBULENT",
        "RANGING": "RANGING",
        "ACCUMULATION": "ACCUMULATION",

        # Phases
        "PANIC": "PANIC",
        "ABSORPTION_ACTIVE": "ABSORPTION_ACTIVE",
        "EQUILIBRIUM": "EQUILIBRIUM",
        "MONITORING": "MONITORING",
        "MONITORING_VQA_OVERRIDE": "MONITORING (VQA)",
        "UNKNOWN": "UNKNOWN",

        # VQA classifications
        "CASCADE_LIQUIDATION": "CASCADE_LIQUIDATION",
        "MARGIN_AVERAGE_DOWN": "MARGIN_AVERAGE_DOWN",
        "INSTITUTIONAL_ACCUMULATION": "INSTITUTIONAL_ACCUMULATION",
        "MARKET_MAKING_CHURN": "MARKET_MAKING_CHURN",
        "LOW_LIQUIDITY_NOISE": "LOW_LIQUIDITY_NOISE",

        # Misc
        "yes": "YES",
        "no": "NO",
        "locked": "Locked",
        "open": "Open",
        "cash_only": "Cash-only",
        "unlock": "Unlock",
        "phase_required": "Phase Required",
        "current": "Current",
        "target": "Target",
    },
}

# ============================================================
# STRIP DIACRITICS for fallback when terminal cannot render UTF-8
# ============================================================
_DIACRITICS_MAP = {
    # Lowercase
    'à': 'a', 'á': 'a', 'ả': 'a', 'ã': 'a', 'ạ': 'a',
    'ă': 'a', 'ằ': 'a', 'ắ': 'a', 'ẳ': 'a', 'ẵ': 'a', 'ặ': 'a',
    'â': 'a', 'ầ': 'a', 'ấ': 'a', 'ẩ': 'a', 'ẫ': 'a', 'ậ': 'a',
    'đ': 'd',
    'è': 'e', 'é': 'e', 'ẻ': 'e', 'ẽ': 'e', 'ẹ': 'e',
    'ê': 'e', 'ề': 'e', 'ế': 'e', 'ể': 'e', 'ễ': 'e', 'ệ': 'e',
    'ì': 'i', 'í': 'i', 'ỉ': 'i', 'ĩ': 'i', 'ị': 'i',
    'ò': 'o', 'ó': 'o', 'ỏ': 'o', 'õ': 'o', 'ọ': 'o',
    'ô': 'o', 'ồ': 'o', 'ố': 'o', 'ổ': 'o', 'ỗ': 'o', 'ộ': 'o',
    'ơ': 'o', 'ờ': 'o', 'ớ': 'o', 'ở': 'o', 'ỡ': 'o', 'ợ': 'o',
    'ù': 'u', 'ú': 'u', 'ủ': 'u', 'ũ': 'u', 'ụ': 'u',
    'ư': 'u', 'ừ': 'u', 'ứ': 'u', 'ử': 'u', 'ữ': 'u', 'ự': 'u',
    'ỳ': 'y', 'ý': 'y', 'ỷ': 'y', 'ỹ': 'y', 'ỵ': 'y',
    # Uppercase
    'À': 'A', 'Á': 'A', 'Ả': 'A', 'Ã': 'A', 'Ạ': 'A',
    'Ă': 'A', 'Ằ': 'A', 'Ắ': 'A', 'Ẳ': 'A', 'Ẵ': 'A', 'Ặ': 'A',
    'Â': 'A', 'Ầ': 'A', 'Ấ': 'A', 'Ẩ': 'A', 'Ẫ': 'A', 'Ậ': 'A',
    'Đ': 'D',
    'È': 'E', 'É': 'E', 'Ẻ': 'E', 'Ẽ': 'E', 'Ẹ': 'E',
    'Ê': 'E', 'Ề': 'E', 'Ế': 'E', 'Ể': 'E', 'Ễ': 'E', 'Ệ': 'E',
    'Ì': 'I', 'Í': 'I', 'Ỉ': 'I', 'Ĩ': 'I', 'Ị': 'I',
    'Ò': 'O', 'Ó': 'O', 'Ỏ': 'O', 'Õ': 'O', 'Ọ': 'O',
    'Ô': 'O', 'Ồ': 'O', 'Ố': 'O', 'Ổ': 'O', 'Ỗ': 'O', 'Ộ': 'O',
    'Ơ': 'O', 'Ờ': 'O', 'Ớ': 'O', 'Ở': 'O', 'Ỡ': 'O', 'Ợ': 'O',
    'Ù': 'U', 'Ú': 'U', 'Ủ': 'U', 'Ũ': 'U', 'Ụ': 'U',
    'Ư': 'U', 'Ừ': 'U', 'Ứ': 'U', 'Ử': 'U', 'Ữ': 'U', 'Ự': 'U',
    'Ỳ': 'Y', 'Ý': 'Y', 'Ỷ': 'Y', 'Ỹ': 'Y', 'Ỵ': 'Y',
}

_VN_NON_DIACRITIC = str.maketrans(_DIACRITICS_MAP)


def strip_diacritics(text: str) -> str:
    """Loại bỏ dấu tiếng Việt, hạ cấp về ASCII."""
    return text.translate(_VN_NON_DIACRITIC)


# ============================================================
# DETECT TERMINAL UTF-8 CAPABILITY
# ============================================================
_TERMINAL_UTF8_CACHE = None


def detect_terminal_utf8() -> bool:
    """Kiểm tra Terminal Windows có hỗ trợ UTF-8 hay không.

    Strategy:
      1. Kiểm tra stdout encoding (UTF-8 = 65001)
      2. Thử encode/decode một ký tự tiếng Việt
      3. Fallback: kiểm tra code page qua 'chcp'
    """
    global _TERMINAL_UTF8_CACHE
    if _TERMINAL_UTF8_CACHE is not None:
        return _TERMINAL_UTF8_CACHE

    # Strategy 1: Check stdout encoding
    stdout_encoding = getattr(sys.stdout, 'encoding', '').lower()
    if 'utf' in stdout_encoding or '65001' in stdout_encoding:
        _TERMINAL_UTF8_CACHE = True
        return True

    # Strategy 2: Try encoding a test character
    try:
        test = "Tiếng Việt"
        test.encode(sys.stdout.encoding or 'utf-8')
        _TERMINAL_UTF8_CACHE = True
        return True
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        pass

    # Strategy 3: Windows code page fallback
    if sys.platform.startswith("win"):
        try:
            import subprocess
            result = subprocess.run(["chcp.com"], capture_output=True, text=True, timeout=2)
            cp = result.stdout.strip()
            if "65001" in cp or "utf-8" in cp.lower() or "utf8" in cp.lower():
                _TERMINAL_UTF8_CACHE = True
                return True
        except Exception:
            pass

    _TERMINAL_UTF8_CACHE = False
    return False


def force_utf8_stdout():
    """Cưỡng bức stdout/stderr dùng UTF-8 trên Windows."""
    if sys.platform.startswith("win"):
        try:
            if isinstance(sys.stdout, io.TextIOWrapper):
                if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
                    try:
                        sys.stdout.reconfigure(encoding='utf-8')
                    except Exception:
                        pass
            elif hasattr(sys.stdout, 'buffer'):
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
            if isinstance(sys.stderr, io.TextIOWrapper):
                if getattr(sys.stderr, 'encoding', '').lower() != 'utf-8':
                    try:
                        sys.stderr.reconfigure(encoding='utf-8')
                    except Exception:
                        pass
            elif hasattr(sys.stderr, 'buffer'):
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
        except Exception:
            pass


def translate(key: str, lang: str = "vi") -> str:
    """Tra cứu bản dịch cho key.

    Nếu terminal không hỗ trợ UTF-8, tự động hạ cấp bỏ dấu.
    """
    result = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)
    if lang == "vi" and not detect_terminal_utf8():
        result = strip_diacritics(result)
    return result


def translate_safe(key: str, lang: str = "vi") -> tuple[str, str]:
    """Trả về (localized_text, canonical_key) — canonical luôn là English.

    Log Parser luôn có thể dùng canonical_key để phân loại,
    bất kể diacritics có bị strip hay không.

    Ví dụ:
      text, canonical = translate_safe("CASCADE_LIQUIDATION", "vi")
      # text = "THANH LÝ THÁC ĐỔ"
      # canonical = "CASCADE_LIQUIDATION"  (bất biến, bất chấp font)
    """
    return (translate(key, lang), key)


def localize_state(state: str, lang: str = "vi") -> str:
    """Dịch trạng thái Macro Governor."""
    return translate(state, lang)


def localize_phase(phase: str, lang: str = "vi") -> str:
    """Dịch pha Per-Symbol Absorption."""
    return translate(phase, lang)


def localize_classification(vqa_class: str, lang: str = "vi") -> str:
    """Dịch phân loại VQA."""
    return translate(vqa_class, lang)


def log_structured(logger_obj, event: str, canonical_key: str, data: dict,
                   lang: str = "vi", level: str = "info"):
    """Ghi log cấu trúc với dual-field: hiển thị + canonical.

    Luôn ghi canonical_key bằng English, kể cả khi data chứa
    trường 'display' đã được dịch. Log Parser dùng canonical_key
    để phân loại, không dùng display text.
    """
    log_entry = {
        "event": event,
        "canonical": canonical_key,
        "data": data,
    }
    log_method = getattr(logger_obj, level, logger_obj.info)
    log_method(f"[{event}] canonical={canonical_key} data={data}")
