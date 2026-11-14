"""Đọc và xác thực fail-closed manifest JSON cho kỳ review MSCI.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Mọi đầu vào ngoại sinh (AUM ETF, delta_weight, FOL, room ngoại) phải được
  khai báo tường minh với cờ `verified=true` và nguồn/ngày trích xuất; nếu thiếu
  hoặc chưa kiểm chứng thì hệ thống buộc phải từ chối chạy tính dòng tiền.
ROOT CAUSE:
  Cho phép chạy với `null` hoặc giá trị mặc định âm thầm tạo ra con số giả
  (`0` hoặc ước đoán), vi phạm bất biến `Missing > Fake` trong AGENTS.md.
INVARIANT:
  1. Lỗi thiếu dữ liệu / `verified=false` -> `STATUS_REFUSED_MISSING_DATA` (exit 2).
  2. Lỗi giá trị phi lý (âm, NaN/Inf, FOL ngoài [0,1], sai dấu chiều bucket, trùng mã)
     -> `STATUS_REFUSED_INVALID_INPUT` (exit 2).
  3. Hỗ trợ riêng cơ chế `no_tracking_fund=true` (hoặc không có quỹ tier SMALLCAP
     khi tier STANDARD đã verified) để gắn cờ `NO_TRACKING_FUND` thay vì bịa AUM.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .models import (
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_MISSING_DATA,
    VALID_BUCKETS,
    VALID_FUND_TIERS,
    FundSpec,
    ManifestSpec,
    SymbolSpec,
)

# [WHY]: Biên phần trăm room ngoại hợp lệ trên sàn chứng khoán Việt Nam nằm trong [0.0, 100.0];
# sửa sẽ bỏ lọt dữ liệu phần trăm sai đơn vị.
MAX_ROOM_PCT = 100.0

# [WHY]: Hệ số FOL chuẩn hóa nằm trong [0.0, 1.0] (1.0 = không giới hạn thêm ngoài tỷ trọng SAIR);
# sửa sẽ cho phép FOL > 1 phóng đại dòng tiền.
MAX_FOL_RATIO = 1.0

# [WHY]: Giá trị tối thiểu không âm cho room và số dương chặt cho AUM/FX — dùng hằng số rõ nghĩa tránh magic literal.
ZERO_THRESHOLD = 0.0


def _is_finite_number(val: Any) -> bool:
    """True nếu `val` là int/float hữu hạn (loại bool vì bool là subclass của int)."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return math.isfinite(float(val))


def _as_optional_float(val: Any, field_name: str, errors: list[str]) -> float | None:
    if val is None:
        return None
    if not _is_finite_number(val):
        errors.append(f"invalid:{field_name} phải là số hữu hạn, nhận {val!r}")
        return None
    return float(val)


def _as_optional_str(val: Any) -> str | None:
    if val is None:
        return None
    text = str(val).strip()
    return text if text else None


