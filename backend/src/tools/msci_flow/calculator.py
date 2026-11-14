"""Bộ tính toán thuần túy (pure engine) dòng tiền ETF MSCI theo 4 nhóm kịch bản.

[WHY / ROOT CAUSE / INVARIANT]
WHY:
  Đảm bảo công thức quy đổi `AUM * (delta_weight_pct / 100) * FOL * FX_VND_USD`
  và tính toán `Estimated_Shares`, `DTL` được tách khỏi DB để kiểm thử đơn vị
  chặt chẽ trên mọi trường hợp biên (kể cả hạ tầng DOWNGRADE_STD_TO_SC hai chân).
ROOT CAUSE:
  Các kịch bản hạ tầng từ Standard xuống Small Cap (như DIG, FTS, PC1) chịu lực
  bán chân Standard và lực mua bù chân Small Cap đồng thời; nếu không tách từng
  chân hoặc tự bịa AUM khi Small Cap thiếu ETF mô phỏng sẽ làm sai lệch dòng ròng.
INVARIANT:
  1. Hàm thuần túy: không truy vấn DB, không sinh số giả khi thiếu trường bắt buộc.
  2. `< 20` phiên hợp lệ -> `STATUS_REFUSED_INSUFFICIENT_HISTORY`;
     `ADV20 == 0` -> `STATUS_REFUSED_ZERO_LIQUIDITY`;
     `close <= 0` hoặc `FX <= 0` hoặc `FOL` ngoài `[0, 1]` -> `STATUS_REFUSED_INVALID_INPUT`.
  3. Nếu nhóm chân `SMALLCAP` không có quỹ mô phỏng (`no_tracking_fund=True` hoặc không
     có quỹ SMALLCAP trong khi nhóm `STANDARD` đã kiểm chứng), chân Small Cap được ghi
     nhận dòng tiền `0.0` kèm cờ `NO_TRACKING_FUND` + `UNVERIFIED_SMALLCAP_AUM`.
"""

from __future__ import annotations

import math

from .classifier import classify_dtl, compute_on_exchange_buyable_shares, evaluate_flags
from .models import (
    MIN_ADV20_SESSIONS,
    PCT_TO_RATIO,
    STATUS_COMPUTED,
    STATUS_REFUSED_INSUFFICIENT_HISTORY,
    STATUS_REFUSED_INVALID_INPUT,
    STATUS_REFUSED_MISSING_DATA,
    STATUS_REFUSED_ZERO_LIQUIDITY,
    FundSpec,
    SymbolFlowResult,
    SymbolMarketSnapshot,
    SymbolSpec,
)

# [WHY]: Biên dưới của FOL và giới hạn kiểm tra số dương cho giá/ADV20/FX — giữ thống nhất với validator manifest.
ZERO_THRESHOLD = 0.0

# [WHY]: Biên trên của FOL chuẩn hóa [0.0, 1.0] — ngăn FOL > 1 phóng đại quy mô giao dịch vượt 100% phần bổ sung MSCI.
MAX_FOL_RATIO = 1.0


def _active_funds_for_tier(funds: tuple[FundSpec, ...], tier: str) -> tuple[list[FundSpec], bool]:
    """Lấy danh sách quỹ kiểm chứng cho `tier` và cờ báo không có quỹ mô phỏng."""
    matching = [f for f in funds if f.tier == tier]
    if not matching:
        # [WHY] Nếu đã có ít nhất một quỹ STANDARD được verify mà hoàn toàn không khai báo quỹ SMALLCAP
        # thì hiểu là không có ETF mô phỏng Small Cap.
        has_verified_std = any(f.tier == "STANDARD" and f.verified and f.aum_usd for f in funds)
        if tier == "SMALLCAP" and has_verified_std:
            return [], True
        return [], True

    if all(f.no_tracking_fund for f in matching):
        return [], True

    active = [f for f in matching if not f.no_tracking_fund]
    return active, False


def compute_fund_leg_flow_vnd(
    *,
    funds: tuple[FundSpec, ...],
    tier: str,
    delta_weight_pct: float,
    fol: float,
    fx_vnd_per_usd: float,
) -> tuple[float, bool]:
    """Tính dòng tiền một chân (`STANDARD` hoặc `SMALLCAP`) theo VND.

    Công thức: `SUM(aum_usd * (delta_weight_pct / 100) * fol) * fx_vnd_per_usd`.
    Trả về `(flow_vnd, has_no_tracking_fund)`.
    """
    active_funds, no_tracking_fund = _active_funds_for_tier(funds, tier)
    if no_tracking_fund or not active_funds:
        return ZERO_THRESHOLD, True

    total_usd = ZERO_THRESHOLD
    for fund in active_funds:
        if not fund.verified or fund.aum_usd is None or fund.aum_usd <= ZERO_THRESHOLD:
            raise ValueError(f"Quỹ {fund.fund_id} ({tier}) chưa kiểm chứng hoặc thiếu AUM hợp lệ")
        total_usd += float(fund.aum_usd) * (float(delta_weight_pct) / PCT_TO_RATIO) * float(fol)

    return total_usd * float(fx_vnd_per_usd), False


