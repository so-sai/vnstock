"""
market_state_coordinator.py — Central Nervous System of Sentinel OS.

Architecture:
    ENGINES (JSON outputs)
        |
        v
    COORDINATOR (Phase 1: Aggregation only)
        |
        v
    market_state.json  (single source of truth)
        |
        v
    UI / API / NARRATIVE

Phase 1 mandate:
    - collect engine outputs (read JSON files)
    - normalize schemas
    - resolve lightweight conflicts
    - compute meta_state (derived insight)
    - build unified market_state.json
    - NO scoring / NO forecasting / NO duplication of engine logic
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
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
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()
OUTPUT_DIR = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output"


# ── Engine output loaders ──────────────────────────────────────────────────


def _load_json(filename: str) -> dict:
    path = OUTPUT_DIR / filename
    if not path.exists():
        logger.warning(f"Coordinator: missing engine output {filename}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Coordinator: error reading {filename}: {e}")
        return {}


def _load_regime_state() -> dict:
    raw = _load_json("decision_board.json")
    if not raw:
        return {"status": "UNKNOWN", "score": 0.0, "confidence": 0.0}
    return {
        "status": raw.get("market_status", "UNKNOWN"),
        "score": raw.get("regime_score", 0.0),
        "confidence": raw.get("confidence", 0.0),
        "active_model": raw.get("active_model", "N/A"),
        "consensus": raw.get("consensus", "N/A"),
    }


def _load_flow_state() -> dict:
    forecast = _load_json("flow_forecast.json")
    displacement = _load_json("capital_displacement.json")
    if not forecast and not displacement:
        return {"status": "UNKNOWN", "leading_sectors": [], "lagging_sectors": []}

    state = {"status": "UNKNOWN", "leading_sectors": [], "lagging_sectors": [], "rotation_velocity": 0.0}

    if forecast:
        summary = forecast.get("projection_summary", {})
        acceleration_count = summary.get("acceleration_count", 0)
        continuation_count = summary.get("continuation_count", 0)
        deceleration_count = summary.get("deceleration_count", 0)
        (
            acceleration_count
            + continuation_count
            + deceleration_count
            + (summary.get("stable_count", 0) + summary.get("lag_count", 0))
        )

        if acceleration_count >= 2 and acceleration_count > deceleration_count:
            state["status"] = "MỞ_RỘNG"
        elif deceleration_count >= 2 and deceleration_count > acceleration_count:
            state["status"] = "THU_HẸP"
        elif continuation_count >= 2:
            state["status"] = "DUY_TRÌ"
        else:
            state["status"] = "PHÂN_HÓA"

        state["leading_sectors"] = forecast.get("leading_sectors", [])
        state["lagging_sectors"] = forecast.get("lagging_sectors", [])
        fm = forecast.get("flow_momentum", {})
        state["rotation_velocity"] = fm.get("rotation_velocity", 0.0)
        state["flow_velocity"] = fm.get("total_flow_velocity", 0.0)

    if displacement:
        state["classification"] = displacement.get("classification", "UNKNOWN")
        state["displacement_conviction"] = displacement.get("conviction", "LOW")
        sec_flow = displacement.get("sector_flow", {})
        state["sector_share"] = sec_flow.get("share", {})
        state["sector_performance"] = sec_flow.get("performance", {})

    return state


def _load_breadth_state() -> dict:
    raw = _load_json("market_pulse.json")
    if not raw:
        return {"health_score": 0.0, "advancers": 0, "decliners": 0}

    return {
        "health_score": raw.get("health_score_ma20", 0.0),
        "total_active": raw.get("total_active", 0),
        "advancers": raw.get("advancers", 0),
        "decliners": raw.get("decliners", 0),
        "unchanged": raw.get("unchanged", 0),
        "nh10_count": raw.get("nh10_count", 0),
    }


def _load_rsi_state() -> dict:
    raw = _load_json("rsi_regime_report.json")
    if not raw:
        return {"bull_count": 0, "bear_count": 0, "habitat_distribution": {}}

    return {
        "bull_count": raw.get("bull_count", 0),
        "bear_count": raw.get("bear_count", 0),
        "total_scanned": raw.get("total_scanned", 0),
        "habitat_distribution": raw.get("habitat_distribution", {}),
        "bull_leaders": raw.get("bull_leaders", [])[:5],
        "bear_leaders": raw.get("bear_leaders", [])[:5],
    }


def _load_risk_state() -> dict:
    raw = _load_json("risk_governance.json")
    if not raw:
        return {"governor_state": "UNKNOWN", "trading_multiplier": 1.0, "veto_flags": []}

    return {
        "governor_state": raw.get("governor_state", "UNKNOWN"),
        "trading_multiplier": raw.get("trading_multiplier", 1.0),
        "drawdown_pct": raw.get("drawdown", {}).get("current_drawdown_pct", 0.0),
        "leverage": raw.get("leverage", {}).get("leverage", 0.0),
        "var_95": raw.get("var", {}).get("var_95_pct", 0.0),
        "correlation_risk": raw.get("correlation", {}).get("risk_level", "LOW"),
        "veto_flags": raw.get("veto_flags", []),
        "active_vetoes": raw.get("summary", {}).get("active_vetoes", 0),
    }


def _load_market_structure() -> dict:
    raw = _load_json("market_structure.json")
    if not raw:
        return {"bdi_signal": "CAN_BANG", "lcr_pct": 30.0, "bdi_pct": 0.0}
    return {
        "bdi_signal": raw.get("bdi_signal", "CAN_BANG"),
        "lcr_pct": raw.get("lcr_pct", 30.0),
        "bdi_pct": raw.get("bdi_pct", 0.0),
        "vnindex_pct": raw.get("vnindex_pct", 0.0),
        "sbmi_pct": raw.get("sbmi_pct", 0.0),
        "ewmi_pct": raw.get("ewmi_pct", 0.0),
    }


def _load_recommendations() -> dict:
    raw = _load_json("portfolio_recommendations.json")
    if not raw:
        return {"core": [], "rotation": [], "opportunity": [], "total_scanned": 0}

    recs = raw.get("recommendations", {})
    summary = raw.get("summary", {})

    return {
        "core": recs.get("core", [])[:10],
        "rotation": recs.get("rotation", [])[:10],
        "opportunity": recs.get("opportunity", [])[:10],
        "total_scanned": summary.get("total_scanned", 0),
        "core_count": summary.get("core_count", 0),
        "rotation_count": summary.get("rotation_count", 0),
        "opportunity_count": summary.get("opportunity_count", 0),
        "top_core": summary.get("top_core", []),
    }


# ── Conflict resolution (lightweight) ──────────────────────────────────────


def _resolve_conflicts(regime_state: dict, flow_state: dict, breadth_state: dict, rsi_state: dict, risk_state: dict) -> list:
    flags = []

    regime_status = regime_state.get("status", "UNKNOWN")
    flow_status = flow_state.get("status", "UNKNOWN")

    if regime_status == "CRISIS" and flow_status in ("MỞ_RỘNG", "DUY_TRÌ"):
        flags.append(
            {
                "type": "REGIME_FLOW_MISMATCH",
                "severity": "MEDIUM",
                "message": f"Regime={regime_status} nhưng Flow={flow_status} — thị trường phân kỳ",
            }
        )

    if risk_state.get("governor_state") == "LOCKDOWN" and risk_state.get("active_vetoes", 0) > 0:
        flags.append(
            {
                "type": "RISK_LOCKDOWN_ACTIVE",
                "severity": "HIGH",
                "message": "Risk governor đang LOCKDOWN — veto đang kích hoạt",
            }
        )

    if breadth_state.get("health_score", 0) < 30 and flow_status == "MỞ_RỘNG":
        flags.append(
            {
                "type": "BREADTH_FLOW_DIVERGENCE",
                "severity": "LOW",
                "message": "Độ rộng yếu nhưng flow mở rộng — tín hiệu chưa đồng thuận",
            }
        )

    return flags


# ── Meta-state computation ──────────────────────────────────────────────────


def _compute_meta_state(regime_state: dict, flow_state: dict, breadth_state: dict, rsi_state: dict, risk_state: dict) -> dict:
    regime_status = regime_state.get("status", "UNKNOWN")
    flow_status = flow_state.get("status", "UNKNOWN")
    risk_gov = risk_state.get("governor_state", "UNKNOWN")
    breadth_health = breadth_state.get("health_score", 0)

    rsi_state.get("bull_count", 0)
    rsi_state.get("bear_count", 0)

    market_phase = "KHÔNG_XÁC_ĐỊNH"
    liquidity_condition = "TRUNG_TÍNH"
    risk_appetite = "TRUNG_TÍNH"
    dominant_flow = "KHÔNG_RÕ"
    confidence = 0.5

    if regime_status in ("TRENDING", "MỞ_RỘNG_TÍCH_CỰC"):
        if flow_status == "MỞ_RỘNG":
            market_phase = "MỞ_RỘNG_TÍCH_CỰC"
            liquidity_condition = "CẢI_THIỆN"
            risk_appetite = "MỞ_RỘNG"
            confidence = 0.8
        elif flow_status == "DUY_TRÌ":
            market_phase = "DUY_TRÌ_ỔN_ĐỊNH"
            liquidity_condition = "ỔN_ĐỊNH"
            risk_appetite = "DUY_TRÌ"
            confidence = 0.7
        else:
            market_phase = "TĂNG_TRƯỞNG_PHÂN_HÓA"
            liquidity_condition = "PHÂN_HÓA"
            risk_appetite = "THẬN_TRỌNG"
            confidence = 0.6

    elif regime_status == "RANGING":
        if flow_status == "MỞ_RỘNG":
            market_phase = "TÍCH_LŨY_TÍCH_CỰC"
            liquidity_condition = "CẢI_THIỆN"
            risk_appetite = "THẬN_TRỌNG_MỞ_RỘNG"
            confidence = 0.6
        else:
            market_phase = "TÍCH_LŨY"
            liquidity_condition = "YẾU"
            risk_appetite = "THẬN_TRỌNG"
            confidence = 0.5

    elif regime_status == "CRISIS":
        market_phase = "SUY_GIẢM"
        liquidity_condition = "KÉM"
        risk_appetite = "ĐÓNG"
        confidence = 0.4

    if risk_gov in ("LOCKDOWN", "RESTRICTED"):
        market_phase = f"{market_phase}_CÓ_KÍCH_HOẠT_RỦI_RO" if market_phase else "KÍCH_HOẠT_RỦI_RO"
        risk_appetite = "ĐÓNG"
        confidence = max(0.1, confidence - 0.3)

    if breadth_health < 30:
        confidence = max(0.1, confidence - 0.15)

    leading = flow_state.get("leading_sectors", [])
    if leading:
        dominant_flow = " → ".join(leading[:3])

    return {
        "market_phase": market_phase,
        "liquidity_condition": liquidity_condition,
        "risk_appetite": risk_appetite,
        "dominant_flow": dominant_flow,
        "confidence": round(confidence, 2),
    }


# ── Build unified state ────────────────────────────────────────────────────


def _compute_drift_label(structure: dict) -> str:
    if not structure:
        return "KHONG XAC DINH"
    bdi = abs(structure.get("bdi_pct", 0))
    lcr = structure.get("lcr_pct", 30.0)
    signal = structure.get("bdi_signal", "CAN_BANG")
    if bdi > 10 and lcr > 35 and signal != "CAN_BANG":
        return f"STRUCTURAL — index +{bdi:.0f}% lech breadth, LCR {lcr:.0f}%"
    if bdi > 5 or lcr > 40:
        return f"TRANSIENT — {signal}, LCR {lcr:.0f}%"
    return "NONE — thong so dong bo"


def _flow_status_to_bias_score(flow_status: str) -> float:
    mapping = {
        "MỞ_RỘNG": 1.0,
        "MỞ_RỘNG_TÍCH_CỰC": 1.0,
        "DUY_TRÌ": 0.65,
        "ỔN_ĐỊNH": 0.65,
        "TRUNG_TÍNH": 0.5,
        "PHÂN_HÓA": 0.35,
        "THU_HẸP": 0.1,
        "UNKNOWN": 0.4,
    }
    return mapping.get(flow_status, 0.4)


def build_market_state() -> dict:
    from core.presentation import build_opportunity_view
    from core.presentation.asset_preference_mapping import compute_asset_preference
    from core.presentation.decision_closure_layer import compute_dcl
    from core.presentation.direction_persistence_layer import compute_direction_persistence
    from core.presentation.directional_bias_extractor import compute_directional_bias
    from core.presentation.state_stability_index import compute_ssi
    from core.presentation.trade_state_policy import compile_action_policy, compute_trade_state
    from core.presentation.transition_trigger_layer import compute_transition_trigger
    from core.presentation.vi_localizer import localize_market_state

    regime_state = _load_regime_state()
    flow_state = _load_flow_state()
    breadth_state = _load_breadth_state()
    rsi_state = _load_rsi_state()
    risk_state = _load_risk_state()
    recommendations = _load_recommendations()
    structure_state = _load_market_structure()

    warnings = _resolve_conflicts(regime_state, flow_state, breadth_state, rsi_state, risk_state)

    meta = _compute_meta_state(regime_state, flow_state, breadth_state, rsi_state, risk_state)

    drift_label = _compute_drift_label(structure_state)

    trade_state = compute_trade_state(
        regime_status=regime_state.get("status", "UNKNOWN"),
        regime_score=regime_state.get("score", 0.0),
        breadth_health=breadth_state.get("health_score", 0.0),
        lcr_pct=structure_state.get("lcr_pct"),
        flow_status=flow_state.get("status", "UNKNOWN"),
        risk_governor=risk_state.get("governor_state", "NORMAL"),
        bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
    )

    action_policy = compile_action_policy(trade_state.level)

    ssi = compute_ssi(
        regime_status=regime_state.get("status", "UNKNOWN"),
        regime_score=regime_state.get("score", 0.0),
        breadth_health=breadth_state.get("health_score", 0.0),
        lcr_pct=structure_state.get("lcr_pct", 30.0),
        bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
        drift_label=drift_label,
        flow_status=flow_state.get("status", "UNKNOWN"),
        flow_velocity=flow_state.get("flow_velocity", 0.0),
        rotation_velocity=flow_state.get("rotation_velocity", 0.0),
    )

    sapm = compute_asset_preference(
        trade_state_level=trade_state.level,
        regime_status=regime_state.get("status", "UNKNOWN"),
        drift_label=drift_label,
        lcr_pct=structure_state.get("lcr_pct", 30.0),
        breadth_health=breadth_state.get("health_score", 0.0),
        bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
    )

    sentinel_data = _load_json("sentinel_verdict.json")
    sentinel_green = sentinel_data.get("final_status", "").startswith("GREEN") if sentinel_data else False
    sentinel_label = sentinel_data.get("final_status", "UNKNOWN") if sentinel_data else "UNKNOWN"
    flow_label = flow_state.get("status", "UNKNOWN")
    flow_bias_score = _flow_status_to_bias_score(flow_label)

    dcl = compute_dcl(
        sentinel_green=sentinel_green,
        sentinel_label=sentinel_label,
        flow_bias_score=flow_bias_score,
        flow_label=flow_label,
        breadth_health=breadth_state.get("health_score", 0.0),
        lcr_pct=structure_state.get("lcr_pct", 30.0),
        bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
        ssi_score=ssi.score,
        trade_state_level=trade_state.level,
        trade_state_score=trade_state.score,
    )

    dbe = compute_directional_bias(
        regime_status=regime_state.get("status", "UNKNOWN"),
        trade_state_level=trade_state.level,
        breadth_health=breadth_state.get("health_score", 0.0),
        lcr_pct=structure_state.get("lcr_pct", 30.0),
        bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
        flow_bias_score=flow_bias_score,
        flow_label=flow_label,
        ssi_score=ssi.score,
        dcl_verdict=dcl.verdict,
        dcl_score=dcl.score,
        compensations_triggered=dcl.compensations_triggered,
    )

    dpl = compute_direction_persistence(dbe)

    ttl = compute_transition_trigger(
        dpl=dpl,
        dbe=dbe,
        regime_status=regime_state.get("status", "UNKNOWN"),
    )

    verdict_summary = None
    try:
        from core.cognition.investment_verdict_compiler import compile_verdicts_for_portfolio

        verdict_summary = compile_verdicts_for_portfolio(
            regime_status=regime_state.get("status", "UNKNOWN"),
            breadth_health=breadth_state.get("health_score", 0.0),
            lcr_pct=structure_state.get("lcr_pct", 30.0),
            bdi_signal=structure_state.get("bdi_signal", "CAN_BANG"),
            ssi_score=ssi.score,
            trade_state_level=trade_state.level,
            dcl_report=dcl.model_dump(),
        )
    except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
        logger.warning(f"Coordinator: verdict compilation failed: {e}")

    opportunity_view = None
    try:
        meta_posture = meta.get("risk_appetite", "")
        posture_map = {
            "MỞ_RỘNG": "TĂNG_TỶ_TRỌNG",
            "DUY_TRÌ": "GIỮ_VỊ_THẾ",
            "THẬN_TRỌNG_MỞ_RỘNG": "TĂNG_TỶ_TRỌNG",
            "THẬN_TRỌNG": "QUAN_SÁT",
            "ĐÓNG": "GIẢM_RỦI_RO",
            "TRUNG_TÍNH": "GIỮ_VỊ_THẾ",
        }
        decision_posture = posture_map.get(meta_posture, "QUAN_SÁT")
        opportunity_view = build_opportunity_view(
            recommendations=recommendations,
            meta_state={
                "market_regime": regime_state,
                "risk_state": risk_state,
                "meta_state": meta,
            },
            decision_posture=decision_posture,
        )
    except (ImportError, AttributeError, TypeError, ValueError, KeyError) as e:
        logger.warning(f"Coordinator: opportunity_view build failed: {e}")

    state = {
        "timestamp": datetime.now().isoformat(),
        "market_regime": regime_state,
        "flow_state": flow_state,
        "breadth_state": breadth_state,
        "rsi_state": rsi_state,
        "risk_state": risk_state,
        "market_structure": structure_state,
        "recommendations": recommendations,
        "meta_state": meta,
        "trade_state": trade_state.model_dump(),
        "state_stability": ssi.model_dump(),
        "asset_preference": sapm.model_dump(),
        "action_policy": action_policy.model_dump(),
        "decision_closure": dcl.model_dump(),
        "directional_bias": dbe.model_dump(),
        "direction_persistence": dpl.model_dump(),
        "transition_trigger": ttl.model_dump(),
        "investment_verdicts": verdict_summary,
        "opportunity_view": opportunity_view,
        "warning_flags": warnings,
    }

    return localize_market_state(state)


# ── Export ──────────────────────────────────────────────────────────────────


def export_market_state(state: dict, path: Path | None = None):
    if path is None:
        path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "market_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    from src.database.db_core import safe_json_dump

    with open(path, "w", encoding="utf-8") as f:
        safe_json_dump(state, f, indent=2, ensure_ascii=False)
    print(f"  [Coordinator] Market state saved → {path}")
    return path


# ── Main entry point ───────────────────────────────────────────────────────


def run_coordinator() -> dict:
    print(f"\n{'=' * 60}")
    print("  MARKET STATE COORDINATOR v1.0")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")

    state = build_market_state()
    export_market_state(state)

    meta = state.get("meta_state", {})
    ts = state.get("trade_state", {})
    ssi = state.get("state_stability", {})
    sapm = state.get("asset_preference", {})
    ap = state.get("action_policy", {})
    db = state.get("directional_bias", {})
    dp = state.get("direction_persistence", {})
    tt = state.get("transition_trigger", {})
    dcl = state.get("decision_closure", {})
    vd = state.get("investment_verdicts", {})
    ov = state.get("opportunity_view", None)
    print(f"    {ts.get('level_vi', '?')}  (score={ts.get('score', 0):.2f}, max={ts.get('max_exposure_pct', 0):.0f}%)")
    print(f"    Tin cậy state:    {ssi.get('label_vi', '?')} (SSI={ssi.get('score', 0):.2f})")
    print(f"    Xu hướng TS:      {sapm.get('dominant_bias_vi', '?')}")
    print(f"    Kỷ luật GD:       {ap.get('label_vi', '?')}")
    print(
        f"    DCL:              {dcl.get('verdict_vi', '?')} (score={dcl.get('score', 0):.2f}, "
        f"override={dcl.get('compensations_triggered', 0)})"
    )
    print(
        f"    Hướng thị trường: {db.get('label_vi', '?')} (strength={db.get('bias_strength', 0):.2f}, "
        f"confidence={db.get('bias_confidence', 0):.2f})"
    )
    print(f"    Lực chi phối:     {db.get('dominant_force_vi', '?')}")
    print(
        f"    DPL:              {dp.get('label_vi', '?')} (stability={dp.get('dbe_stability_score', 0):.2f}, "
        f"flicker={dp.get('flicker_risk_code', '?')})"
    )
    print(
        f"    TTL:              {tt.get('label_vi', '?')} (type={tt.get('transition_type_vi', '?')}, "
        f"confidence={tt.get('trigger_confidence', 0):.2f})"
    )
    print(
        f"    Phán quyết:       {vd.get('allowed', 0)}/{vd.get('total_symbols', 0)} mã được giải ngân "
        f"(+{vd.get('conditional', 0)} có điều kiện)"
    )
    print(f"    Pha thị trường:   {meta.get('market_phase', '?')}")
    print(f"    Rủi ro:           {meta.get('risk_appetite', '?')}")
    print(f"    Thanh khoản:      {meta.get('liquidity_condition', '?')}")
    print(f"    Dòng tiền:        {meta.get('dominant_flow', '?')}")
    print(f"    Tự tin:           {meta.get('confidence', 0.0):.0%}")
    print(f"    Cảnh báo:         {len(state.get('warning_flags', []))}")
    if ov:
        print(f"    Top Picks:        {len(ov.top_picks)}")
        print(f"    Watchlist:        {len(ov.watchlist)}")
    print(f"{'=' * 60}\n")

    return state


if __name__ == "__main__":
    if sys.platform == "win32":
        import io

        if isinstance(sys.stdout, io.TextIOWrapper):
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except OSError, AttributeError, ValueError:
                    logger.debug("stdout.reconfigure(utf-8) không khả dụng — giữ nguyên encoding")
        elif hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    run_coordinator()
