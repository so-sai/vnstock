"""
canonical_output_adapter.py — Final Output Gate Contract v1.0

SINGLE EXIT RULE:
    ALL output MUST pass through localize_output() BEFORE reaching the API.
    NO RAW ENGLISH TOKEN IN FINAL REPORT.

Architecture:
    Engine (English) → cognitive_schema (mapping) → adapter (gate) → API (Vietnamese)

Usage:
    from core.canonical_output_adapter import localize_output
    return localize_output(build_weekly_report())

Contract:
    1. Every string value is checked against the master EN→VI mapping
    2. Exact-match enum values (TRENDING, CRISIS, PROMOTABLE, ...) → always translated
    3. UPPERCASE tokens embedded in narrative text → replaced via word-boundary regex
    4. Unknown values → pass through unchanged
    5. Non-string values → pass through unchanged
"""
from __future__ import annotations

import re
from typing import Any

from .cognitive_schema import (
    CONVICTION_VI,
    DRIFT_SOURCE_VI,
    DRIFT_STATUS_VI,
    DRIFT_TREND_VI,
    DRIVER_VI,
    DRIVER_VI_LOWER,
    EARLY_WARNING_VI,
    ETS_STATUS_VI,
    FIELD_LABELS,
    FLOW_ROTATION_VI,
    FLOW_STATE_VI,
    REGIME_LABEL_VI,
    REGIME_NOUN_VI,
    REGIME_VI,
    SECTOR_VI,
    SEVERITY_LABEL_VI,
    STATUS_VI,
)

# ====================================================================
# MASTER MAPPING — aggregate ALL known EN→VI pairs from the system
# ====================================================================

