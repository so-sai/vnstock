import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _flow_bias_score(flow: str) -> float:
    mapping = {
        "ACCELERATION": 1.0,
        "CONTINUATION": 0.65,
        "DECELERATION": 0.35,
        "LAG": 0.1,
    }
    return mapping.get(flow, 0.0)


def _sentinel_is_green(sentinel: dict) -> bool:
    status = sentinel.get("final_status", "")
    return status.startswith("GREEN")


def _dcl_override_weight(dcl_report: Optional[dict]) -> float:
    if not dcl_report:
        return 0.0
    verdict = dcl_report.get("verdict", "NO_TRADE")
    if verdict == "ACTIONABLE":
        return 1.0
    if verdict == "OBSERVE":
        comp = dcl_report.get("compensations_triggered", 0)
        if comp >= 2:
            return 0.7
        if comp >= 1:
            return 0.5
        return 0.3
    return 0.0


def _veto_code(veto_key: str, override_pct: float) -> str:
    if override_pct >= 0.5:
        return f"WARN_{veto_key}"
    return f"VETO_{veto_key}"


def compile_verdict(
    symbol: str,
    model_b_entry: Optional[dict] = None,
    sentinel_data: Optional[dict] = None,
    regime_status: str = "UNKNOWN",
    breadth_health: float = 0.0,
    lcr_pct: float = 30.0,
    bdi_signal: str = "CAN_BANG",
    ssi_score: float = 0.5,
    trade_state_level: str = "PROHIBITED",
    dcl_report: Optional[dict] = None,
) -> dict:
    veto_reasons: list[str] = []
    warnings: list[str] = []
    override_pct = _dcl_override_weight(dcl_report)

    is_trade_blocked = trade_state_level in ("PROHIBITED", "RESTRICTED")

    sentinel = sentinel_data or {}
    sentinel_ok = _sentinel_is_green(sentinel)
    sentinel_label = sentinel.get("final_status", "UNKNOWN")

    if model_b_entry:
        flow_label = model_b_entry.get("flow_alignment_label", "UNKNOWN")
        flow_score_val = model_b_entry.get("flow_alignment_score", 0)
        momentum_score = model_b_entry.get("momentum_score", 0)
        liquidity_score = model_b_entry.get("liquidity_score", 0)
        conviction = model_b_entry.get("conviction", 0)
        entry_suggestion = model_b_entry.get("entry_suggestion", "")
        tier = model_b_entry.get("tier", "UNKNOWN")
    else:
        flow_label = "UNKNOWN"
        flow_score_val = 0
        momentum_score = 0
        liquidity_score = 0
        conviction = 0
        entry_suggestion = ""
        tier = "UNKNOWN"

    flow_score = _flow_bias_score(flow_label)

    if is_trade_blocked:
        vc = _veto_code("TRADE_STATE", override_pct)
        if override_pct < 0.5:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if not sentinel_ok and sentinel:
        vc = _veto_code("SENTINEL", override_pct)
        if override_pct < 0.4:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if flow_score < 0.35:
        vc = _veto_code("FLOW", override_pct)
        if override_pct < 0.5:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if breadth_health < 30:
        vc = _veto_code("BREADTH", override_pct)
        if override_pct < 0.5:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if lcr_pct > 35 and bdi_signal != "CAN_BANG":
        vc = _veto_code("STRUCTURE", override_pct)
        if override_pct < 0.6:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if ssi_score < 0.35:
        vc = _veto_code("SSI", override_pct)
        if override_pct < 0.5:
            veto_reasons.append(vc)
        else:
            warnings.append(vc)

    if 0.35 <= flow_score < 0.65:
        warnings.append("WARN_FLOW_DECELERATION")

    if not sentinel_ok and flow_score >= 0.65 and not veto_reasons:
        warnings.append("WARN_SENTINEL_RED_FLOW_STRONG")

    investment_allowed = len(veto_reasons) == 0

    if investment_allowed:
        action_code = "SELECTIVE_BUY"
    elif override_pct >= 0.5:
        action_code = "CONDITIONAL_BUY"
    else:
        action_code = "STAND_DOWN"

    return {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "action_code": action_code,
        "trade_allowed": investment_allowed,
        "veto_count": len(veto_reasons),
        "warning_count": len(warnings),
        "dcl_override_pct": round(override_pct, 2),
        "veto_reasons": veto_reasons,
        "warnings": warnings,
        "flow_data": {
            "label": flow_label,
            "score": flow_score_val,
            "momentum": momentum_score,
            "liquidity": liquidity_score,
            "conviction": conviction,
        },
        "structure": {
            "regime": regime_status,
            "breadth_health": round(breadth_health, 1),
            "lcr_pct": round(lcr_pct, 1),
            "bdi_signal": bdi_signal,
            "ssi_score": round(ssi_score, 2),
            "sentinel_status": sentinel_label,
            "trade_state": trade_state_level,
        },
        "entry": {
            "suggestion": entry_suggestion,
            "tier": tier,
        },
    }


def compile_verdicts_for_portfolio(
    portfolio_path: Optional[Path] = None,
    sentinel_path: Optional[Path] = None,
    regime_status: str = "UNKNOWN",
    breadth_health: float = 0.0,
    lcr_pct: float = 30.0,
    bdi_signal: str = "CAN_BANG",
    ssi_score: float = 0.5,
    trade_state_level: str = "PROHIBITED",
    dcl_report: Optional[dict] = None,
) -> dict:
    from src.config import DATA_DIR

    if portfolio_path is None:
        portfolio_path = Path(DATA_DIR) / "output" / "portfolio_recommendations.json"
    if sentinel_path is None:
        sentinel_path = Path(DATA_DIR) / "output" / "sentinel_verdict.json"

    portfolio_data = _load_json(portfolio_path)
    sentinel_data = _load_json(sentinel_path)

    verdicts = []
    recs = portfolio_data.get("recommendations", {})
    all_symbols = []
    for tier_key in ("core", "rotation", "opportunity"):
        for item in recs.get(tier_key, []):
            sym = item.get("symbol")
            if sym and sym not in all_symbols:
                all_symbols.append(sym)

    for symbol in all_symbols:
        sym_entry = None
        for tier_key in ("core", "rotation", "opportunity"):
            for item in recs.get(tier_key, []):
                if item.get("symbol") == symbol:
                    sym_entry = item
                    break
            if sym_entry:
                break

        verdict = compile_verdict(
            symbol=symbol,
            model_b_entry=sym_entry,
            sentinel_data=sentinel_data,
            regime_status=regime_status,
            breadth_health=breadth_health,
            lcr_pct=lcr_pct,
            bdi_signal=bdi_signal,
            ssi_score=ssi_score,
            trade_state_level=trade_state_level,
            dcl_report=dcl_report,
        )
        verdicts.append(verdict)

    allowed = sum(1 for v in verdicts if v.get("trade_allowed"))
    conditional = sum(1 for v in verdicts if not v.get("trade_allowed") and v.get("dcl_override_pct", 0) >= 0.5)
    denied = sum(1 for v in verdicts if not v.get("trade_allowed") and v.get("dcl_override_pct", 0) < 0.5)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_symbols": len(verdicts),
        "allowed": allowed,
        "conditional": conditional,
        "denied": denied,
        "verdicts": verdicts,
    }

    return summary
