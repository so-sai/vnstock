"""Weekly Cognitive Report — READ-ONLY aggregation layer.

Not an engine. Not a learning layer. Not a scoring layer.

Aggregates:
    - Market state (regime + LCI + opportunity)
    - Gold (VN + global + premium regime)
    - CAO trust (validation state + shadow stats)

Builds 1 narrative summary_vi. Never overrides CAO verdict.
Never modifies telemetry. Never writes to production tables.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.cao_validation.activation_gate import run_full_validation
from src.cao_validation.trust_accumulator import get_accumulator
from src.core.data_quality.detectors.chained_assignment import capture_chained_assignments
from src.core.market_state_coordinator import build_market_state
from src.engine.regime_engine import detect_regime
from src.shadow_cao.storage import get_shadow_stats

# ====================================================================
# 1. MARKET AGGREGATION
# ====================================================================

def aggregate_market() -> dict:
    """Read market state: regime, LCI, opportunity bias.

    Read-only. Does not modify any state.
    """
    result = {
        "regime": "UNKNOWN",
        "regime_score": 0.0,
        "lci": None,
        "top_sectors": [],
        "opportunity_bias": "NEUTRAL",
        "source": "live",
    }
    try:
        regime = detect_regime()
        result["regime"] = regime.get("status", "UNKNOWN")
        result["regime_score"] = regime.get("regime_score", 0.0)
        result["regime_details"] = regime.get("details", {})
    except Exception as e:
        logger.warning("[WEEKLY] Market regime read failed: %s", e)
        result["regime_error"] = str(e)
    try:
        state = build_market_state()
        result["lci"] = state.get("liquidity_condition", "UNKNOWN")
        result["risk_appetite"] = state.get("risk_appetite", "UNKNOWN")
        result["market_phase"] = state.get("market_phase", "UNKNOWN")
        result["dominant_flow"] = state.get("dominant_flow", "UNKNOWN")
    except Exception as e:
        logger.warning("[WEEKLY] Market state read failed: %s", e)
    try:
        from src.services.actionable_intelligence_service import get_opportunity_queue
        opps = get_opportunity_queue(top_n=5)
        if isinstance(opps, list):
            result["opportunity_count"] = len(opps)
            sectors = set()
            for o in opps:
                if isinstance(o, dict) and "sector" in o:
                    sectors.add(o["sector"])
            result["top_sectors"] = sorted(sectors)[:5]
        elif isinstance(opps, dict):
            result["opportunity_count"] = len(opps.get("opportunities", []))
            sectors = set()
            for o in opps.get("opportunities", []):
                if isinstance(o, dict) and "sector" in o:
                    sectors.add(o["sector"])
            result["top_sectors"] = sorted(sectors)[:5]
    except Exception as e:
        logger.warning("[WEEKLY] Opportunity read failed: %s", e)
        try:
            from src.engine.elite_scanner import get_elite_scan
            scan = get_elite_scan()
            if isinstance(scan, list):
                result["opportunity_count"] = len(scan)
        except Exception:
            pass
    return result


# ====================================================================
# 2. GOLD AGGREGATION
# ====================================================================

def aggregate_gold() -> dict:
    """Read gold state: VN + global + premium regime.

    Read-only. Does not modify any state.
    """
    result = {
        "regime": "UNKNOWN",
        "driver": "UNKNOWN",
        "premium_regime": "NORMAL",
        "stress_signal": False,
    }
    try:
        from core.macro.gold_regime_engine import analyze_gold_regime
        regime = analyze_gold_regime()
        if isinstance(regime, dict):
            result["regime"] = regime.get("regime", "UNKNOWN")
            result["driver"] = regime.get("driver", "UNKNOWN")
    except Exception as e:
        logger.warning("[WEEKLY] Gold regime read failed: %s", e)
    try:
        from core.macro.gold_spread_engine import analyze_domestic_premium
        premium = analyze_domestic_premium()
        if isinstance(premium, dict):
            result["premium_regime"] = premium.get("regime", "NORMAL")
            result["premium_pct"] = premium.get("premium_pct", 0.0)
            result["stress_signal"] = premium.get("regime") in ("SURGE", "DANGER")
    except Exception as e:
        logger.warning("[WEEKLY] Gold premium read failed: %s", e)
    try:
        from src.services.macro.gold_world_service import fetch_world_gold_live
        world = fetch_world_gold_live()
        if isinstance(world, dict):
            result["xau_usd"] = world.get("price")
            result["xau_change_pct"] = world.get("change_pct")
    except Exception as e:
        logger.warning("[WEEKLY] World gold read failed: %s", e)
    return result


# ====================================================================
# 3. CAO TRUST AGGREGATION
# ====================================================================

def aggregate_trust() -> dict:
    """Read CAO validation + shadow state.

    Read-only. Never modifies CAO state.
    """
    result = {
        "status": "NO_DATA",
        "consistency_score": 0.0,
        "stability": 0.0,
        "decision_count": 0,
        "regime_states": {},
    }
    try:
        report = run_full_validation()
        result["status"] = "PROMOTABLE" if report.overall_promotable else "BLOCKED"
        result["verdicts"] = [
            {"regime": v.regime, "can_promote": v.can_promote, "failures": v.failures}
            for v in report.promotion_verdicts
        ]
        for v in report.promotion_verdicts:
            result.setdefault("regime_states", {})[v.regime] = {
                "promotable": v.can_promote,
                "failures": v.failures,
            }
    except Exception as e:
        logger.warning("[WEEKLY] CAO validation read failed: %s", e)
        result["validation_error"] = str(e)
    try:
        acc = get_accumulator()
        states = acc.get_state()
        for regime, s in states.items():
            result.setdefault("regime_states", {})[regime] = {
                "confidence": s.confidence,
                "consistency": s.mean_consistency,
                "samples": s.total_samples,
                "drift": s.drift_score,
            }
            result["consistency_score"] = max(
                result["consistency_score"],
                s.mean_consistency,
            )
    except Exception as e:
        logger.warning("[WEEKLY] Trust state read failed: %s", e)
    try:
        stats = get_shadow_stats()
        result["decision_count"] = stats.get("decision_logs", 0)
    except Exception as e:
        logger.warning("[WEEKLY] Shadow stats read failed: %s", e)
    try:
        from src.core.data_quality import QualityScoreEngine
        dq = QualityScoreEngine().compute_report()
        result["data_integrity"] = {
            "dis": dq.integrity_score,
            "divi": dq.divi,
            "events": dq.events_in_window,
            "method": dq.integrity_method,
            "recommendation": dq.recommendation,
        }
    except Exception as e:
        logger.warning("[WEEKLY] DQ monitor read failed: %s", e)
    return result


# ====================================================================
# 4. SEMANTIC LAYER — backend-owned meaning for UI
# ====================================================================
# Every response section gets:
#   label_vi      : short Vietnamese label (2-4 words)
#   explanation_vi: context-aware Vietnamese explanation (one sentence)
#   severity      : 0.0 (calm) → 1.0 (critical)
#
# Rule: Backend = meaning generator. Frontend = renderer only.
# NO re-interpretation in UI.

_SEVERITY_MAP = {
    "clean": 0.05,
    "mild_noise": 0.15,
    "degraded": 0.35,
    "caution": 0.45,
    "unstable": 0.60,
    "caution_severe": 0.70,
    "block_promotion": 0.85,
}


def _market_semantic(market: dict) -> dict:
    regime = market.get("regime", "UNKNOWN")
    lci = market.get("lci", "UNKNOWN")
    risk = market.get("risk_appetite", "UNKNOWN")
    phase = market.get("market_phase", "")
    flow = market.get("dominant_flow", "")
    parts = []
    if phase:
        parts.append(f"pha {phase}")
    if flow:
        parts.append(f"dòng {flow}")
    ctx = ", ".join(parts) if parts else "không rõ trạng thái"

    label_map = {
        "TRENDING": "thị trường có xu hướng",
        "RANGING": "thị trường đi ngang",
        "CRISIS": "thị trường khủng hoảng",
        "RISK_OFF": "thị trường phòng thủ",
    }
    label = label_map.get(regime, f"thị trường {regime}")

    if "CRISIS" in regime:
        expl = "Đang trong giai đoạn căng thẳng, rủi ro hệ thống cao."
        sev = 0.75
    elif "RISK_OFF" in lci or "ĐÓNG" in risk:
        expl = f"Dòng tiền đang rút khỏi rủi ro ({ctx}), thanh khoản co hẹp."
        sev = 0.55
    elif regime == "TRENDING":
        expl = f"Đang có xu hướng rõ rệt ({ctx}), cơ hội theo trend."
        sev = 0.20
    else:
        expl = f"Đi ngang ({ctx}), chưa có tín hiệu xu hướng rõ."
        sev = 0.30
    return {"label_vi": label, "explanation_vi": expl, "severity": sev}


def _gold_semantic(gold: dict) -> dict:
    regime = gold.get("regime", "UNKNOWN")
    premium = gold.get("premium_regime", "NORMAL")
    premium_pct = gold.get("premium_pct", 0)
    stress = gold.get("stress_signal", False)
    driver = gold.get("driver", "UNKNOWN")

    if stress:
        return {
            "label_vi": "vàng căng thẳng",
            "explanation_vi": f"Vàng nội địa đang chịu áp lực lớn (phí bảo hiểm {premium_pct:.0f}%), chênh lệch với thế giới ở mức báo động.",
            "severity": 0.80,
        }
    if premium == "SURGE":
        return {
            "label_vi": "vàng nội địa tăng nóng",
            "explanation_vi": f"Giá vàng trong nước cao hơn thế giới {premium_pct:.0f}%, phản ánh cầu trú ẩn nội địa mạnh.",
            "severity": 0.55,
        }
    if regime == "BULLISH":
        return {
            "label_vi": "vàng tăng giá",
            "explanation_vi": f"Vàng thế giới đang trong xu hướng tăng, driver chính là {driver}.",
            "severity": 0.25,
        }
    return {
        "label_vi": "vàng ổn định",
        "explanation_vi": "Thị trường vàng chưa có biến động bất thường.",
        "severity": 0.10,
    }


def _trust_semantic(trust: dict) -> dict:
    status = trust.get("status", "NO_DATA")
    dis = trust.get("data_integrity", {}).get("dis", 1.0)
    divi = trust.get("data_integrity", {}).get("divi", 0.0)
    dq_rec = trust.get("data_integrity", {}).get("recommendation", "clean")

    if status == "PROMOTABLE":
        return {
            "label_vi": "CAO sẵn sàng",
            "explanation_vi": "Hệ thống CAO đã đủ điều kiện để kích hoạt ở ít nhất một chế độ thị trường.",
            "severity": 0.10,
        }
    if status == "BLOCKED":
        if dis < 0.50:
            return {
                "label_vi": "dữ liệu không tin cậy",
                "explanation_vi": f"Dữ liệu đầu vào có độ tin cậy thấp (DIS={dis:.2f}), CAO chưa thể kích hoạt.",
                "severity": 0.75,
            }
        if divi > 0.30:
            return {
                "label_vi": "dữ liệu biến động cao",
                "explanation_vi": f"Chất lượng dữ liệu đang biến động mạnh (DIVI={divi:.2f}), CAO bị chặn để tránh nhiễu.",
                "severity": 0.60,
            }
        return {
            "label_vi": "CAO chưa sẵn sàng",
            "explanation_vi": f"Hệ thống chưa tích lũy đủ dữ liệu để kích hoạt CAO (DIS={dis:.2f}).",
            "severity": 0.40,
        }
    return {
        "label_vi": "chưa có dữ liệu",
        "explanation_vi": "Chưa có đủ thông tin để đánh giá trạng thái CAO.",
        "severity": 0.20,
    }


def _overall_severity(market_sev: float, gold_sev: float, trust_sev: float) -> float:
    return max(market_sev, gold_sev, trust_sev)


def _overall_label(sev: float) -> str:
    if sev < 0.15:
        return "ổn định"
    if sev < 0.35:
        return "thận trọng"
    if sev < 0.55:
        return "cảnh báo nhẹ"
    if sev < 0.75:
        return "cảnh báo"
    return "rủi ro cao"


# ====================================================================
# 5. NARRATIVE BUILDER
# ====================================================================

def _risk_label(regime: str, lci: str, risk_appetite: str) -> str:
    if "CRISIS" in regime or "ĐÓNG" in risk_appetite:
        return "CAO"
    if "TRENDING" in regime and "MỞ_RỘNG" in risk_appetite:
        return "THẤP"
    return "TRUNG_BÌNH"


def build_narrative(market: dict, gold: dict, trust: dict) -> str:
    """Build a single Vietnamese summary string.

    TEXT COMPOSER ONLY. No ML. No scoring. No weight update.
    """
    regime = market.get("regime", "UNKNOWN")
    lci = market.get("lci", "UNKNOWN")
    risk_appetite = market.get("risk_appetite", "UNKNOWN")
    gold_regime = gold.get("regime", "UNKNOWN")
    premium = gold.get("premium_regime", "NORMAL")
    trust_status = trust.get("status", "NO_DATA")
    risk = _risk_label(regime, lci, risk_appetite)
    parts = []
    parts.append(f"Thị trường: {regime}")
    if market.get("market_phase"):
        parts.append(f"pha {market['market_phase']}")
    if market.get("dominant_flow"):
        parts.append(f"dòng tiền {market['dominant_flow']}")
    parts.append(f"Rủi ro: {risk}")
    parts.append(f"Vàng: {gold_regime}")
    if premium == "SURGE":
        parts.append("(phí bảo hiểm nội địa tăng)")
    elif premium == "DISCOUNT":
        parts.append("(chiết khấu nội địa)")
    cao_status_str = {
        "PROMOTABLE": "CAO có thể kích hoạt",
        "BLOCKED": "CAO chưa sẵn sàng",
    }.get(trust_status, f"CAO: {trust_status}")
    parts.append(cao_status_str)
    return " | ".join(parts) + "."


# ====================================================================
# 5. FULL WEEKLY REPORT
# ====================================================================

def build_weekly_report() -> dict:
    """Build the complete weekly cognitive report.

    READ-ONLY. No side effects. No writes to any DB.
    Every section includes ``label_vi``, ``explanation_vi``, ``severity``
    for the UI semantic layer.

    PSR integration: captures a system-state snapshot + writes an audit
    entry for every invocation.  Both are non-blocking (errors swallowed).
    """
    with capture_chained_assignments():
        market = aggregate_market()
        gold = aggregate_gold()
        trust = aggregate_trust()
    narrative = build_narrative(market, gold, trust)
    market_sem = _market_semantic(market)
    gold_sem = _gold_semantic(gold)
    trust_sem = _trust_semantic(trust)
    market["label_vi"] = market_sem["label_vi"]
    market["explanation_vi"] = market_sem["explanation_vi"]
    market["severity"] = market_sem["severity"]
    gold["label_vi"] = gold_sem["label_vi"]
    gold["explanation_vi"] = gold_sem["explanation_vi"]
    gold["severity"] = gold_sem["severity"]
    trust["label_vi"] = trust_sem["label_vi"]
    trust["explanation_vi"] = trust_sem["explanation_vi"]
    trust["severity"] = trust_sem["severity"]
    overall_sev = _overall_severity(market_sem["severity"], gold_sem["severity"], trust_sem["severity"])
    report = {
        "timestamp": datetime.now().isoformat(),
        "market": market,
        "gold": gold,
        "trust": trust,
        "summary_vi": narrative,
        "label_vi": _overall_label(overall_sev),
        "explanation_vi": f"Thị trường {market_sem['label_vi']}. Vàng {gold_sem['label_vi']}. CAO: {trust_sem['label_vi']}.",
        "severity": overall_sev,
    }
    _psr_audit(report, trust)
    return report


def _psr_audit(report: dict, trust: dict) -> None:
    """Non-blocking PSR snapshot + audit entry."""
    try:
        from src.core.psr.audit import DecisionAuditTrail
        from src.core.psr.snapshot import SystemStateSnapshotter
        snapper = SystemStateSnapshotter()
        snap = snapper.capture()
        snapper.persist(snap)
        DecisionAuditTrail().record(
            source="weekly_report",
            label_vi=report.get("label_vi", ""),
            explanation_vi=report.get("explanation_vi", ""),
            severity=report.get("severity", 0.0),
            regime=report.get("market", {}).get("regime", "UNKNOWN"),
            dis=trust.get("data_integrity", {}).get("dis", 1.0),
            divi=trust.get("data_integrity", {}).get("divi", 0.0),
            trust_status=trust.get("status", "NO_DATA"),
            snapshot_id=snap.snapshot_id,
            extra={"summary_vi": report.get("summary_vi", "")[:200]},
        )
    except Exception as e:
        logger.debug("[PSR] Audit skipped: %s", e)
