"""Mô hình dữ liệu thuần (domain dataclasses & invariants) cho dòng tiền MSCI.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Tách biệt cấu trúc dữ liệu đầu vào/đầu ra và mã trạng thái từ chối ra khỏi
  lớp I/O DB và CLI để mọi công thức tính toán chạy thuần trong bộ nhớ và kiểm
  thử đơn vị tốc độ cao.
ROOT CAUSE:
  Gộp chung dataclass vào script CLI khiến test phải dựng DB đầy đủ mới kiểm tra
  được các trường hợp biên như DTL = 0.5/2.0 hay thiếu room ngoại.
INVARIANT:
  1. Không chứa logic đọc/ghi DB, mạng hay file trong module này.
  2. Giá trị chưa đo được / thiếu bắt buộc giữ là `None`, tuyệt đối không mặc định 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# [WHY]: Quy ước mã thoát CLI chuẩn PTCK: 0 = hợp lệ / survey OK, 2 = từ chối fail-closed do thiếu/sai dữ liệu;
# sửa sẽ làm script caller hiểu nhầm thất bại.
EXIT_OK = 0

# [WHY]: Mã thoát 2 phân biệt lỗi dữ liệu đầu vào / vi phạm invariant với lỗi runtime Python (1);
# xóa hoặc sửa sẽ gộp chung lỗi thiếu manifest với bug hệ thống.
EXIT_REFUSED = 2

# [WHY]: Chuẩn thanh khoản hệ thống PTCK (AGENTS.md) định nghĩa ADV20 trên đúng 20 phiên hợp lệ;
# dưới 20 phiên làm méo mẫu số DTL — sửa sẽ chấp nhận mã thiếu lịch sử.
MIN_ADV20_SESSIONS = 20

# [WHY]: Tỷ trọng delta_weight trong manifest khai báo theo phần trăm (%), phải chia 100 thành hệ số nhân AUM;
# xóa sẽ phóng đại dòng tiền gấp 100 lần.
PCT_TO_RATIO = 100.0

STATUS_COMPUTED = "COMPUTED"
STATUS_SURVEY_OK = "SURVEY_OK"
STATUS_REFUSED_MISSING_DATA = "REFUSED_MISSING_DATA"
STATUS_REFUSED_INVALID_INPUT = "REFUSED_INVALID_INPUT"
STATUS_REFUSED_INSUFFICIENT_HISTORY = "REFUSED_INSUFFICIENT_HISTORY"
STATUS_REFUSED_ZERO_LIQUIDITY = "ZERO_LIQUIDITY"

VALID_FUND_TIERS: frozenset[str] = frozenset({"STANDARD", "SMALLCAP"})
VALID_BUCKETS: frozenset[str] = frozenset({"ADD_STD", "DOWNGRADE_STD_TO_SC", "ADD_SC", "REMOVE_SC"})

NON_DECISIONAL_BANNER = (
    "NON-DECISIONAL (OOS 2025-2026 QUARANTINE): Chi dung de kiem toan dinh luong "
    "ap luc dong tien thu dong; cam dung de toi uu tham so hay khuyen nghi giao dich."
)
MICROSTRUCTURE_WARNING_BANNER = "⚠️ CẢNH BÁO VI CẤU TRÚC: THIẾU DỮ LIỆU INTRADAY ATC — KHÔNG THỂ BACKTEST CHIẾN LƯỢC T+"


@dataclass(frozen=True)
class FundSpec:
    """Thông số quỹ ETF bám chỉ số MSCI tại một kỳ cơ cấu."""

    fund_id: str
    tier: str
    aum_usd: float | None = None
    aum_source: str | None = None
    aum_as_of: str | None = None
    verified: bool = False
    no_tracking_fund: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class SymbolSpec:
    """Khai báo ngoại sinh cho một cổ phiếu trong kỳ cơ cấu MSCI."""

    symbol: str
    bucket: str
    delta_weight_pct: float | None = None
    delta_weight_std_pct: float | None = None
    delta_weight_sc_pct: float | None = None
    fol: float | None = None
    foreign_room_pct: float | None = None
    available_room_shares: float | None = None
    verified: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class ManifestSpec:
    """Toàn bộ manifest ngoại sinh cho một kỳ review MSCI."""

    review_name: str
    effective_date: str
    funds: tuple[FundSpec, ...]
    symbols: tuple[SymbolSpec, ...]
    announcement_date: str | None = None
    fx_source: str | None = None
    fx_vnd_per_usd_override: float | None = None
    manifest_path: str | None = None


@dataclass(frozen=True)
class SymbolMarketSnapshot:
    """Ảnh chụp dữ liệu thị trường & sức khỏe nội tại đọc từ DB nội bộ (read-only)."""

    symbol: str
    last_trade_date: str | None
    close_vnd: float | None
    adv20_shares: float | None
    valid_session_count: int
    health_archetype: str | None = None
    latest_health_period: str | None = None
    net_income_vnd: float | None = None
    net_income_period: str | None = None
    market_error_code: str | None = None
    market_error_detail: str | None = None


@dataclass(frozen=True)
class SymbolFlowResult:
    """Kết quả ước lượng dòng tiền & bẫy thanh khoản cho từng mã (hoặc từ chối)."""

    symbol: str
    bucket: str
    status: str
    last_trade_date: str | None
    close_vnd: float | None
    adv20_shares: float | None
    valid_session_count: int
    fx_vnd_per_usd: float | None
    flow_std_vnd: float | None
    flow_sc_vnd: float | None
    net_flow_vnd: float | None
    estimated_shares: float | None
    dtl: float | None
    dtl_bucket: str | None
    fol: float | None
    available_room_shares: float | None
    foreign_room_pct: float | None
    on_exchange_buyable_shares: float | None
    health_archetype: str | None
    latest_health_period: str | None
    net_income_vnd: float | None
    net_income_period: str | None
    flags: tuple[str, ...] = ()
    error_detail: str | None = None


@dataclass(frozen=True)
class AuditRunResult:
    """Kết quả tổng hợp một lần chạy kiểm toán/survey MSCI."""

    mode: str
    status: str
    exit_code: int
    review_name: str
    effective_date: str
    announcement_date: str | None
    fx_vnd_per_usd: float | None
    fx_as_of_date: str | None
    fx_source: str | None
    results: tuple[SymbolFlowResult, ...]
    refusal_reasons: tuple[str, ...] = ()
    dry_run: bool = False
    audit_id: int | None = None
    input_hash: str | None = None
    banners: tuple[str, ...] = field(
        default_factory=lambda: (NON_DECISIONAL_BANNER, MICROSTRUCTURE_WARNING_BANNER),
    )