def load_manifest(path: str | Path) -> ManifestSpec:
    """Đọc file JSON manifest thành `ManifestSpec` (ném ValueError nếu JSON/schema hỏng)."""
    manifest_file = Path(path)
    if not manifest_file.exists():
        raise FileNotFoundError(f"Không tìm thấy manifest MSCI: {manifest_file}")

    raw = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Manifest JSON phải là object ở cấp cao nhất")

    parse_errors: list[str] = []
    funds_raw = raw.get("funds")
    if funds_raw is None or not isinstance(funds_raw, list):
        raise ValueError("Manifest JSON thiếu danh sách `funds`")

    funds: list[FundSpec] = []
    for idx, item in enumerate(funds_raw):
        if not isinstance(item, dict):
            raise ValueError(f"funds[{idx}] phải là object")
        fund_id = _as_optional_str(item.get("fund_id")) or f"FUND_{idx}"
        tier = (_as_optional_str(item.get("tier")) or "").upper()
        funds.append(
            FundSpec(
                fund_id=fund_id,
                tier=tier,
                aum_usd=_as_optional_float(item.get("aum_usd"), f"funds[{idx}].aum_usd", parse_errors),
                aum_source=_as_optional_str(item.get("aum_source")),
                aum_as_of=_as_optional_str(item.get("aum_as_of")),
                verified=bool(item.get("verified", False)),
                no_tracking_fund=bool(item.get("no_tracking_fund", False)),
                notes=_as_optional_str(item.get("notes")),
            )
        )

    symbols_raw = raw.get("symbols")
    if symbols_raw is None or not isinstance(symbols_raw, list):
        raise ValueError("Manifest JSON thiếu danh sách `symbols`")

    symbols: list[SymbolSpec] = []
    for idx, item in enumerate(symbols_raw):
        if not isinstance(item, dict):
            raise ValueError(f"symbols[{idx}] phải là object")
        sym = (_as_optional_str(item.get("symbol")) or "").upper()
        bucket = (_as_optional_str(item.get("bucket")) or "").upper()
        symbols.append(
            SymbolSpec(
                symbol=sym,
                bucket=bucket,
                delta_weight_pct=_as_optional_float(
                    item.get("delta_weight_pct"),
                    f"symbols[{idx}].delta_weight_pct",
                    parse_errors,
                ),
                delta_weight_std_pct=_as_optional_float(
                    item.get("delta_weight_std_pct"),
                    f"symbols[{idx}].delta_weight_std_pct",
                    parse_errors,
                ),
                delta_weight_sc_pct=_as_optional_float(
                    item.get("delta_weight_sc_pct"),
                    f"symbols[{idx}].delta_weight_sc_pct",
                    parse_errors,
                ),
                fol=_as_optional_float(item.get("fol"), f"symbols[{idx}].fol", parse_errors),
                foreign_room_pct=_as_optional_float(
                    item.get("foreign_room_pct"),
                    f"symbols[{idx}].foreign_room_pct",
                    parse_errors,
                ),
                available_room_shares=_as_optional_float(
                    item.get("available_room_shares"),
                    f"symbols[{idx}].available_room_shares",
                    parse_errors,
                ),
                verified=bool(item.get("verified", False)),
                notes=_as_optional_str(item.get("notes")),
            )
        )

    fx_override = _as_optional_float(raw.get("fx_vnd_per_usd_override"), "fx_vnd_per_usd_override", parse_errors)
    if parse_errors:
        raise ValueError("; ".join(parse_errors))

    return ManifestSpec(
        review_name=_as_optional_str(raw.get("review_name")) or "MSCI Index Review",
        effective_date=_as_optional_str(raw.get("effective_date")) or "",
        announcement_date=_as_optional_str(raw.get("announcement_date")),
        fx_source=_as_optional_str(raw.get("fx_source")),
        fx_vnd_per_usd_override=fx_override,
        funds=tuple(funds),
        symbols=tuple(symbols),
        manifest_path=str(manifest_file),
    )


def _validate_fund_strict(
    fund: FundSpec,
    missing_reasons: list[str],
    invalid_reasons: list[str],
) -> None:
    if fund.tier not in VALID_FUND_TIERS:
        invalid_reasons.append(f"Quỹ {fund.fund_id} có tier không hợp lệ: {fund.tier!r}")
        return
    if fund.no_tracking_fund:
        # [WHY] Quỹ SMALLCAP không tồn tại ETF bám chỉ số được xác nhận tường minh -> không bắt buộc AUM
        # nhưng cấm vừa khai no_tracking_fund vừa điền AUM mâu thuẫn.
        if fund.aum_usd is not None and fund.aum_usd > ZERO_THRESHOLD:
            invalid_reasons.append(f"Quỹ {fund.fund_id} đặt no_tracking_fund=true nhưng vẫn có aum_usd={fund.aum_usd}")
        return
    if not fund.verified:
        missing_reasons.append(f"Quỹ {fund.fund_id} ({fund.tier}) chưa verified=true")
    if fund.aum_usd is None:
        missing_reasons.append(f"Quỹ {fund.fund_id} ({fund.tier}) thiếu aum_usd")
    elif not math.isfinite(fund.aum_usd) or fund.aum_usd <= ZERO_THRESHOLD:
        invalid_reasons.append(f"Quỹ {fund.fund_id} có aum_usd <= 0 hoặc không hữu hạn: {fund.aum_usd}")
    if not fund.aum_source:
        missing_reasons.append(f"Quỹ {fund.fund_id} ({fund.tier}) thiếu aum_source")
    if not fund.aum_as_of:
        missing_reasons.append(f"Quỹ {fund.fund_id} ({fund.tier}) thiếu aum_as_of")