_EXTRA_MAP: dict[str, str] = {
    # Trust / CAO
    "PROMOTABLE": "Có thể kích hoạt",
    "BLOCKED": "Bị chặn",
    "NO_DATA": "Không có dữ liệu",
    # Gold regime
    "BULLISH": "Tăng",
    "BEARISH": "Giảm",
    # Premium regime
    "NORMAL": "Bình thường",
    "SURGE": "Tăng nóng",
    "DANGER": "Nguy hiểm",
    "DISCOUNT": "Chiết khấu",
    # General
    "NEUTRAL": "Trung tính",
    "live": "trực tiếp",
    "UNKNOWN": "Không xác định",
    # LCI / Flow
    "EXPANDING": "Mở rộng",
    "CONTRACTING": "Thu hẹp",
    "HEALTHY_ROTATION": "Luân chuyển lành mạnh",
    "DIVERGENT": "Phân kỳ",
    "HIGH_BREAKOUT_ACTIVITY": "Nhiều phá vỡ",
    "MODERATE_BREAKOUT_ACTIVITY": "Phá vỡ vừa phải",
    "LOW_BREAKOUT_ACTIVITY": "Ít phá vỡ",
    # Risk / Appetite
    "RISK_ON": "Chấp nhận rủi ro",
    "RISK_OFF": "Phòng thủ",
    # Constraint
    "ALLOWED": "Được phép",
    "PARTIAL": "Một phần",
    "BLOCKED": "Bị chặn",
    # Opportunity bias
    "BULLISH_BIAS": "Thiên hướng tăng",
    "BEARISH_BIAS": "Thiên hướng giảm",
    "DEFENSIVE": "Phòng thủ",
    # Market phase
    "ACCUMULATION": "Tích lũy",
    "DISTRIBUTION": "Phân phối",
    "MARKUP": "Tăng giá",
    "MARKDOWN": "Giảm giá",
    "WAITING": "Chờ đợi",
    "PRESERVATION": "Bảo toàn",
    # System
    "ok": "tốt",
    "ACTIVE": "Hoạt động",
    "INACTIVE": "Không hoạt động",
    # ACCELERATION patterns (used in scanner output)
    "ACCELERATION": "Tăng tốc",
    "DECELERATION": "Chậm lại",
    "CONTINUATION": "Tiếp diễn",
    "STABLE": "Ổn định",
    # Additional cognitive
    "NONE": "Không",
    "aligned": "khớp",
    "misaligned": "lệch",
    # Regime tags (lowercase, from reputation ledger)
    "trending": "Xu hướng rõ",
    "ranging": "Đi ngang",
    "crisis": "Khủng hoảng",
    # Driver keys (reputation ledger output, matching cognitive_schema)
    "VOLATILITY": "Biến động",

    # ============================================================
    # DECISION ACTIONS — mapped from decision_tensor
    # ============================================================
    "HOLD": "Giữ",
    "BUY": "Mua",
    "SELL": "Bán",
    "STRONG_BUY": "Mua mạnh",
    "STRONG_SELL": "Bán mạnh",
    "ENTER": "Mở vị thế",
    "EXIT": "Thoát",
    "REDUCE": "Giảm",
    "STAND_DOWN": "Đứng ngoài",
    "WATCH": "Theo dõi",
    "OBSERVE": "Quan sát",

    # ============================================================
    # RISK STATES — mapped from decision_tensor risk_state
    # ============================================================
    "SAFE": "An toàn",
    "CAUTION": "Thận trọng",
    "STRESS": "Căng thẳng",
    "LOCKED": "Khóa",

    # ============================================================
    # COACH TONE — mapped from portfolio coach tone
    # ============================================================
    "WARNING": "Cảnh báo",
    "CRITICAL": "Nghiêm trọng",
    "OPPORTUNITY": "Cơ hội",
    "BALANCED": "Cân bằng",

    # ============================================================
    # MARKET PRESSURE — mapped from foreign flow
    # ============================================================
    "ACCUMULATING": "Tích lũy",
    "DISTRIBUTING": "Phân phối",

    # ============================================================
    # RETAIL CHASE LABELS — mapped from liquidity wave
    # ============================================================
    "EXTREME": "Cực đoan",
    "MODERATE": "Vừa phải",

    # ============================================================
    # REPLAY EVENT CODES — mapped from replay timeline
    # ============================================================
    "ATR_SHOCK": "Sốc biến động",
    "BREADTH_COLLAPSE": "Sụp đổ độ rộng",
    "LOW_PARTICIPATION": "Tham gia thấp",
    "REGIME_CRISIS": "Khủng hoảng thị trường",
    "REGIME_FLIP": "Đảo chiều chế độ",
    "RECOVERY_FIRE": "Kích hoạt phục hồi",
    "BLOCKED": "Bị chặn",
    "PICK": "Chọn",
    "PRIMARY_MA200": "MA200 chính",
    "STRONG_TREND": "Xu hướng mạnh",
    "Pullback recovery setup": "Thiết lập phục hồi sau điều chỉnh",
    "Momentum continuation": "Tiếp diễn đà tăng",
    "Recovery detector activated": "Bộ phát hiện phục hồi đã kích hoạt",

    # ============================================================
    # OPPORTUNITY REASON STRINGS — mapped from scanner
    # ============================================================
    "volume confirmed": "xác nhận khối lượng",
    "strong retail chase": "dòng tiền bán lẻ mạnh",
    "moderate inflow": "dòng tiền vào vừa phải",
    "high continuation probability": "xác suất tiếp diễn cao",
    "Monitoring": "Đang theo dõi",

    # ============================================================
    # ============================================================
    # FLOW MAP (CrossMarketFlowMap) — Bản đồ dòng vốn 4 tầng
    # ============================================================
    "CASH_SHELTER": "Hầm trú ẩn tiền mặt",
    "HARD_ASSET_SHELTER": "Hầm trú ẩn tài sản cứng",
    "EQUITY_EXPANSION": "Bung xõa cổ phiếu",
    "TRANSITION_STATE": "Luân chuyển ngầm",
    "HIGH_CONFIDENCE": "Độ tin cậy cao",
    "LOW_CONFIDENCE_MACRO_VN": "Độ tin cậy vĩ mô trong nước thấp",
    "LATE-CYCLE OBSERVABILITY GAP: ADX spike trong regime RANGING": "Khoảng cách quan sát cuối chu kỳ: ADX tăng đột biến trong khi regime vẫn ở trạng thái đi ngang",

    # ============================================================
    # MIXED-CASE FIXES — catch non-uppercase variants
    # ============================================================
    "Unknown": "Không xác định",
    "NOT_FOUND": "Không tìm thấy",
}


