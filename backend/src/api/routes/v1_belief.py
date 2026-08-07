"""v1_belief.py — Dashboard API: Sentinel Telemetry + Governor Belief + Rejected Signals.

Song ngữ output — mỗi endpoint trả về cấu trúc HCI với localization EN/VI.
Tuân thủ CLI-First Law: không nhúng logic, chỉ gọi backend module.
"""

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

logger = logging.getLogger("PTCK_API_BELIEF")

router = APIRouter()


def _hydrate_path():
    current = Path(__file__).resolve().parent
    root = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root = current
            break
        current = current.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    backend_dir = root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root


PROJECT_ROOT = _hydrate_path()


# ── Sentinel Telemetry ────────────────────────────────────────────────


@router.get("/sentinel/status")
async def get_sentinel_status():
    """Trạng thái hạ tầng dữ liệu: tier, staleness, trust score.

    Trả về danh sách symbol với source tier, staleness_hours,
    api_status, trust_score, force_hdr.
    """
    try:
        from src.core.meta_evidence import get_meta_evidence
        from src.data.fallback_resolver import resolve

        # Kiểm tra VNINDEX + 5 mã đầu watchlist
        symbols = ["VNINDEX", "FPT", "VCB", "HPG", "VNM", "TCB"]
        today = datetime.now().strftime("%Y-%m-%d")
        meta = get_meta_evidence()
        cv = meta.calibration_vector

        results = []
        for sym in symbols:
            row, m = resolve(sym, today)
            results.append(
                {
                    "symbol": sym,
                    "source": m.get("source", "UNKNOWN"),
                    "provider": m.get("provider", "?"),
                    "fallback": m.get("fallback", False),
                    "is_synthetic": m.get("is_synthetic", False),
                    "staleness_hours": m.get("staleness_hours", -1),
                    "api_status": m.get("api_status", "UNKNOWN"),
                    "trust_score": round(meta.effective_trust, 3),
                    "force_hdr": m.get("force_hdr", None),
                }
            )

        return {
            "timestamp": datetime.now().isoformat(),
            "date": today,
            "symbols": results,
            "overall_synthetic": any(r["is_synthetic"] for r in results),
            "overall_fallback": any(r["fallback"] for r in results),
            "calibration_vector": cv,
        }
    except (ImportError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        logger.warning(f"[API] sentinel/status error: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "symbols": [],
            "overall_synthetic": True,
            "overall_fallback": True,
            "error": str(e),
        }


@router.get("/sentinel/freshness")
async def get_data_freshness():
    """Chi tiết độ tươi dữ liệu theo symbol."""
    try:
        from src.database.data_freshness import get_stale_symbols

        today = datetime.now().strftime("%Y-%m-%d")
        stale = get_stale_symbols(max_stale=48.0, date=today)
        return {
            "date": today,
            "stale_symbols": stale,
            "stale_count": len(stale),
            "max_stale_hours": 48.0,
        }
    except (ImportError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        return {"date": datetime.now().strftime("%Y-%m-%d"), "error": str(e)}


# ── Governor Belief Bridge ──────────────────────────────────────────


@router.get("/belief/quantstats")
async def get_quantstats_belief(window: int = Query(30, ge=5, le=120)):
    """QuantStats belief report: calibration penalty, effective trust, DOC, IG."""
    try:
        from src.core.quantstats_bridge import QuantStatsBridge

        bridge = QuantStatsBridge(window_days=window)
        report = bridge.run_all()
        cal = report.get("calibration", {})
        return {
            "timestamp": report.get("timestamp"),
            "window_days": window,
            "sharpe_live_smoothed": cal.get("sharpe_live_smoothed", 0),
            "sharpe_vs_random": cal.get("sharpe_vs_random", 0),
            "outlier_win_ratio": cal.get("outlier_win_ratio", 0),
            "calibration_penalty": cal.get("calibration_penalty", 0),
            "action": cal.get("action", "NONE"),
            "reason": cal.get("reason", ""),
            "doc_index": cal.get("doc_index", 0),
            "doc_wins": cal.get("doc_wins", 0),
            "doc_losses": cal.get("doc_losses", 0),
            "cumulative_information_gain": cal.get("cumulative_information_gain", 0),
            "live": report.get("live", {}),
            "rejected": report.get("rejected", {}),
            "random_baseline": report.get("random_baseline", {}),
        }
    except (ImportError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        logger.warning(f"[API] belief/quantstats error: {e}")
        return {
            "error": str(e),
            "calibration_penalty": 1.0,
            "action": "ABORT",
        }


@router.get("/belief/meta")
async def get_belief_meta():
    """Meta evidence: effective trust per symbol."""
    try:
        from src.core.meta_evidence import get_meta_evidence

        meta = get_meta_evidence()
        symbols = ["VNINDEX", "FPT", "VCB", "HPG", "VNM", "TCB"]
        results = {}
        for sym in symbols:
            ev = meta.get_effective_trust(sym)
            results[sym] = {
                "trust_score": round(ev.get("trust_score", 0.5), 4),
                "data_quality": round(ev.get("data_quality_score", 0.5), 4),
                "calibration_penalty": round(ev.get("calibration_penalty", 0), 4),
                "novelty": round(ev.get("novelty_score", 0), 4),
                "effective_trust": round(ev.get("effective_trust", 0.5), 4),
            }
        return {
            "timestamp": datetime.now().isoformat(),
            "symbols": results,
        }
    except (ImportError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        return {"error": str(e), "symbols": {}}


# ── Rejected Signals Archive ──────────────────────────────────────────


@router.get("/rejected/archive")
async def get_rejected_archive(
    limit: int = Query(20, ge=1, le=100),
    reason: str | None = Query(None),
):
    """Rejected signals archive: counterfactual PnL + DOC comparison."""
    try:
        from src.database.rejected_signals import (
            fetch_doc_returns,
            get_cumulative_information_gain,
            get_rejected_signals,
        )

        entries = get_rejected_signals(limit=limit, reason_filter=reason)
        cum_ig = get_cumulative_information_gain()
        doc_pairs = fetch_doc_returns(window_days=30)

        # Format for UI
        formatted = []
        for e in entries:
            formatted.append(
                {
                    "id": e.get("id"),
                    "timestamp": e.get("timestamp"),
                    "ticker": e.get("ticker"),
                    "signal_type": e.get("signal_type"),
                    "rejection_reason": e.get("rejection_reason"),
                    "regime_score": round(e.get("regime_score", 0), 3),
                    "adx_value": round(e.get("adx_value", 0), 1),
                    "prior_belief": round(e.get("prior_belief", 0), 3),
                    "posterior_belief": round(e.get("posterior_belief", 0), 3),
                    "evaluation_horizon": e.get("evaluation_horizon"),
                    "status": e.get("status"),
                    "simulated_exit_5d": e.get("simulated_exit_5d"),
                    "simulated_exit_10d": e.get("simulated_exit_10d"),
                    "simulated_exit_20d": e.get("simulated_exit_20d"),
                    "alternative_return_5d": e.get("alternative_return_5d"),
                    "alternative_return_10d": e.get("alternative_return_10d"),
                    "alternative_return_20d": e.get("alternative_return_20d"),
                    "accepted_alternative": e.get("accepted_alternative"),
                    "information_gain": round(e.get("information_gain", 0), 4),
                    "surprise": round(e.get("surprise", 0), 4),
                    "cumulative_ig": round(e.get("cumulative_ig", 0), 4),
                }
            )

        # DOC summary
        doc_summary = {"n_pairs": len(doc_pairs)}
        if doc_pairs:
            diffs = [p["alternative_return"] - p["rejected_return"] for p in doc_pairs]
            doc_summary["doc_index"] = round(sum(diffs) / len(diffs), 4)
            doc_summary["wins"] = sum(1 for d in diffs if d > 0)
            doc_summary["losses"] = sum(1 for d in diffs if d < 0)

        return {
            "entries": formatted,
            "count": len(formatted),
            "cumulative_information_gain": round(cum_ig, 4),
            "doc_summary": doc_summary,
        }
    except (ImportError, TypeError, ValueError, KeyError, AttributeError, sqlite3.Error) as e:
        logger.warning(f"[API] rejected/archive error: {e}")
        return {"entries": [], "error": str(e)}