def _validate_symbol_strict(
    sym: SymbolSpec,
    missing_reasons: list[str],
    invalid_reasons: list[str],
) -> None:
    if not sym.symbol:
        invalid_reasons.append("Có phần tử symbol rỗng trong manifest")
        return
    if sym.bucket not in VALID_BUCKETS:
        invalid_reasons.append(f"{sym.symbol}: bucket không hợp lệ {sym.bucket!r}")
        return
    if not sym.verified:
        missing_reasons.append(f"{sym.symbol}: verified=false (chưa kiểm chứng trọng số/FOL)")

    if sym.fol is None:
        missing_reasons.append(f"{sym.symbol}: thiếu fol")
    elif not math.isfinite(sym.fol) or sym.fol < ZERO_THRESHOLD or sym.fol > MAX_FOL_RATIO:
        invalid_reasons.append(f"{sym.symbol}: fol={sym.fol} nằm ngoài [0, 1]")

    if sym.foreign_room_pct is not None and (
        not math.isfinite(sym.foreign_room_pct) or sym.foreign_room_pct < ZERO_THRESHOLD or sym.foreign_room_pct > MAX_ROOM_PCT
    ):
        invalid_reasons.append(f"{sym.symbol}: foreign_room_pct={sym.foreign_room_pct} nằm ngoài [0, 100]")

    if sym.available_room_shares is not None and (
        not math.isfinite(sym.available_room_shares) or sym.available_room_shares < ZERO_THRESHOLD
    ):
        invalid_reasons.append(f"{sym.symbol}: available_room_shares={sym.available_room_shares} < 0")

    if sym.bucket == "DOWNGRADE_STD_TO_SC":
        if sym.delta_weight_std_pct is None:
            missing_reasons.append(f"{sym.symbol}: DOWNGRADE_STD_TO_SC thiếu delta_weight_std_pct")
        elif sym.delta_weight_std_pct > ZERO_THRESHOLD:
            invalid_reasons.append(f"{sym.symbol}: DOWNGRADE_STD_TO_SC đòi hỏi delta_weight_std_pct <= 0")
        if sym.delta_weight_sc_pct is None:
            missing_reasons.append(f"{sym.symbol}: DOWNGRADE_STD_TO_SC thiếu delta_weight_sc_pct")
        elif sym.delta_weight_sc_pct < ZERO_THRESHOLD:
            invalid_reasons.append(f"{sym.symbol}: DOWNGRADE_STD_TO_SC đòi hỏi delta_weight_sc_pct >= 0")
    else:
        if sym.delta_weight_pct is None:
            missing_reasons.append(f"{sym.symbol}: thiếu delta_weight_pct")
        elif sym.bucket in {"ADD_STD", "ADD_SC"} and sym.delta_weight_pct < ZERO_THRESHOLD:
            invalid_reasons.append(f"{sym.symbol}: {sym.bucket} đòi hỏi delta_weight_pct >= 0")
        elif sym.bucket == "REMOVE_SC" and sym.delta_weight_pct > ZERO_THRESHOLD:
            invalid_reasons.append(f"{sym.symbol}: REMOVE_SC đòi hỏi delta_weight_pct <= 0")


def validate_manifest_strict(spec: ManifestSpec) -> tuple[str | None, tuple[str, ...]]:
    """Kiểm tra manifest ở chế độ fail-closed.

    Trả về `(error_status, reasons)`. Nếu hợp lệ trả `(None, ())`.
    Ưu tiên báo `STATUS_REFUSED_INVALID_INPUT` nếu có giá trị sai logic/dấu, ngược
    lại báo `STATUS_REFUSED_MISSING_DATA` khi thiếu trường bắt buộc hoặc chưa verified.
    """
    missing_reasons: list[str] = []
    invalid_reasons: list[str] = []

    if not spec.effective_date:
        missing_reasons.append("Manifest thiếu effective_date")

    if spec.fx_vnd_per_usd_override is not None and (
        not math.isfinite(spec.fx_vnd_per_usd_override) or spec.fx_vnd_per_usd_override <= ZERO_THRESHOLD
    ):
        invalid_reasons.append(f"fx_vnd_per_usd_override phải > 0, nhận {spec.fx_vnd_per_usd_override}")

    if not spec.funds:
        missing_reasons.append("Manifest không có quỹ ETF nào trong `funds`")
    else:
        for fund in spec.funds:
            _validate_fund_strict(fund, missing_reasons, invalid_reasons)

    if not spec.symbols:
        missing_reasons.append("Manifest không có mã cổ phiếu nào trong `symbols`")
    else:
        seen: set[str] = set()
        for sym in spec.symbols:
            if sym.symbol in seen and sym.symbol:
                invalid_reasons.append(f"Mã trùng lặp trong manifest: {sym.symbol}")
            seen.add(sym.symbol)
            _validate_symbol_strict(sym, missing_reasons, invalid_reasons)

    if invalid_reasons:
        return STATUS_REFUSED_INVALID_INPUT, tuple(invalid_reasons + missing_reasons)
    if missing_reasons:
        return STATUS_REFUSED_MISSING_DATA, tuple(missing_reasons)
    return None, ()
