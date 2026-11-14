"""Gói công cụ định lượng dòng tiền cơ cấu rổ MSCI (NQ-25 — NON-DECISIONAL).

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Mỗi kỳ cơ cấu rổ MSCI Frontier Markets (Standard & Small Cap) tạo ra áp lực
  mua/bán cơ học tập trung ở phiên ATC ngày hiệu lực, có thể bóp méo thanh khoản
  cục bộ (DTL > 2 ngày) hoặc va chạm trần room ngoại (FOL).
ROOT CAUSE:
  Trước đây việc ước lượng dòng tiền MSCI dễ pha trộn số liệu thị trường (giá,
  ADV20) tự điền tay với giả định AUM ETF không kiểm chứng, dẫn đến ảo giác
  chính xác và vi phạm bất biến Missing > Fake.
INVARIANT:
  1. Giá, ADV20 (20 phiên hợp lệ) và tỷ giá USD/VND đọc trực tiếp từ DB nội bộ
     theo bộ lọc `VALID_ROW_SQL AND is_stale = 0`.
  2. Dữ liệu ngoại sinh (AUM có nguồn & ngày, delta_weight_pct, FOL, `verified`)
     BẮT BUỘC đi qua manifest JSON; thiếu hoặc `verified=false` -> TỪ CHỐI
     (exit code 2, REFUSED_MISSING_DATA), cấm tự điền số giả.
  3. Mọi báo cáo đều thuộc vùng cách ly OOS 2025-2026 (`NON-DECISIONAL`) và gắn
     cảnh báo thiếu dữ liệu intraday ATC.
"""

from __future__ import annotations

from .calculator import compute_fund_leg_flow_vnd, compute_symbol_flow
from .classifier import DTL_ABSORBED_MAX, DTL_ELEVATED_MAX, classify_dtl, evaluate_flags
from .manifest import load_manifest, validate_manifest_strict
from .models import (
    EXIT_OK,
    EXIT_REFUSED,
    MIN_ADV20_SESSIONS,
    STATUS_COMPUTED,
    STATUS_REFUSED_INSUFFICIENT_HISTORY,
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_MISSING_DATA,
    STATUS_REFUSED_ZERO_LIQUIDITY,
    STATUS_SURVEY_OK,
    VALID_BUCKETS,
    VALID_FUND_TIERS,
    AuditRunResult,
    FundSpec,
    ManifestSpec,
    SymbolFlowResult,
    SymbolMarketSnapshot,
    SymbolSpec,
)

__all__ = [
    "AuditRunResult",
    "DTL_ABSORBED_MAX",
    "DTL_ELEVATED_MAX",
    "EXIT_OK",
    "EXIT_REFUSED",
    "FundSpec",
    "MIN_ADV20_SESSIONS",
    "ManifestSpec",
    "STATUS_COMPUTED",
    "STATUS_REFUSED_INSUFFICIENT_HISTORY",
    "STATUS_REFUSED_INVALID_INPUT",
    "STATUS_REFUSED_MISSING_DATA",
    "STATUS_REFUSED_ZERO_LIQUIDITY",
    "STATUS_SURVEY_OK",
    "SymbolFlowResult",
    "SymbolMarketSnapshot",
    "SymbolSpec",
    "VALID_BUCKETS",
    "VALID_FUND_TIERS",
    "classify_dtl",
    "compute_fund_leg_flow_vnd",
    "compute_symbol_flow",
    "evaluate_flags",
    "load_manifest",
    "validate_manifest_strict",
]