def _refused_result(
    spec: SymbolSpec,
    snapshot: SymbolMarketSnapshot,
    fx_vnd_per_usd: float | None,
    status: str,
    detail: str,
) -> SymbolFlowResult:
    return SymbolFlowResult(
        symbol=spec.symbol,
        bucket=spec.bucket,
        status=status,
        last_trade_date=snapshot.last_trade_date,
        close_vnd=snapshot.close_vnd,
        adv20_shares=snapshot.adv20_shares,
        valid_session_count=snapshot.valid_session_count,
        fx_vnd_per_usd=fx_vnd_per_usd,
        flow_std_vnd=None,
        flow_sc_vnd=None,
        net_flow_vnd=None,
        estimated_shares=None,
        dtl=None,
        dtl_bucket=None,
        fol=spec.fol,
        available_room_shares=spec.available_room_shares,
        foreign_room_pct=spec.foreign_room_pct,
        on_exchange_buyable_shares=None,
        health_archetype=snapshot.health_archetype,
        latest_health_period=snapshot.latest_health_period,
        net_income_vnd=snapshot.net_income_vnd,
        net_income_period=snapshot.net_income_period,
        flags=(status,),
        error_detail=detail,
    )


def compute_symbol_flow(
    *,
    spec: SymbolSpec,
    snapshot: SymbolMarketSnapshot,
    funds: tuple[FundSpec, ...],
    fx_vnd_per_usd: float | None,
) -> SymbolFlowResult:
    """Tính ước lượng dòng tiền, `Estimated_Shares`, `DTL`, nút thắt room và cờ nội tại cho 1 mã."""
    if snapshot.market_error_code is not None:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            snapshot.market_error_code,
            snapshot.market_error_detail or snapshot.market_error_code,
        )

    if fx_vnd_per_usd is None:
        return _refused_result(spec, snapshot, fx_vnd_per_usd, STATUS_REFUSED_MISSING_DATA, "Thiếu tỷ giá USD/VND")
    if not math.isfinite(fx_vnd_per_usd) or fx_vnd_per_usd <= ZERO_THRESHOLD:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_INVALID_INPUT,
            f"Tỷ giá USD/VND không hợp lệ: {fx_vnd_per_usd}",
        )

    if snapshot.valid_session_count < MIN_ADV20_SESSIONS:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_INSUFFICIENT_HISTORY,
            f"{spec.symbol} chỉ có {snapshot.valid_session_count}/{MIN_ADV20_SESSIONS} phiên hợp lệ",
        )

    if snapshot.close_vnd is None or not math.isfinite(snapshot.close_vnd) or snapshot.close_vnd <= ZERO_THRESHOLD:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_INVALID_INPUT,
            f"{spec.symbol} có giá đóng cửa không hợp lệ: {snapshot.close_vnd}",
        )

    if snapshot.adv20_shares is None or not math.isfinite(snapshot.adv20_shares) or snapshot.adv20_shares <= ZERO_THRESHOLD:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_ZERO_LIQUIDITY,
            f"{spec.symbol} có ADV20 = {snapshot.adv20_shares} (mất thanh khoản)",
        )

    if spec.fol is None or not math.isfinite(spec.fol) or spec.fol < ZERO_THRESHOLD or spec.fol > MAX_FOL_RATIO:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_INVALID_INPUT if spec.fol is not None else STATUS_REFUSED_MISSING_DATA,
            f"{spec.symbol} thiếu hoặc sai FOL: {spec.fol}",
        )

    flow_std_vnd: float | None = None
    flow_sc_vnd: float | None = None
    has_no_tracking_fund = False
    has_smallcap_gap = False

    if spec.bucket == "ADD_STD":
        if spec.delta_weight_pct is None:
            return _refused_result(spec, snapshot, fx_vnd_per_usd, STATUS_REFUSED_MISSING_DATA, "Thiếu delta_weight_pct")
        flow_std_vnd, std_missing = compute_fund_leg_flow_vnd(
            funds=funds,
            tier="STANDARD",
            delta_weight_pct=spec.delta_weight_pct,
            fol=spec.fol,
            fx_vnd_per_usd=fx_vnd_per_usd,
        )
        if std_missing:
            return _refused_result(
                spec,
                snapshot,
                fx_vnd_per_usd,
                STATUS_REFUSED_MISSING_DATA,
                "Thiếu quỹ STANDARD đã kiểm chứng",
            )
        net_flow_vnd = flow_std_vnd
    elif spec.bucket in {"ADD_SC", "REMOVE_SC"}:
        if spec.delta_weight_pct is None:
            return _refused_result(spec, snapshot, fx_vnd_per_usd, STATUS_REFUSED_MISSING_DATA, "Thiếu delta_weight_pct")
        flow_sc_vnd, sc_missing = compute_fund_leg_flow_vnd(
            funds=funds,
            tier="SMALLCAP",
            delta_weight_pct=spec.delta_weight_pct,
            fol=spec.fol,
            fx_vnd_per_usd=fx_vnd_per_usd,
        )
        if sc_missing:
            has_no_tracking_fund = True
            has_smallcap_gap = True
            flow_sc_vnd = ZERO_THRESHOLD
        net_flow_vnd = flow_sc_vnd
    elif spec.bucket == "DOWNGRADE_STD_TO_SC":
        if spec.delta_weight_std_pct is None or spec.delta_weight_sc_pct is None:
            return _refused_result(
                spec,
                snapshot,
                fx_vnd_per_usd,
                STATUS_REFUSED_MISSING_DATA,
                "DOWNGRADE_STD_TO_SC thiếu delta_weight_std_pct hoặc delta_weight_sc_pct",
            )
        flow_std_vnd, std_missing = compute_fund_leg_flow_vnd(
            funds=funds,
            tier="STANDARD",
            delta_weight_pct=spec.delta_weight_std_pct,
            fol=spec.fol,
            fx_vnd_per_usd=fx_vnd_per_usd,
        )
        if std_missing:
            return _refused_result(
                spec,
                snapshot,
                fx_vnd_per_usd,
                STATUS_REFUSED_MISSING_DATA,
                "Thiếu quỹ STANDARD đã kiểm chứng",
            )
        flow_sc_vnd, sc_missing = compute_fund_leg_flow_vnd(
            funds=funds,
            tier="SMALLCAP",
            delta_weight_pct=spec.delta_weight_sc_pct,
            fol=spec.fol,
            fx_vnd_per_usd=fx_vnd_per_usd,
        )
        if sc_missing:
            has_no_tracking_fund = True
            has_smallcap_gap = True
            flow_sc_vnd = ZERO_THRESHOLD
        net_flow_vnd = flow_std_vnd + flow_sc_vnd
    else:
        return _refused_result(
            spec,
            snapshot,
            fx_vnd_per_usd,
            STATUS_REFUSED_INVALID_INPUT,
            f"Bucket không hợp lệ: {spec.bucket}",
        )

    estimated_shares = net_flow_vnd / float(snapshot.close_vnd)
    dtl = abs(estimated_shares) / float(snapshot.adv20_shares)
    dtl_bucket = classify_dtl(dtl)
    on_exchange_buyable = compute_on_exchange_buyable_shares(spec, estimated_shares)
    flags = evaluate_flags(
        spec=spec,
        snapshot=snapshot,
        net_flow_vnd=net_flow_vnd,
        estimated_shares=estimated_shares,
        has_smallcap_gap=has_smallcap_gap,
        has_no_tracking_fund=has_no_tracking_fund,
    )

    return SymbolFlowResult(
        symbol=spec.symbol,
        bucket=spec.bucket,
        status=STATUS_COMPUTED,
        last_trade_date=snapshot.last_trade_date,
        close_vnd=snapshot.close_vnd,
        adv20_shares=snapshot.adv20_shares,
        valid_session_count=snapshot.valid_session_count,
        fx_vnd_per_usd=fx_vnd_per_usd,
        flow_std_vnd=flow_std_vnd,
        flow_sc_vnd=flow_sc_vnd,
        net_flow_vnd=net_flow_vnd,
        estimated_shares=estimated_shares,
        dtl=dtl,
        dtl_bucket=dtl_bucket,
        fol=spec.fol,
        available_room_shares=spec.available_room_shares,
        foreign_room_pct=spec.foreign_room_pct,
        on_exchange_buyable_shares=on_exchange_buyable,
        health_archetype=snapshot.health_archetype,
        latest_health_period=snapshot.latest_health_period,
        net_income_vnd=snapshot.net_income_vnd,
        net_income_period=snapshot.net_income_period,
        flags=flags,
        error_detail=None,
    )
