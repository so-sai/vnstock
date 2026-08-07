import sys
from pathlib import Path

from fastapi import APIRouter


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
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import logging

from core.flow.liquidity_concentration_engine import get_lci_dashboard
from src.engine.flow_decay_engine import (
    get_decayed_flow_summary,
    get_decayed_foreign_summary,
    get_decayed_liquidity_health,
    get_decayed_rotation_beta,
    synthesize_decayed_banner,
)
from src.engine.liquidity_wave import get_market_liquidity_health, scan_liquidity_waves
from src.engine.money_flow_engine import MoneyFlowEngine
from src.engine.sector_rotation_graph import get_sector_rotation_map

logger = logging.getLogger(__name__)

from src.core.canonical_output_adapter import localize_output

router = APIRouter(tags=["Phase 12B - Asia Flow Layer"])


@router.get("/liquidity")
async def get_liquidity_flow():
    """Channel 1: Liquidity Wave — volume acceleration, turnover shock, retail chase."""
    try:
        health = get_market_liquidity_health()
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Liquidity health failed: {e}")
        health = {}

    try:
        waves = scan_liquidity_waves(top_n=20)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Liquidity wave scan failed: {e}")
        waves = []

    return localize_output(
        {
            "liquidity_phase": health.get("liquidity_phase", "NEUTRAL"),
            "volume_trend_5d": health.get("volume_trend_5d", 0),
            "value_trend_5d": health.get("value_trend_5d", 0),
            "top_concentration_pct": health.get("top_concentration_pct", 0),
            "waves": [
                {
                    "symbol": w.get("symbol"),
                    "sector": w.get("sector", "Khác"),
                    "vol_ratio": round(w.get("vol_ratio", 0), 2),
                    "vol_accel_20d": round(w.get("vol_accel_20d", 0), 3),
                    "turnover_shock": round(w.get("turnover_shock", 0), 2),
                    "retail_chase_score": round(w.get("retail_chase_score", 0), 2),
                    "wave_strength": w.get("wave_strength", "NORMAL"),
                    "consecutive_high_vol_days": w.get("consecutive_high_vol_days", 0),
                }
                for w in waves[:20]
            ],
        }
    )


@router.get("/sector")
async def get_sector_flow():
    """Channel 2: Sector Rotation — inter-sector momentum, leader/follower graph."""
    try:
        rotation = get_sector_rotation_map()
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Sector rotation failed: {e}")
        rotation = {}

    sectors_raw = rotation.get("sectors", rotation.get("rotation_map", []))
    sectors = []
    for s in sectors_raw:
        score = s.get("flow_score", s.get("momentum_score", s.get("score", 50)))
        sectors.append(
            {
                "sector": s.get("sector", s.get("name", "Unknown")),
                "flow_score": score,
                "momentum": s.get("momentum", 0),
                "return_20d": s.get("return_20d", 0),
                "volatility_20d": s.get("volatility_20d", 0),
                "phase": _classify_phase(score),
            }
        )
    sectors.sort(key=lambda x: x["flow_score"], reverse=True)

    chains = _build_chains_from_waves()
    flow_alignment = rotation.get("flow_alignment_pct", rotation.get("alignment", 0))

    return localize_output(
        {
            "rotation_regime": rotation.get("rotation_regime", "UNKNOWN"),
            "flow_alignment_pct": flow_alignment,
            "sectors": sectors,
            "leader_follower_chains": chains,
        }
    )


@router.get("/foreign")
async def get_foreign_flow():
    """Channel 3: Foreign Flow — net accumulation, pressure by sector."""
    try:
        engine = MoneyFlowEngine()
        top_symbols = ["VCB", "HPG", "BSR", "GMD", "STB", "MBB", "TCB", "VNM", "FPT", "VIC"]
        accumulations = {}
        total_net = 0.0
        for sym in top_symbols:
            try:
                acc = engine.get_accumulation(sym, 10)
                accumulations[sym] = round(acc, 2)
                total_net += acc
            except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
                accumulations[sym] = 0.0
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Foreign flow engine failed: {e}")
        accumulations = {}
        total_net = 0.0

    top_accumulated = sorted(accumulations.items(), key=lambda x: x[1], reverse=True)

    return localize_output(
        {
            "total_net_10d_bn_vnd": round(total_net, 2),
            "market_pressure": "ACCUMULATING" if total_net > 0 else "DISTRIBUTING",
            "top_accumulated": [{"symbol": s, "net_10d_bn_vnd": v} for s, v in top_accumulated[:5]],
            "top_distributed": [{"symbol": s, "net_10d_bn_vnd": v} for s, v in reversed(top_accumulated[-5:])],
        }
    )


@router.get("/decayed-summary")
async def get_decayed_flow():
    """Phase 12C — Full decay-augmented flow summary (3 channels + banner with uncertainty)."""
    try:
        summary = get_decayed_flow_summary()
        return localize_output(summary)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Decayed flow summary failed: {e}")
        return localize_output({"status": "ERROR", "detail": str(e)})


@router.get("/decayed-liquidity")
async def get_decayed_liquidity():
    """Phase 12C — Liquidity wave with exponential decay weighting + persistence metrics."""
    try:
        health = get_decayed_liquidity_health()
        return localize_output(health)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Decayed liquidity failed: {e}")
        return localize_output({"status": "ERROR", "detail": str(e)})


@router.get("/decayed-sector")
async def get_decayed_sector():
    """Phase 12C — Sector rotation with decay-weighted momentum + signal quality."""
    try:
        beta = get_decayed_rotation_beta()
        return localize_output(beta)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Decayed sector failed: {e}")
        return localize_output({"status": "ERROR", "detail": str(e)})


