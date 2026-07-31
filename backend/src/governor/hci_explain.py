"""hci_explain.py — HCI Causal Trace Explainer (First-Principles CLI).

WHY: The flat-table CLI output (print_report, Tầng 2) collapses the entire
multi-layer macro transmission chain (World FedState → VN macro → sector →
company) into a single row of numbers. An operator cannot see WHY an HCI
score dropped. This module renders the Causal DAG Trace (Cây vết truyền dẫn)
behind a company's HCI score:

    [FED_TARGET_RATE=5.50] (Fact)
       │──(lag 1-30d)──> [DXY_USD=100.05]
       │──(lag 1-10d)──> [US10Y_YIELD=4.66%]
       │
       ▼
    [INTEREST_RATE] → [NIM] → [VCB HCI: 0.41 ↓ (DISCOUNT -18%)] (Conf: 0.63)

Three First-Principles display contracts:
  1. Causal Trace Output — DAG path with per-hop lag + confidence.
  2. Facts vs Surprise — measured values (Fed rate, DXY) vs latent states
     (FED_UNCERTAINTY from hawkish dissent, macro/transmission/sector phase).
  3. Entropy/Confidence — HCI carries chain confidence = 1 - normalized entropy.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

# ── Sentinel v2.2 (AGENTS.md Anchor) ─────────────────────────────────
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
    for _p in [root_path / "backend" / "src", root_path / "backend", root_path]:
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = BACKEND_DIR / "data"

MACRO_DIR = DATA_DIR / "macro"

# ── Archetype → company metric node in the CausalGraph registry ──────
# WHY: each archetype has a primary macro→company chain in causal_edge.py.
#   The final hop converts a macro/transmission state into a company-level
#   financial metric (NIM for banks, PRESALES for RE, etc).
ARCHETYPE_TARGET_NODE = {
    "FRANCHISE_BANK": "NIM",
    "ASSET_BANK": "NIM",
    "COMPOUNDER": "IT_REVENUE",
    "CYCLICAL_HEAVY": "GROSS_MARGIN",
    "REAL_ESTATE_DEVELOPER": "PRESALES",
    "RETAIL_PLATFORM": "SAME_STORE_SALES",
    "REGULATED_UTILITY": "BRENT_LINK",
    "EXPORT_MANUFACTURER": "FX_MARGIN_IMPACT",
    "STEADY_EARNER": None,
    "UNKNOWN": None,
}

# Archetype → macro source that feeds the target (the real registered chain).
ARCHETYPE_SOURCE_NODE = {
    "FRANCHISE_BANK": "INTEREST_RATE",
    "ASSET_BANK": "INTEREST_RATE",
    "COMPOUNDER": "AI_CAPEX",
    "CYCLICAL_HEAVY": "STEEL_PRICE",
    "REAL_ESTATE_DEVELOPER": "INTEREST_RATE",
    "RETAIL_PLATFORM": "CONSUMER_SPENDING",
    "REGULATED_UTILITY": "OIL_PRICE",
    "EXPORT_MANUFACTURER": "USD_VND",
}

# Max entropy for normalization: the macro classifier uses 5 narrative
# classes (NORMAL, LIQUIDITY_CRUNCH, INFLATION, AI_BOOM, ENERGY_SHOCK).
# log2(5) ≈ 2.322. Chain confidence = 1 - entropy / H_max.
MAX_ENTROPY = 2.322


class HCIExplainEngine:
    """Build the causal DAG trace + confidence for a single symbol."""

    def __init__(self):
        self._graph = None
        self._world = None
        self._perception = None

    # ── Lazy dependencies ────────────────────────────────────────────

    def _get_graph(self):
        if self._graph is None:
            from calibration.causal_edge import CausalGraph
            self._graph = CausalGraph()
        return self._graph

    def _get_world(self) -> dict:
        if self._world is None:
            try:
                from src.sensors.world_sensor import WorldSensor
                # status() reads the persisted fed_policy_cache.json without
                # hitting the network — deterministic for CLI explanation.
                self._world = WorldSensor(use_cache=True).status()
            except Exception:
                self._world = {}
        return self._world

    def _get_perception(self):
        if self._perception is None:
            from src.governor.company_state import PerceptionLoader
            self._perception = PerceptionLoader()
        return self._perception

    # ── World layer (P0.5) ────────────────────────────────────────────

    def _world_nodes(self) -> List[Dict]:
        """Map WorldSensor fields → causal nodes tagged Fact vs Latent.

        FACTS are measured values (fed funds, DXY, US10Y, QT balance).
        SURPRISE/LATENT are derived dispersion states (hawkish dissent →
        FED_UNCERTAINTY). This satisfies the Facts-vs-Surprise contract.
        """
        w = self._get_world()
        nodes = [
            {
                "node": "FED_TARGET_RATE",
                "kind": "fact",
                "label": "Fed funds",
                "value": w.get("fed_target_rate"),
                "display": f"{w.get('fed_target_rate'):.2f}%" if w.get("fed_target_rate") else "N/A",
            },
            {
                "node": "DXY_USD",
                "kind": "fact",
                "label": "USD Index",
                "value": w.get("usd_index"),
                "display": f"{w.get('usd_index'):.2f}" if w.get("usd_index") else "N/A",
            },
            {
                "node": "US10Y_YIELD",
                "kind": "fact",
                "label": "US 10Y yield",
                "value": w.get("us10y_yield"),
                "display": f"{w.get('us10y_yield'):.2f}%" if w.get("us10y_yield") else "N/A",
            },
            {
                "node": "QT_IMPULSE",
                "kind": "fact",
                "label": "Fed balance sheet",
                "value": w.get("qt_balance_tr"),
                "display": f"{w.get('qt_balance_tr'):.2f}T" if w.get("qt_balance_tr") else "N/A",
            },
            {
                "node": "FOMC_DISSENT",
                "kind": "fact",
                "label": "FOMC dissents",
                "value": w.get("fomc_dissent"),
                "display": f"{int(w.get('fomc_dissent') or 0)}",
            },
            {
                "node": "FED_UNCERTAINTY",
                "kind": "surprise",
                "label": "Policy uncertainty (dissent)",
                "value": w.get("fed_uncertainty"),
                "display": f"{w.get('fed_uncertainty'):.2f}" if w.get("fed_uncertainty") is not None else "0.00",
            },
        ]
        return nodes

    # ── Symbol → sector lookup ─────────────────────────────────────────

    def _business_archetype(self, symbol: str) -> str:
        """Business archetype from Giai đoạn 1 engine (has causal edges).

        WHY: CausalGraph archetype edges (FRANCHISE_BANK, COMPOUNDER...)
        are keyed on the BUSINESS archetype, not the P2 health archetype
        (which may be STEADY_EARNER — a latent health state with no edges).
        """
        try:
            from src.business.archetype import ArchetypeEngine
            arch = ArchetypeEngine().classify(symbol)
            return arch.archetype if arch else "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _symbol_sector(self, symbol: str) -> Optional[str]:
        """Map symbol → ICB sector name via the same mapping SectorStateEngine uses."""
        try:
            from src.core.macro.sector_state_engine import SectorStateEngine
            mapping = SectorStateEngine._load_icb_mapping()
            for sector, syms in mapping.items():
                if symbol in syms:
                    return sector
        except Exception:
            pass
        return None

    def _sector_phase(self, sector: str) -> Optional[Dict]:
        """Look up the sector's rotation phase from the persisted P1 report."""
        try:
            path = MACRO_DIR / "sector_rotation_latest.json"
            if not path.exists():
                return None
            report = json.loads(path.read_text(encoding="utf-8"))
            for s in report.get("sectors", []):
                if s.get("sector") == sector:
                    return {
                        "name": sector,
                        "phase": s.get("phase", "NEUTRAL"),
                        "score": s.get("score", 0),
                    }
        except Exception:
            pass
        return None

    # ── Causal path ───────────────────────────────────────────────────

    def _trace_chain(self, symbol: str, archetype: str) -> Dict:
        """Build the DAG path: World source → VN macro → company metric.

        Uses CausalGraph.trace_path (highest-confidence path) for the
        World→macro leg and the archetype macro→company leg. Falls back
        gracefully when the graph has no path.
        """
        cg = self._get_graph()
        target_node = ARCHETYPE_TARGET_NODE.get(archetype)

        # Leg 1: World → VN macro (universal edges), try each world source.
        world_nodes = self._world_nodes()
        world_to_vn = None
        for wn in world_nodes:
            # FED_TARGET_RATE → INTEREST_RATE via US10Y_YIELD is the
            # canonical rates channel; DXY_USD → USD_VND is the FX channel.
            vn_target = "INTEREST_RATE" if wn["node"] == "FED_TARGET_RATE" else (
                "USD_VND" if wn["node"] == "DXY_USD" else "LIQUIDITY_TRAP"
            )
            try:
                path = cg.trace_path(wn["node"], vn_target, None)
            except Exception:
                path = None
            if path:
                # Store the first found (highest-confidence) world→VN leg.
                if world_to_vn is None:
                    world_to_vn = {
                        "source": wn["node"],
                        "target": vn_target,
                        "hops": path,
                    }
                if wn["node"] == "FED_TARGET_RATE":
                    break  # rates channel is the primary World→VN leg

        # Leg 2: macro → company metric (archetype edge).
        company_leg = None
        if target_node:
            # Prefer the archetype's own registered macro source (e.g.
            # AI_CAPEX→IT_BACKLOG→IT_REVENUE for COMPOUNDER). Fall back to
            # the world→VN macro target (INTEREST_RATE) when no specific
            # source exists for the archetype.
            for src in (ARCHETYPE_SOURCE_NODE.get(archetype), world_to_vn and world_to_vn["target"]):
                if not src:
                    continue
                try:
                    path = cg.trace_path(src, target_node, archetype)
                except Exception:
                    path = None
                if path:
                    company_leg = {
                        "target": target_node,
                        "hops": path,
                    }
                    break

        return {
            "world_to_vn": world_to_vn,
            "company_leg": company_leg,
            "target_node": target_node,
        }

    # ── Confidence ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_entropy(entropy: float) -> float:
        """Map macro entropy → chain confidence [0,1]."""
        try:
            h = float(entropy or 0.0)
            return max(0.0, min(1.0, 1.0 - h / MAX_ENTROPY))
        except Exception:
            return 0.5

    # ── Attribution: Policy Rate vs Hawkish Dissent ───────────────────

    def _attribution(self) -> Dict:
        """Clarify whether HCI pressure comes from the rate LEVEL (fact)
        or from hawkish DISSENT (surprise/latent state)."""
        w = self._get_world()
        fed_rate = float(w.get("fed_target_rate") or 0.0)
        dissent = int(w.get("fomc_dissent") or 0)
        uncertainty = float(w.get("fed_uncertainty") or 0.0)

        policy_rate_active = fed_rate >= 5.0  # restrictive corridor
        dissent_active = dissent > 0 or uncertainty > 0.2

        if policy_rate_active and dissent_active:
            primary = "dissent" if uncertainty > 0.25 else "policy_rate"
            message = (
                "Áp lực kép: chính sách lãi suất duy trì cao (Fact) "
                "và bất đồng Fed gia tăng độ bất định (Surprise)."
            )
        elif policy_rate_active:
            primary = "policy_rate"
            message = "HCI bị kìm bởi MỨC LÃI SUẤT (Fact — policy rate còn cao)."
        elif dissent_active:
            primary = "dissent"
            message = "HCI bị kìm bởi BẤT ĐỒNG DIỀU HÀNH (Surprise — hawkish dissent)."
        else:
            primary = "none"
            message = "Chuỗi World không tạo áp lực rõ ràng lên HCI."

        return {
            "primary": primary,
            "policy_rate_active": policy_rate_active,
            "dissent_active": dissent_active,
            "fed_rate": fed_rate,
            "dissent": dissent,
            "uncertainty": uncertainty,
            "message": message,
        }

    # ── Main explain ──────────────────────────────────────────────────

    def explain(self, symbol: str) -> Dict:
        """Assemble the full HCI explanation for one symbol."""
        perception = self._get_perception()

        # P0 / P1 / P2 states (single source of truth — loaded once).
        macro = perception.load_macro_state()
        trans = perception.load_transmission()
        health = perception.load_health(symbol)
        health_arch = health.get("archetype", "UNKNOWN")

        # Business archetype drives the causal trace (has CausalGraph edges).
        business_arch = self._business_archetype(symbol)

        # P3 governor — HCI score + MoS + action.
        try:
            from src.governor.company_state import BayesianGovernor
            gov = BayesianGovernor()
            mandate = gov.assess(symbol)
            gov.close()
            hci = {
                "p_gain": mandate.p_gain,
                "action": mandate.action,
                "mos": mandate.margin_of_safety,
                "market_context": mandate.market_context_tag,
            }
        except Exception:
            hci = {"p_gain": 0.5, "action": "N/A", "mos": None, "market_context": "N/A"}

        # Sector leg.
        sector_name = self._symbol_sector(symbol)
        sector_state = self._sector_phase(sector_name) if sector_name else None

        # Causal chain (business archetype has registered causal edges).
        trace = self._trace_chain(symbol, business_arch)

        # Confidence: chain entropy from P0 + compounded edge confidence.
        macro_entropy = float(macro.get("entropy", 0.0))
        chain_conf = self._normalize_entropy(macro_entropy)

        # Per-hop edge confidence along the found path (product).
        hop_confidences = []
        for leg in (trace.get("world_to_vn"), trace.get("company_leg")):
            if leg:
                for hop in leg["hops"]:
                    hop_confidences.append(hop.get("compounded_confidence", 0.0))
        edge_chain_conf = (
            hop_confidences[-1] if hop_confidences else chain_conf
        )
        hci_confidence = round(0.7 * chain_conf + 0.3 * edge_chain_conf, 3)

        return {
            "symbol": symbol,
            "date": str(date.today()),
            "world": self._world_nodes(),
            "macro": macro,
            "transmission": trans,
            "sector": sector_state,
            "health": health,
            "archetype": business_arch,
            "health_archetype": health_arch,
            "trace": trace,
            "hci": hci,
            "entropy": {
                "macro_entropy": round(macro_entropy, 4),
                "max_entropy": MAX_ENTROPY,
                "chain_confidence": chain_conf,
                "edge_chain_confidence": round(edge_chain_conf, 3),
                "hci_confidence": hci_confidence,
            },
            "attribution": self._attribution(),
        }


