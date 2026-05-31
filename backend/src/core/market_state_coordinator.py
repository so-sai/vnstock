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

import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class _PydanticEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        return super().default(obj)


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
        with open(path, 'r', encoding='utf-8') as f:
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
        total = acceleration_count + continuation_count + deceleration_count + summary.get("stable_count", 0) + summary.get("lag_count", 0)

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

def _resolve_conflicts(regime_state: dict, flow_state: dict,
                       breadth_state: dict, rsi_state: dict,
                       risk_state: dict) -> list:
    flags = []

    regime_status = regime_state.get("status", "UNKNOWN")
    flow_status = flow_state.get("status", "UNKNOWN")

    if regime_status == "CRISIS" and flow_status in ("MỞ_RỘNG", "DUY_TRÌ"):
        flags.append({
            "type": "REGIME_FLOW_MISMATCH",
            "severity": "MEDIUM",
            "message": f"Regime={regime_status} nhưng Flow={flow_status} — thị trường phân kỳ",
        })

    if risk_state.get("governor_state") == "LOCKDOWN" and risk_state.get("active_vetoes", 0) > 0:
        flags.append({
            "type": "RISK_LOCKDOWN_ACTIVE",
            "severity": "HIGH",
            "message": "Risk governor đang LOCKDOWN — veto đang kích hoạt",
        })

    if breadth_state.get("health_score", 0) < 30 and flow_status == "MỞ_RỘNG":
        flags.append({
            "type": "BREADTH_FLOW_DIVERGENCE",
            "severity": "LOW",
            "message": "Độ rộng yếu nhưng flow mở rộng — tín hiệu chưa đồng thuận",
        })

    return flags


# ── Meta-state computation ──────────────────────────────────────────────────

def _compute_meta_state(regime_state: dict, flow_state: dict,
                        breadth_state: dict, rsi_state: dict,
                        risk_state: dict) -> dict:
    regime_status = regime_state.get("status", "UNKNOWN")
    flow_status = flow_state.get("status", "UNKNOWN")
    risk_gov = risk_state.get("governor_state", "UNKNOWN")
    breadth_health = breadth_state.get("health_score", 0)

    bull_count = rsi_state.get("bull_count", 0)
    bear_count = rsi_state.get("bear_count", 0)

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

def build_market_state() -> dict:
    from core.presentation import build_opportunity_view

    regime_state = _load_regime_state()
    flow_state = _load_flow_state()
    breadth_state = _load_breadth_state()
    rsi_state = _load_rsi_state()
    risk_state = _load_risk_state()
    recommendations = _load_recommendations()

    warnings = _resolve_conflicts(
        regime_state, flow_state, breadth_state, rsi_state, risk_state
    )

    meta = _compute_meta_state(
        regime_state, flow_state, breadth_state, rsi_state, risk_state
    )

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
    except Exception as e:
        logger.warning(f"Coordinator: opportunity_view build failed: {e}")

    state = {
        "timestamp": datetime.now().isoformat(),

        "market_regime": regime_state,
        "flow_state": flow_state,
        "breadth_state": breadth_state,
        "rsi_state": rsi_state,
        "risk_state": risk_state,

        "recommendations": recommendations,

        "meta_state": meta,

        "opportunity_view": opportunity_view,

        "warning_flags": warnings,
    }

    return state


# ── Export ──────────────────────────────────────────────────────────────────

def export_market_state(state: dict, path: Optional[Path] = None):
    if path is None:
        path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "market_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False, cls=_PydanticEncoder)
    print(f"  [Coordinator] Market state saved → {path}")
    return path


# ── Main entry point ───────────────────────────────────────────────────────

def run_coordinator() -> dict:
    print(f"\n{'='*60}")
    print(f"  MARKET STATE COORDINATOR v1.0")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    state = build_market_state()
    export_market_state(state)

    meta = state.get("meta_state", {})
    ov = state.get("opportunity_view", None)
    print(f"    Market Phase:     {meta.get('market_phase', 'N/A')}")
    print(f"    Liquidity:        {meta.get('liquidity_condition', 'N/A')}")
    print(f"    Risk Appetite:    {meta.get('risk_appetite', 'N/A')}")
    print(f"    Dominant Flow:    {meta.get('dominant_flow', 'N/A')}")
    print(f"    Confidence:       {meta.get('confidence', 0.0):.0%}")
    print(f"    Warnings:         {len(state.get('warning_flags', []))}")
    if ov:
        print(f"    Top Picks:        {len(ov.top_picks)}")
        print(f"    Watchlist:        {len(ov.watchlist)}")
    print(f"{'='*60}\n")

    return state


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    run_coordinator()