def _build_master_map() -> dict[str, str]:
    merged: dict[str, str] = {}
    sources = [
        REGIME_LABEL_VI, REGIME_VI, REGIME_NOUN_VI,
        DRIVER_VI, DRIVER_VI_LOWER,
        DRIFT_SOURCE_VI, DRIFT_STATUS_VI,
        FLOW_STATE_VI, FLOW_ROTATION_VI,
        CONVICTION_VI, SECTOR_VI,
        STATUS_VI, SEVERITY_LABEL_VI,
        FIELD_LABELS,
        EARLY_WARNING_VI, DRIFT_TREND_VI,
        ETS_STATUS_VI,
        _EXTRA_MAP,
    ]
    for m in sources:
        merged.update(m)
    return merged


_MASTER_MAP: dict[str, str] = _build_master_map()

# Pre-compile word-boundary regex for UPPERCASE tokens (kernel enum convention)
# This handles embedded English tokens in narrative text without false positives
_UPPER_TOKENS: dict[str, str] = {
    k: v for k, v in _MASTER_MAP.items()
    if k.isupper() and len(k) > 1
}
_NARRATIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf'\b{re.escape(k)}\b'), v) for k, v in _UPPER_TOKENS.items()
]


# ====================================================================
# HYBRID DATA GUARDRAIL — protect numeric/time strings from translation
# ====================================================================

_HYBRID_GUARD = re.compile(
    r'^(?:\d+(?:\.\d+)?%?'          # pure number, optional trailing %
    r'|[-+]?\d+\.?\d*'              # signed float
    r'|\d{4}-\d{2}-\d{2}'           # date YYYY-MM-DD
    r'|[\d.]+ \([^)]+\)'            # "16.4 (yếu)" pattern
    r')$'
)

# ====================================================================
# RECURSIVE LOCALIZER
# ====================================================================

def _localize_value(value: Any) -> Any:
    if isinstance(value, str):
        # Guard: hybrid numeric/time strings skip translation entirely
        if _HYBRID_GUARD.match(value):
            return value
        if value in _MASTER_MAP:
            return _MASTER_MAP[value]
        for pattern, vi in _NARRATIVE_PATTERNS:
            if pattern.search(value):
                value = pattern.sub(vi, value)
        return value
    if isinstance(value, dict):
        return {k: _localize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_localize_value(item) for item in value]
    return value


# ====================================================================
# PUBLIC API — Single Exit Gate
# ====================================================================

def localize_output(data: dict) -> dict:
    """Final Output Gate — localize ALL string values in a report dict.

    This is the LAST transformation before data leaves the backend.
    Recursively walks the entire dict and maps known English kernel tokens
    to Vietnamese using the aggregated master mapping from cognitive_schema.

    Algorithm:
        1. Exact-match enum values → always translated
        2. UPPERCASE tokens in narrative strings → word-boundary regex replacement
        3. Unknown values → pass through unchanged

    Usage:
        from core.canonical_output_adapter import localize_output

        @router.get("/report")
        async def get_report():
            return localize_output(build_weekly_report())
    """
    return _localize_value(data)


# ====================================================================
# CLI LABEL LOCALIZATION — for direct print() statements in CLI
# ====================================================================