# ════════════════════════════════════════════════════════════════════
# RENDERING — Causal DAG Trace (Cây vết truyền dẫn)
# ════════════════════════════════════════════════════════════════════


def _fmt_mos(mos: Optional[float]) -> str:
    if mos is None:
        return "MoS: N/A"
    zone = "DISCOUNT" if mos < 0 else "PREMIUM" if mos > 0 else "FAIR"
    return f"MoS: {mos:+.1f}% ({zone})"


def print_hci_explain(result: Dict, lang_mode: str = "full") -> None:
    """Render the HCI causal trace as an ASCII DAG."""
    sym = result["symbol"]
    hci = result["hci"]
    trace = result["trace"]
    ent = result["entropy"]
    attr = result["attribution"]
    macro = result["macro"]
    trans = result["transmission"]
    sector = result["sector"]
    health = result["health"]

    arrow = {
        "REDUCE": "↓",
        "AVOID": "↓↓",
        "VETO": "✖",
        "WAIT": "→",
        "HOLD": "•",
        "SCALE_IN": "↑",
        "OPEN": "↑↑",
    }.get(hci.get("action"), "→")

    print(f"\n  {'═'*100}")
    print(f"  🔎 HCI EXPLAIN — {sym} | {result['date']} | "
          f"Archetype: {result['archetype']}"
          + (f" (Health: {result['health_archetype']})" if result.get("health_archetype") else ""))
    print(f"  {'═'*100}")

    # ── Header: HCI score + confidence ────────────────────────────────
    print(f"\n  🎯 {sym} HCI: {hci.get('p_gain', 0.0):.2f} {arrow} "
          f"({_fmt_mos(hci.get('mos'))})")
    print(f"     {_('Action')}: {hci.get('action')} | "
          f"{_('Market context')}: {hci.get('market_context')}")
    print(f"     {_('Chain Confidence')}: {ent['hci_confidence']:.2f} "
          f"(1 - H={ent['macro_entropy']:.2f}/{ent['max_entropy']:.2f})")

    # ── Layer 1: World FedState (Facts + Surprise) ────────────────────
    print(f"\n  🌍 WORLD LAYER (P0.5 — FedState)")
    print(f"  {'─'*100}")
    for wn in result["world"]:
        tag = "FACT" if wn["kind"] == "fact" else "SURPRISE"
        label = wn["label"]
        print(f"    [{wn['node']}] ({label}) = {wn['display']:<12} [{tag}]")

    # ── Layer 2: Causal DAG trace ─────────────────────────────────────
    print(f"\n  🔀 CAUSAL DAG TRACE (Cây vết truyền dẫn)")
    print(f"  {'─'*100}")

    world_to_vn = trace.get("world_to_vn")
    company_leg = trace.get("company_leg")

    if world_to_vn and world_to_vn.get("hops"):
        prev_target = None
        for i, hop in enumerate(world_to_vn["hops"]):
            src = hop["source"]
            tgt = hop["target"]
            lag = f"{hop['lag_min']}-{hop['lag_max']}D"
            conf = f"conf {hop['confidence']:.2f}"
            branch = "│" if i < len(world_to_vn["hops"]) - 1 else "└"
            print(f"    [{src}]")
            print(f"      {branch}──({lag}, {conf})──> [{tgt}]")
            prev_target = tgt
    else:
        # Fallback: show world nodes feeding the macro state directly.
        ms = macro.get("state", "STABLE")
        print(f"    [World FedState]")
        print(f"      └──(no registered causal edge)──> [VN Macro: {ms}]")

    # Macro → company leg
    ms = macro.get("state", "STABLE")
    m_ent = float(macro.get("entropy", 0.0))
    print(f"    [VN MACRO: {ms}] (P={macro.get('posterior', 0):.2f}, H={m_ent:.2f})")

    tp = trans.get("phase", "N/A")
    print(f"      └──(transmission)──> [{_('Transmission')}: {tp} "
          f"(Liquidity {trans.get('liquidity', 0):.0f}, "
          f"Credit {trans.get('credit', 0):.0f})]")

    if sector:
        print(f"        └──(sector)──> [{_('Sector')}: {sector['name']} "
              f"({sector['phase']}, score {sector['score']:.1f})]")
    else:
        print(f"        └──(sector)──> [{_('Sector')}: N/A]")

    if company_leg and company_leg.get("hops"):
        for hop in company_leg["hops"]:
            lag = f"{hop['lag_min']}-{hop['lag_max']}D"
            conf = f"conf {hop['confidence']:.2f}"
            print(f"          └──({lag}, {conf})──> [{_('Company metric')}: {hop['target']}]")
    elif trace.get("target_node"):
        print(f"          └──> [{_('Company metric')}: {trace['target_node']}]")
    else:
        print(f"          └──> [{_('Company metric')}: N/A] (archetype không có causal edge)")

    print(f"                    └──> [🎯 {sym} HCI: {hci.get('p_gain', 0.0):.2f} {arrow}]")

    # ── Layer 3: Facts vs Surprise ────────────────────────────────────
    print(f"\n  📊 FACTS vs SURPRISE (Dữ liệu thực vs Độ lệch kỳ vọng)")
    print(f"  {'─'*100}")
    print(f"    {attr['message']}")
    if attr["primary"] == "policy_rate":
        print(f"    → Nguồn chính: POLICY RATE (Fact) — mức lãi suất "
              f"{attr['fed_rate']:.2f}% kìm HCI.")
    elif attr["primary"] == "dissent":
        print(f"    → Nguồn chính: HAWKISH DISSENT (Surprise) — "
              f"{attr['dissent']} phiếu bất đồng, uncertainty={attr['uncertainty']:.2f}.")
    else:
        print(f"    → Không có nguồn World vượt ngưỡng áp lực.")

    # ── Layer 4: Entropy / Confidence ─────────────────────────────────
    print(f"\n  🧮 ENTROPY / CONFIDENCE")
    print(f"  {'─'*100}")
    print(f"    Macro entropy H = {ent['macro_entropy']:.2f} (max {ent['max_entropy']:.2f})")
    print(f"    Chain confidence = 1 - H/H_max = {ent['chain_confidence']:.3f}")
    print(f"    Edge confidence  = {ent['edge_chain_confidence']:.3f}")
    print(f"    HCI confidence   = 0.7·chain + 0.3·edge = {ent['hci_confidence']:.3f}")

    # ── Layer 5: Company health evidence ──────────────────────────────
    print(f"\n  🏥 COMPANY EVIDENCE (P2)")
    print(f"  {'─'*100}")
    print(f"    Archetype: {health.get('archetype', 'N/A')} "
          f"(P={health.get('confidence', 0):.2f}) | "
          f"Periods: {health.get('periods', 0)}")
    print(f"    Organs: " + ", ".join(
        f"{k}={v:.2f}" for k, v in zip(
            ["Profit", "Cash", "BS", "Eff", "Moat"], health.get("vector", [])
        )
    ))

    print(f"\n  {'═'*100}\n")