@router.get("/decayed-foreign")
async def get_decayed_foreign():
    """Phase 12C — Foreign flow with decay-weighted accumulation (EWMA vs flat sum)."""
    try:
        summary = get_decayed_foreign_summary()
        return localize_output(summary)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Decayed foreign failed: {e}")
        return localize_output({"status": "ERROR", "detail": str(e)})


@router.get("/banner")
async def get_flow_banner():
    """Predictive macro flow banner — synthesized from all 3 channels without merging raw data."""
    try:
        health = get_market_liquidity_health()
    except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        health = {}

    try:
        rotation = get_sector_rotation_map()
    except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        rotation = {}

    try:
        waves = scan_liquidity_waves(top_n=10)
    except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        waves = []

    liquidity_phase = health.get("liquidity_phase", "NEUTRAL")
    rotation_regime = rotation.get("rotation_regime", "UNKNOWN")
    sector_scores = {}

    for w in waves:
        sector = w.get("sector", "Khác")
        score = w.get("retail_chase_score", 0)
        if sector not in sector_scores or score > sector_scores[sector]["score"]:
            sector_scores[sector] = {"score": score, "symbol": w.get("symbol")}

    sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1]["score"], reverse=True)

    if len(sorted_sectors) >= 2:
        top_sec, top_data = sorted_sectors[0]
        second_sec, second_data = sorted_sectors[1]
        banner = (
            f"TIỀN ĐANG CHUYỂN DỊCH: {top_sec.upper()} ({top_data['symbol']}) "
            f"→ {second_sec.upper()} ({second_data['symbol']}) | "
            f"THANH KHOẢN: {_phase_label_vn(liquidity_phase)} | "
            f"XOAY VÒNG NGÀNH: {_rotation_label_vn(rotation_regime)}"
        )
    elif len(sorted_sectors) == 1:
        top_sec, top_data = sorted_sectors[0]
        banner = (
            f"DÒNG TIỀN TẬP TRUNG: {top_sec.upper()} ({top_data['symbol']}) | THANH KHOẢN: {_phase_label_vn(liquidity_phase)}"
        )
    else:
        banner = "HỆ THỐNG ĐANG THU THẬP DỮ LIỆU DÒNG CHẢY — CHỜ TÍN HIỆU XÁC NHẬN"

    return localize_output(
        {
            "banner": banner,
            "liquidity_phase": liquidity_phase,
            "rotation_regime": rotation_regime,
        }
    )


@router.get("/banner/decayed")
async def get_flow_banner_decayed():
    """Phase 12C — Probabilistic flow banner with uncertainty, persistence tags, conflict flags."""
    try:
        banner_data = synthesize_decayed_banner()
        return localize_output(banner_data)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Decayed banner failed: {e}")
        return localize_output(
            {
                "banner": "LỖI HỆ THỐNG — KHÔNG THỂ TỔNG HỢP DỮ LIỆU DÒNG TIỀN",
                "liquidity_phase_decayed": "UNKNOWN",
                "rotation_regime_decayed": "UNKNOWN",
                "persistence_score": 0.0,
                "instability_score": 0.0,
                "signal_strength": 0.0,
                "conflict_flag": False,
                "confidence_band": "THẤP",
            }
        )


@router.get("/lci")
async def get_liquidity_concentration_index():
    """Phase 14 — LCI: Liquidity Concentration Index.
    Đo mức độ co cụm thanh khoản: Top-N ratio, entropy, return divergence.
    """
    try:
        lci = get_lci_dashboard()
        return localize_output(lci)
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"LCI failed: {e}")
        return localize_output(
            {
                "lci_score": 0.0,
                "market_breadth_quality": "LAN_TOA_THAT",
                "error": str(e),
            }
        )


def _build_chains_from_waves() -> dict:
    chains = {}
    try:
        waves = scan_liquidity_waves(top_n=30)
    except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        return chains
    sector_stocks = {}
    for w in waves:
        sym = w.get("symbol")
        sector = w.get("sector", "Khác")
        if not sym:
            continue
        if sector not in sector_stocks:
            sector_stocks[sector] = []
        sector_stocks[sector].append(
            {
                "symbol": sym,
                "score": w.get("retail_chase_score", w.get("vol_ratio", 0)),
            }
        )
    for sector, stocks in sector_stocks.items():
        if not stocks:
            continue
        stocks.sort(key=lambda x: x["score"], reverse=True)
        chains[sector] = {
            "leader": stocks[0]["symbol"],
            "followers": [s["symbol"] for s in stocks[1:4]],
        }
    return chains


def _classify_phase(score: float) -> str:
    if score >= 80:
        return "EXPANDING"
    if score >= 60:
        return "EARLY_ACCEL"
    if score >= 40:
        return "CONTRACTING"
    return "CRISIS"


def _phase_label_vn(phase: str) -> str:
    labels = {"EXPANDING": "MỞ RỘNG", "CONTRACTING": "CO HẸP", "NEUTRAL": "CÂN BẰNG"}
    return labels.get(phase, "KHÔNG XÁC ĐỊNH")


def _rotation_label_vn(regime: str) -> str:
    labels = {
        "HEALTHY_ROTATION": "XOAY VÒNG KHỎE",
        "BROAD_ROTATION": "XOAY VÒNG RỘNG",
        "DIVERGENT": "PHÂN HÓA",
        "NARROW_LEADERSHIP": "DẪN DẮT HẸP",
    }
    return labels.get(regime, "THEO DÕI")
