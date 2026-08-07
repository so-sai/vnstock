from .models import CausalAttributionReport, CausalFactor

FRIENDLY_NAMES = {
    "FLOW": "Dòng tiền (FLOW)",
    "BREADTH": "Độ rộng thị trường (BREADTH)",
    "STRUCTURE": "Cấu trúc thị trường (STRUCTURE)",
    "REGIME": "Hệ thống trạng thái (REGIME)",
    "SSI": "Độ ổn định hệ thống (SSI)",
    "TRADE_STATE": "Chính sách giao dịch (TRADE_STATE)",
}


def compute_causal_attribution(
    transition_type: str,
    dbe_history: list[dict],
    lookback_days: int = 5,
) -> CausalAttributionReport:
    """
    Attributes causal weights to the transition based on the delta of DBE driver impacts.

    Parameters
    ----------
    transition_type : str
        Type of transition (e.g. REGIME_SHIFT, FLICKER_TO_TREND, TREND_TO_FLICKER, BIAS_FLIP)
    dbe_history : list of dict
        History of DBE snap logs. Each dict must contain:
          - "bias_drivers": list of BiasDriver or dict with keys "source" and "impact"
    lookback_days : int
        Attribution window (T) to calculate impact change.

    Returns
    -------
    CausalAttributionReport
    """
    if not dbe_history:
        return CausalAttributionReport(
            transition_type=transition_type,
            primary_cause="NONE",
            factors=[],
            summary_vi="Không đủ dữ liệu lịch sử để phân tích nhân quả.",
        )

    t_curr = len(dbe_history) - 1
    t_base = max(0, t_curr - lookback_days)

    curr_snap = dbe_history[t_curr]
    base_snap = dbe_history[t_base]

    # helper to extract drivers as dict
    def get_drivers_dict(snap: dict) -> dict[str, float]:
        drivers = snap.get("bias_drivers", [])
        out = {}
        for d in drivers:
            if isinstance(d, dict):
                out[d["source"]] = d["impact"]
            elif hasattr(d, "source") and hasattr(d, "impact"):
                out[d.source] = d.impact
        return out

    curr_drivers = get_drivers_dict(curr_snap)
    base_drivers = get_drivers_dict(base_snap)

    all_sources = ["FLOW", "STRUCTURE", "BREADTH", "REGIME", "SSI", "TRADE_STATE"]
    factors = []
    total_abs_delta = 0.0

    for source in all_sources:
        curr_val = curr_drivers.get(source, 0.0)
        base_val = base_drivers.get(source, 0.0)
        delta = round(curr_val - base_val, 4)
        direction = 1 if delta > 0 else (-1 if delta < 0 else 0)

        total_abs_delta += abs(delta)
        factors.append({"source": source, "delta": delta, "direction": direction, "abs_delta": abs(delta)})

    # Sort factors by absolute delta descending
    factors.sort(key=lambda x: x["abs_delta"], reverse=True)

    # Compute contribution percentages
    causal_factors = []
    for f in factors:
        contrib = round(f["abs_delta"] / total_abs_delta, 4) if total_abs_delta > 0 else 0.0
        causal_factors.append(
            CausalFactor(source=f["source"], delta=f["delta"], direction=f["direction"], contribution_pct=contrib)
        )

    # Generate Vietnamese summary narrative
    if total_abs_delta == 0.0:
        summary_vi = "Không phát hiện sự dịch chuyển đáng kể trong các yếu tố cấu thành nhân quả."
        primary_cause = "NONE"
    else:
        primary = factors[0]
        primary_cause = primary["source"]
        primary_name = FRIENDLY_NAMES.get(primary_cause, primary_cause)
        primary_dir = "gia tăng/phục hồi" if primary["delta"] > 0 else "suy giảm/áp lực"
        primary_delta_str = f"{'+' if primary['delta'] > 0 else ''}{primary['delta']:.2f}"

        summary_vi = f"Cơ chế chuyển pha ({transition_type}) chủ yếu do {primary_name} {primary_dir} ({primary_delta_str})"

        # Include secondary factor if it contributes significantly (abs delta > 0.05)
        if len(factors) > 1 and factors[1]["abs_delta"] > 0.05:
            secondary = factors[1]
            sec_name = FRIENDLY_NAMES.get(secondary["source"], secondary["source"])
            sec_dir = "gia tăng/nới lỏng" if secondary["delta"] > 0 else "sụt giảm/thắt chặt"
            sec_delta_str = f"{'+' if secondary['delta'] > 0 else ''}{secondary['delta']:.2f}"
            summary_vi += f", kết hợp với sự {sec_dir} từ {sec_name} ({sec_delta_str})."
        else:
            summary_vi += "."

    return CausalAttributionReport(
        transition_type=transition_type, primary_cause=primary_cause, factors=causal_factors, summary_vi=summary_vi
    )