def _localize(label: str) -> str:
    """Localize label (best-effort, VI-first for Vietnamese readers)."""
    try:
        from src.core.canonical_output_adapter import localize_label
        vi = localize_label(label, "full")
        return f"{vi} ({label})" if vi != label else label
    except Exception:
        return label


def _(
    label: str,
) -> str:
    return _localize(label)


def print_sector_hci_comparison(results: List[Dict], sector_name: str) -> None:
    """Render a side-by-side comparison of HCI traces within the same sector.

    WHY: Operators need to see how the same macro/transmission/sector context
    produces different HCI scores across companies sharing the same archetype.
    This reveals company-specific alpha vs systemic macro drag.
    """
    if not results:
        print(f"\n  No results for sector '{sector_name}'.")
        return

    # Deduplicate sector phase from results (all share same sector leg).
    sector_phase = next(
        (r["sector"]["phase"] for r in results if r.get("sector")), "N/A"
    )
    sector_score = next(
        (r["sector"]["score"] for r in results if r.get("sector")), 0.0
    )

    print(f"\n  {'═'*100}")
    print(f"  🏭 SECTOR HCI COMPARISON — {sector_name}")
    print(f"     Phase: {sector_phase} | Score: {sector_score:.1f} | "
          f"Date: {results[0]['date']}")
    print(f"  {'═'*100}")

    # ── Shared macro context (one row) ────────────────────────────────
    r0 = results[0]
    print(f"\n  🌍 SHARED MACRO CONTEXT")
    print(f"  {'─'*100}")
    ms = r0["macro"].get("state", "N/A")
    tp = r0["transmission"].get("phase", "N/A")
    attr = r0["attribution"]
    print(f"    Macro State: {ms} | Transmission: {tp} | "
          f"Attribution: {attr['primary']} ({attr['message'][:60]}...)")
    fed_rate = next(
        (w["value"] for w in r0["world"] if w["node"] == "FED_TARGET_RATE"), None
    )
    print(f"    Fed Rate: {fed_rate:.2f}% | "
          f"Entropy: {r0['entropy']['macro_entropy']:.2f} | "
          f"Confidence: {r0['entropy']['hci_confidence']:.3f}")

    # ── Per-symbol comparison table ────────────────────────────────────
    print(f"\n  📊 PER-SYMBOL BREAKDOWN")
    print(f"  {'─'*100}")
    header = f"    {'Symbol':<8} {'HCI':>5} {'Action':<10} {'Archetype':<20} {'MoS':>8} {'Conf':>6} {'Attribution':<12}"
    print(header)
    print(f"    {'─'*92}")

    for r in sorted(results, key=lambda x: x["hci"].get("p_gain", 0), reverse=True):
        sym = r["symbol"]
        hci_val = r["hci"].get("p_gain", 0.0)
        action = r["hci"].get("action", "N/A")
        arch = r["archetype"]
        mos = r["hci"].get("mos")
        mos_str = f"{mos:+.1f}%" if mos is not None else "N/A"
        conf = r["entropy"]["hci_confidence"]
        attrib = r["attribution"]["primary"]

        arrow = {"REDUCE": "↓", "AVOID": "↓↓", "VETO": "✖",
                 "WAIT": "→", "HOLD": "•", "SCALE_IN": "↑", "OPEN": "↑↑"
                 }.get(action, "→")

        print(f"    {sym:<8} {hci_val:.2f}{arrow:>1} {action:<10} {arch:<20} "
              f"{mos_str:>8} {conf:>6.3f} {attrib:<12}")

    # ── Causal path divergence (which hops differ) ─────────────────────
    print(f"\n  🔀 CAUSAL PATH DIVERGENCE")
    print(f"  {'─'*100}")
    # Group by archetype to show path similarities/differences.
    by_arch: Dict[str, List[Dict]] = {}
    for r in results:
        a = r["archetype"]
        by_arch.setdefault(a, []).append(r)

    for arch, arch_results in by_arch.items():
        targets = set()
        for r in arch_results:
            tn = r["trace"].get("target_node")
            if tn:
                targets.add(tn)
        target_str = ", ".join(targets) if targets else "N/A"
        syms = [r["symbol"] for r in arch_results]
        print(f"    [{arch}] ({', '.join(syms)})")
        print(f"      Target metric(s): {target_str}")
        # Show company leg hops for first symbol as representative.
        first = arch_results[0]
        comp_leg = first["trace"].get("company_leg")
        if comp_leg and comp_leg.get("hops"):
            for hop in comp_leg["hops"]:
                lag = f"{hop['lag_min']}-{hop['lag_max']}D"
                conf = f"conf {hop['confidence']:.2f}"
                print(f"        └──({lag}, {conf})──> [{hop['target']}]")
        else:
            print(f"        └──(no registered company edge)")

    # ── Key insight ────────────────────────────────────────────────────
    print(f"\n  💡 INSIGHT")
    print(f"  {'─'*100}")
    hci_vals = [r["hci"].get("p_gain", 0) for r in results]
    if hci_vals:
        spread = max(hci_vals) - min(hci_vals)
        best = max(results, key=lambda x: x["hci"].get("p_gain", 0))
        worst = min(results, key=lambda x: x["hci"].get("p_gain", 0))
        print(f"    HCI spread within sector: {spread:.2f} "
              f"({worst['symbol']}={min(hci_vals):.2f} → {best['symbol']}={max(hci_vals):.2f})")
        print(f"    All {len(results)} symbols share macro drag: {attr['primary']} "
              f"(Fed {fed_rate:.2f}%)")
        print(f"    Differentiation comes from company-specific health/archetype, "
              f"not sector leg.")

    print(f"\n  {'═'*100}\n")


def resolve_sector_symbols(sector_query: str) -> List[str]:
    """Resolve a sector name (or partial match) to list of symbols.

    Supports exact ICB name or fuzzy substring match. Returns sorted symbol list.
    """
    try:
        from src.core.macro.sector_state_engine import SectorStateEngine
        mapping = SectorStateEngine._load_icb_mapping()
    except Exception:
        return []

    # Exact match first.
    if sector_query in mapping:
        return sorted(mapping[sector_query])

    # Fuzzy: case-insensitive substring match.
    query_lower = sector_query.lower()
    for sector_name, symbols in mapping.items():
        if query_lower in sector_name.lower():
            return sorted(symbols)

    return []


def list_sectors() -> List[str]:
    """Return all available ICB sector names for display."""
    try:
        from src.core.macro.sector_state_engine import SectorStateEngine
        mapping = SectorStateEngine._load_icb_mapping()
        return sorted(mapping.keys())
    except Exception:
        return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HCI Causal Trace Explainer")
    parser.add_argument("--symbol", type=str, required=True, help="Mã cổ phiếu")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    engine = HCIExplainEngine()
    result = engine.explain(args.symbol)
    print_hci_explain(result, "full")


if __name__ == "__main__":
    main()