CLI_LABEL_MAP: dict[str, str] = {
    # ── Engine & Regime ──
    "Regime": "Trạng thái vĩ mô",
    "ADX": "Xung lực",
    "Entropy": "Mức độ nhiễu",
    "B-Score": "Điểm độ rộng",
    "ATR ratio": "Tỷ lệ biến động",
    "Độ rộng": "Số mã tham gia",
    "Cảnh báo sớm": "Tín hiệu chuyển pha",
    "Driver trội": "Nguyên nhân chính",
    "MA50 slope": "Độ dốc MA50",
    "VNINDEX vs MA200": "Chỉ số vs MA200",
    "BREADTH": "Độ rộng",
    "MOMENTUM": "Đà",
    "VOLATILITY": "Biến động",
    "STRUCTURE": "Cấu trúc",
    "MACRO": "Vĩ mô",
    "Trạng thái": "Pha thị trường",
    "Score": "Điểm số",
    "Độ rộng:": "Tỷ lệ tham gia:",
    "B-Score (continuous)": "Điểm Độ rộng (liên tục)",
    "T-Score": "Điểm Xu hướng",
    "V-Score (continuous)": "Điểm Biến động (liên tục)",
    "ATR Ratio": "Tỷ lệ ATR",
    "Raw Score": "Điểm Thô",
    "EMA Alpha": "Hệ số Phẳng hóa",
    "Prev Smoothed": "Giá trị Phẳng hóa Trước",
    "SMOOTHED REGIME SCORE": "ĐIỂM TRẠNG THÁI PHẲNG HÓA",
    "REGIME ENGINE": "BỘ PHÂN TÍCH MÔI TRƯỜNG",
    "HISTORICAL REPLAY": "PHÁT LẠI LỊCH SỬ",
    "LIVE ANALYSIS": "PHÂN TÍCH TRỰC TIẾP",
    "[LOCK 2] ATR SHOCK DETECTED": "[KHÓA 2] PHÁT HIỆN SỐC BIẾN ĐỘNG",
    "Today:": "Hôm nay:",
    "vs Avg:": "so với TB:",

    # ── Phase Classification ──
    "Phase Classification": "Phân loại Pha Thị trường",
    "RANGING": "ĐI NGANG (BIÊN ĐỘ HẸP)",
    "SILENT_DISTRIBUTION_HEAD": "ĐỈNH PHÂN PHỐI NGẦM",
    "RE_ACCUMULATION_BOTTOM": "ĐÁY TÍCH LŨY LẠI",
    "UNCERTAIN": "KHÔNG XÁC ĐỊNH",
    "Asia θ": "Góc xoay Châu Á (θ)",
    "Cấu trúc": "Cấu trúc nội tại",
    "Governor": "Cỗ máy Điều hướng",
    "should_reduce_exposure['risk_on']=True": "HẠ TỶ TRỌNG TÀI SẢN RỦI RO (RISK-OFF)",
    "should_reduce_exposure": "Giảm tỷ trọng",
    "Consensus": "Đồng thuận Trạng thái",
    "converging": "Hội tụ đồng pha",
    "diverging": "Phân kỳ rủi ro",
    "Rotation θ": "Độ trễ truyền dẫn (θ)",
    "DDI": "Áp lực DDI",
    "Δ_SA": "Δ Sai lệch",
    "HEALING ILLUSION": "ẢO GIÁC PHỤC HỒI",
    "KL Spread": "KL Phân kỳ",
    "Entropy ∇": "Độ nhiễu ∇",
    "BULLISH": "TĂNG",
    "BEARISH": "GIẢM",
    "NEUTRAL": "TRUNG TÍNH",
    "Label": "Nhãn",
    "Confidence": "Độ tin cậy",
    "BÌNH_THƯỜNG": "BÌNH THƯỜNG",
    "RỦI_RO_HỆ_THỐNG": "RỦI RO HỆ THỐNG",
    "CHUYỂN_PHA_MẠNH": "CHUYỂN PHA MẠNH",

    # ── Execution Layer: TWAP + Circuit Breaker ──
    "PENDING": "CHỜ XỬ LÝ",
    "CANCELED": "ĐÃ HỦY LỆNH (AN TOÀN)",
    "FAILED": "CHIẾN DỊCH ĐÌNH CHỈ",
    "Canary Triggered": "CẢNH BÁO ĐỎ CHÂU Á (CANARY)",
    "Emergency Halt": "KÍCH HOẠT DỪNG KHẨN CẤP",
    "Unfilled TWAP Campaign": "NGÂN SÁCH GIẢI NGÂN TỒN ĐỌNG",
    "ASIA_TIER1_VETO": "CHÂU Á — VETO TUYỆT ĐỐI (1.5%)",
    "ASIA_TIER2_HALT": "CHÂU Á — DỪNG XÁC NHẬN LÂY LAN",
    "ASIA_TIER2_NO_VN": "CHÂU Á — THIẾU DỮ LIỆU VN",
    "TWAP plan": "CHIẾN DỊCH TWAP",
    "slice": "lát cắt",
    "broker order": "lệnh sàn",
    "CANCEL_FAILED": "HỦY LỆNH THẤT BẠI",
    "TWAP PLAN": "KẾ HOẠCH TWAP",
    "TWAP STATUS": "TRẠNG THÁI TWAP",
    "TWAP CAMPAIGN": "CHIẾN DỊCH TWAP",
    "PHASE 5 — PAPER TRADING": "GIAI ĐOẠN 5 — MÔ PHỎNG GIAO DỊCH",
    "BREAK-GLASS PROTOCOL": "GIAO THỨC PHÁ VỠ KÍNH",
    "DELTA DIVERGENCE INDEX": "CHỈ SỐ PHÂN KỲ DELTA",
    "PREDICTION REGISTRY — THỐNG KÊ": "SỔ DỰ BÁO — THỐNG KÊ",
    "GOLD REGIME": "CHẾ ĐỘ VÀNG",
    "TABLE STATISTICS": "THỐNG KÊ BẢNG",
    "PORTFOLIO": "DANH MỤC",
    "Order Book": "SỔ LỆNH",
    "TWAP Executor": "BỘ THỰC THI TWAP",
    "StalePositionManager": "QUẢN LÝ VỐN KẸT",

    # ── DB / System ──
    "Python": "Python",
    "Project root": "Thư mục gốc",
    "Backend": "Phụ trợ",
    "Data": "Dữ liệu",
    "Libraries": "Thư viện",
    "Database": "Cơ sở dữ liệu",
    "Rows": "Dòng",
    "Row Counts": "Số dòng",
    "Size": "Kích thước",
    "Last Modified": "Cập nhật lần cuối",
    "Table": "Bảng",
    "Database Info": "Thông tin CSDL",
    "Version": "Phiên bản",
    "Driver": "Trình điều khiển",
    "Used": "Đã dùng",
    "Free": "Còn trống",

    # ── OHLCV ──
    "Symbol": "Mã CK",
    "Date": "Ngày",
    "Open": "Mở cửa",
    "High": "Cao nhất",
    "Low": "Thấp nhất",
    "Close": "Đóng cửa",
    "Volume": "Khối lượng",
    "Time": "Thời gian",

    # ── QuantStats ──
    "Live Sharpe": "Sharpe Thực tế",
    "Sharpe": "Sharpe",
    "Sortino": "Sortino",
    "Max Drawdown": "Sụt giảm Tối đa",
    "Recovery Factor": "Hệ số Phục hồi",
    "Win Rate": "Tỷ lệ Thắng",
    "Profit Factor": "Hệ số Lợi nhuận",
    "Kelly Criterion": "Tiêu chí Kelly",
    "Calibration Penalty": "Mức phạt Hiệu chỉnh",
    "Live Metrics": "Chỉ số Thực tế",
    "Rejected Metrics": "Chỉ số Bị từ chối",
    "Random Baseline": "Đường cơ sở Ngẫu nhiên",
    "Information Gain": "Lượng thông tin",
    "DOC Index": "Chỉ số DOC",
    "n_observations": "số quan sát",

    # ── Rejected Signals ──
    "Rejected Signals": "Tín hiệu Bị từ chối",
    "Rejection Stats": "Thống kê Từ chối",
    "Ticker": "Mã CK",
    "Reason": "Lý do",
    "Regime Score": "Điểm Trạng thái",
    "IG": "TT",
    "Status": "Trạng thái",
    "Valid Until": "Hiệu lực đến",
    "Simulated Exit 5d": "Mô phỏng 5n",
    "Simulated Exit 10d": "Mô phỏng 10n",
    "Simulated Exit 20d": "Mô phỏng 20n",

    # ── Telemetry ──
    "Reputation Score": "Điểm Uy tín",
    "Reliability": "Độ Tin cậy",
    "Accuracy": "Độ Chính xác",
    "Precision": "Độ Chuẩn xác",
    "Recall": "Độ Bao phủ",
    "F1 Score": "Điểm F1",
    "Latency": "Độ trễ",
    "Downtime": "Thời gian ngừng",
    "Uptime": "Thời gian hoạt động",
    "Driver Reputation": "Uy tín Động cơ",
    "Shadow Metrics": "Chỉ số Bóng",

    # ── Gold / Silver ──
    "Gold": "Vàng",
    "Silver": "Bạc",
    "Gold-to-Silver Ratio": "Tỷ lệ Vàng/Bạc",
    "XAU": "Vàng Thế giới",
    "SJC": "Vàng SJC",
    "BTMC": "Vàng BTMC",
    "Gold Price": "Giá Vàng",
    "Silver Price": "Giá Bạc",

    # ── Paper Trading ──
    "FILLED": "KHỚP",
    "REJECTED": "TỪ CHỐI",
    "CASH": "TIỀN MẶT",
    "BUY": "MUA",
    "SELL": "BÁN",
    "Filled": "Đã khớp",
    "Rejected": "Bị từ chối",
    "Quantity": "Khối lượng",
    "Price": "Giá",
    "Buying Power": "Sức mua",
    "Total Equity": "Tổng vốn",
    "Unrealized P&L": "Lãi/Lỗ chưa hiện thực",
    "Realized P&L": "Lãi/Lỗ đã hiện thực",
    "Slippage": "Trượt giá",
    "Latency": "Độ trễ",
    "Settlement": "Thanh toán",

    # ── Stale Positions ──
    "Stale Layer": "Lớp vốn kẹt",
    "Campaign": "Chiến dịch",
    "Escrow": "Ký quỹ",
    "Write-off": "Xóa sổ",
    "Reclaim": "Thu hồi",

    # ── Break-Glass ──
    "Ticket": "Phiếu",
    "Challenge": "Thử thách",
    "Passphrase": "Mật khẩu",
    "Override": "Ghi đè",
    "Request": "Yêu cầu",
    "Verify": "Xác thực",
    "Cancel": "Hủy",
    "Time-delay": "Trễ thời gian",

    # ── DDI / Rotation ──
    "Rotation Angle": "Góc xoay",
    "Lambda_max": "Lambda cực đại",
    "Action Filter": "Bộ lọc Hành động",
    "dS/dt": "dS/dt",
    "Asia Supply Chain": "Chuỗi cung ứng Châu Á",
    "Index Reality": "Thực tế Chỉ số",

    # ── Prediction Registry ──
    "Prediction ID": "Mã dự báo",
    "Hypothesis": "Giả thuyết",
    "Outcome": "Kết quả",
    "Hit": "Đúng",
    "Miss": "Sai",
    "Pending": "Chờ",
    "Accuracy Rate": "Tỷ lệ Chính xác",
    "Total Predictions": "Tổng Dự báo",

    # ── Statistics ──
    "Total": "Tổng",
    "Mean": "Trung bình",
    "Median": "Trung vị",
    "Std": "Độ lệch",
    "Min": "Tối thiểu",
    "Max": "Tối đa",
    "Count": "Số lượng",
    "Sum": "Tổng",
    "Rate": "Tỷ lệ",
    "Ratio": "Tỷ số",

    # ── Cleanup / Maintenance ──
    "cleaned": "đã dọn",
    "removed": "đã xóa",
    "skipped": "đã bỏ qua",
    "archived": "đã lưu trữ",
    "restored": "đã phục hồi",
    "backed up": "đã sao lưu",

    # ── Macro ──
    "DXY": "Chỉ số USD",
    "US10Y": "Lợi suất 10 năm Mỹ",
    "INTERBANK ON": "Lãi suất Liên ngân hàng ON",
    "INTERBANK 1W": "Lãi suất Liên ngân hàng 1W",
    "BREAKEVEN INFLATION": "Lạm phát kỳ vọng",
    "REAL YIELD": "Lợi suất thực",
    "SBV": "Ngân hàng Nhà nước",
    "World Bank": "Ngân hàng Thế giới",

    # ── Scheduler / EOD ──
    "EOD RUNNER": "BỘ CHẠY CUỐI NGÀY",
    "Catch-up": "Bù ngày",
    "Idempotency": "Tính đơn nhất",
    "Retry": "Thử lại",
    "Sleep": "Ngủ",
    "Scheduler": "Bộ lập lịch",

    # ── Scanner ──
    "Deep Scan": "Quét sâu",
    "Quick Scan": "Quét nhanh",
    "Elite Scanner": "Máy quét Tinh hoa",
    "Screener": "Bộ sàng lọc",
    "Signal": "Tín hiệu",

    # ─── Absorption ──
    "Absorption Detector": "Bộ phát hiện Hấp thụ",
    "SDI": "Chỉ số Phân kỳ Cấu trúc",
    "PCA": "Phân tích Thành phần Chính",
    "Volume Profile": "Hồ sơ Khối lượng",

    # ── Structure Evolution ──
    "W1": "W1 Wasserstein",
    "Survival Mode": "Chế độ Sinh tồn",
    "Structure Evolution": "Tiến hóa Cấu trúc",
    "HDR": "Tỷ lệ Giảm thiểu Rủi ro",
}


