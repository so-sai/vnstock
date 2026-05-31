"""
Gold Provenance Registration — Đăng ký tín hiệu vàng vào Provenance Graph
Cho phép Sentinel tracing root cause: SJC price → GOLD_XAU → gold_regime → decision
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent
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

from core.signal_provenance.models import (
    SignalProvenanceNode, SignalValue, SignalQuality, SignalContext, EpistemicState
)
from core.signal_provenance.registry import ProvenanceRegistry

logger = logging.getLogger(__name__)

try:
    from core.signal_provenance import ProvenanceRegistry as RegistryClass
except ImportError:
    ProvenanceRegistry = None


def register_gold_nodes(registry: ProvenanceRegistry, xau_price: float = 0.0, timestamp: int = 0) -> bool:
    """
    Đăng ký gold signal nodes vào Provenance Registry.
    Gồm 5 nodes:
      - macro.gold.xau (RAW) — giá vàng thế giới XAUUSD
      - macro.fx.usdvnd (RAW) — tỷ giá USD/VND
      - macro.gold.regime (DERIVED) — gold regime từ engine
      - macro.gold.sjc (RAW) — giá vàng SJC trong nước
      - macro.gold.premium (DERIVED) — domestic premium regime

    Lineage:
      macro.gold.xau ──→ macro.gold.regime
      macro.gold.xau ──┐
      macro.fx.usdvnd ─┼──→ macro.gold.premium
      macro.gold.sjc ──┘
    """
    now = timestamp if timestamp > 0 else int(datetime.now().timestamp())
    try:
        raw_node = SignalProvenanceNode(
            node_id="macro.gold.xau",
            signal_id="macro.gold.xau",
            engine="gold_macro_engine",
            timestamp=now,
            type="RAW",
            value=SignalValue(
                metric="xau_usd_price",
                direction="stable",
                magnitude=0.1,
                raw=xau_price,
            ),
            quality=SignalQuality(
                latency_ms=1200,
                freshness=0.98,
                noise=0.08,
                completeness=0.95,
                stability=0.92,
            ),
            context=SignalContext(
                market_regise="ranging",
                liquidity_state="stable",
                volatility_regime="low",
            ),
            truth_status=EpistemicState.PROBABILISTIC,
            confidence_hint=0.85,
            ttl_seconds=86400,
        )
        derived_node = SignalProvenanceNode(
            node_id="macro.gold.regime",
            signal_id="macro.gold.regime",
            engine="gold_macro_engine",
            timestamp=now + 1,
            type="DERIVED",
            transformations=[{"name": "gold_regime_classification", "params": {"lookback": 20}}],
            value=SignalValue(
                metric="gold_regime",
                direction="stable",
                magnitude=0.3,
                raw=xau_price,
            ),
            quality=SignalQuality(
                latency_ms=1500,
                freshness=0.90,
                noise=0.12,
                completeness=0.90,
                stability=0.85,
            ),
            context=SignalContext(
                market_regise="ranging",
                liquidity_state="stable",
                volatility_regime="low",
            ),
            truth_status=EpistemicState.PROBABILISTIC,
            confidence_hint=0.75,
            ttl_seconds=86400,
        )
        registry.register_node(raw_node)
        registry.register_node(derived_node)
        registry.add_edge(
            from_id="macro.gold.xau",
            to_id="macro.gold.regime",
            weight=0.85,
            edge_type="DERIVATION",
            attenuation={"noise_gain": 0.08, "information_loss": 0.05},
        )
        # Premium nodes
        sjc_node = SignalProvenanceNode(
            node_id="macro.gold.sjc",
            signal_id="macro.gold.sjc",
            engine="gold_macro_engine",
            timestamp=now,
            type="RAW",
            value=SignalValue(
                metric="sjc_vnd_price",
                direction="stable",
                magnitude=0.1,
                raw=xau_price,
            ),
            quality=SignalQuality(
                latency_ms=5000,
                freshness=0.85,
                noise=0.15,
                completeness=0.90,
                stability=0.80,
            ),
            context=SignalContext(
                market_regise="ranging",
                liquidity_state="stable",
                volatility_regime="low",
            ),
            truth_status=EpistemicState.PROBABILISTIC,
            confidence_hint=0.80,
            ttl_seconds=3600,
        )
        premium_node = SignalProvenanceNode(
            node_id="macro.gold.premium",
            signal_id="macro.gold.premium",
            engine="gold_spread_engine",
            timestamp=now + 1,
            type="DERIVED",
            transformations=[{"name": "domestic_premium_calculation", "params": {"sources": ["sjc", "xauusd", "usd_vnd"]}}],
            value=SignalValue(
                metric="domestic_premium",
                direction="stable",
                magnitude=0.3,
                raw=xau_price,
            ),
            quality=SignalQuality(
                latency_ms=6000,
                freshness=0.80,
                noise=0.18,
                completeness=0.85,
                stability=0.75,
            ),
            context=SignalContext(
                market_regise="ranging",
                liquidity_state="stable",
                volatility_regime="low",
            ),
            truth_status=EpistemicState.PROBABILISTIC,
            confidence_hint=0.70,
            ttl_seconds=3600,
        )
        registry.register_node(sjc_node)
        # USD/VND node
        usdvnd_node = SignalProvenanceNode(
            node_id="macro.fx.usdvnd",
            signal_id="macro.fx.usdvnd",
            engine="macro_service",
            timestamp=now,
            type="RAW",
            value=SignalValue(
                metric="usd_vnd_rate",
                direction="stable",
                magnitude=0.05,
                raw=25400.0,
            ),
            quality=SignalQuality(
                latency_ms=60000,
                freshness=0.90,
                noise=0.05,
                completeness=0.95,
                stability=0.95,
            ),
            context=SignalContext(
                market_regise="ranging",
                liquidity_state="stable",
                volatility_regime="low",
            ),
            truth_status=EpistemicState.PROBABILISTIC,
            confidence_hint=0.90,
            ttl_seconds=86400,
        )
        registry.register_node(usdvnd_node)
        registry.register_node(premium_node)
        registry.add_edge(
            from_id="macro.gold.xau",
            to_id="macro.gold.premium",
            weight=0.70,
            edge_type="DERIVATION",
            attenuation={"noise_gain": 0.12, "information_loss": 0.10},
        )
        registry.add_edge(
            from_id="macro.fx.usdvnd",
            to_id="macro.gold.premium",
            weight=0.60,
            edge_type="DERIVATION",
            attenuation={"noise_gain": 0.05, "information_loss": 0.03},
        )
        registry.add_edge(
            from_id="macro.gold.sjc",
            to_id="macro.gold.premium",
            weight=0.80,
            edge_type="DERIVATION",
            attenuation={"noise_gain": 0.10, "information_loss": 0.08},
        )
        logger.info("Gold provenance nodes registered: macro.gold.xau→regime | macro.gold.xau+macro.fx.usdvnd+macro.gold.sjc→premium")
        return True
    except ValueError as e:
        if "already registered" in str(e):
            logger.debug("Gold nodes already registered, skipping.")
            return True
        logger.error(f"Gold provenance registration failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Gold provenance error: {e}")
        return False
