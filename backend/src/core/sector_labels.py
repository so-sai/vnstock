"""sector_labels.py — Canonical Sector Vocabulary (Single Source of Truth).

Patch B1: gom 3 nơi định nghĩa sector labels phân mảnh về MỘT nguồn:
  - daily_market_report._SECTOR_BI (VI+EN, renderer báo cáo ngày)
  - portfolio_decision_layer._SECTOR_VI (VI, phân loại danh mục)
  - orchestrator Sector Macro Scores (trước đây in mã thô STEEL/OIL...)

Phủ đúng 11 ngành CORE_SECTORS (src/engine/universe.py). Mọi module muốn
dịch sector code PHẢI import từ đây — cấm định nghĩa mapping riêng
(bị chặn bởi backend/tests/test_sector_labels.py).

Contract 3 mode (khớp _bi() của daily_market_report):
  full      -> "Thép"
  compact   -> "Steel"
  annotated -> "Thép (STEEL)"   (mã giữ lại để dev đối chiếu DB/log)
"""

from __future__ import annotations

# code -> (VI, EN)
SECTOR_LABELS: dict[str, tuple[str, str]] = {
    "BANK": ("Ngân hàng", "Banking"),
    "RE": ("Bất động sản", "Real Estate"),
    "SEC": ("Chứng khoán", "Securities"),
    "STEEL": ("Thép", "Steel"),
    "CONSUMER": ("Tiêu dùng", "Consumer"),
    "TECH": ("Công nghệ", "Technology"),
    "OIL": ("Dầu khí", "Oil & Gas"),
    "TRANS": ("Vận tải", "Transport"),
    "CONST": ("Xây dựng", "Construction"),
    "FOOD": ("Thực phẩm", "Food"),
    "UTILITY": ("Tiện ích / Điện", "Utilities"),
}

CORE_SECTOR_CODES: tuple[str, ...] = tuple(SECTOR_LABELS.keys())


def sector_label(code: str, mode: str = "annotated") -> str:
    """Localize 1 sector code theo 3 mode. Code lạ -> nguyên trạng."""
    norm = str(code).strip().upper()
    pair = SECTOR_LABELS.get(norm)
    if not pair:
        return code
    vi, en = pair
    if mode == "compact":
        return en
    if mode == "full":
        return vi
    return f"{vi} ({norm})"


def sector_label_vi(code: str) -> str:
    """Tên VI thuần; code lạ -> nguyên trạng."""
    pair = SECTOR_LABELS.get(str(code).strip().upper())
    return pair[0] if pair else code