def _detect_lang_mode(mode: str) -> str:
    """Auto-detect mode from terminal width.
    
    Rules:
        - Non-auto modes → passed through unchanged
        - Non-TTY output (pipe/file) → 'annotated' (preserve EN+VI for logs)
        - TTY with ≥120 cols → 'annotated'
        - TTY with <120 cols → 'compact'
    """
    if mode != "auto":
        return mode
    try:
        import shutil
        import sys
        if not sys.stdout.isatty():
            return "annotated"
        cols = shutil.get_terminal_size().columns
        return "annotated" if cols >= 120 else "compact"
    except Exception:
        return "annotated"


def localize_label(label: str, mode: str = "annotated") -> str:
    """Localize a CLI label based on mode.

    Args:
        label: English or Vietnamese label string
        mode: 'compact' (keep EN), 'annotated' (EN + VI), 'full' (VI only), 'auto'

    Returns:
        Localized label string.
    """
    mode = _detect_lang_mode(mode)
    vi = CLI_LABEL_MAP.get(label)
    if vi is None:
        return label
    if mode == "compact":
        return label
    if mode == "full":
        return vi
    return f"{label} ({vi})"


# ====================================================================
# ASSERT — Verify the contract at import time
# ====================================================================

def assert_no_english_tokens(data: dict, path: str = "") -> None:
    """Recursively assert that no known English tokens remain in data.

    Raises AssertionError if any known EN token is still present.
    Useful for tests and CI/CD gates.
    """
    for key, value in data.items():
        current = f"{path}.{key}" if path else key
        if isinstance(value, str) and value in _MASTER_MAP:
            raise AssertionError(
                f"EN token leak at {current!r}: {value!r} → should be {_MASTER_MAP[value]!r}"
            )
        if isinstance(value, dict):
            assert_no_english_tokens(value, current)
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    assert_no_english_tokens(item, f"{current}[{i}]")
                elif isinstance(item, str) and item in _MASTER_MAP:
                    raise AssertionError(
                        f"EN token leak at {current}[{i}]: {item!r} → should be {_MASTER_MAP[item]!r}"
                    )
