"""daily_cycle_orchestrator.py — Ưu tiên 3: Orchestration & Economic Daily Cycle.

Thay thế pipeline gộp thô sơ bằng ExecutionGraph có stale-window:

  1. morning  — SBV Update → Fed/World Sensors → MacroState → Transmission
                → Governor → Morning Brief  → SystemAuditor (System Health Report)
  2. close    — Market Data (EOD) → Breadth → Sector Flow → Governor
                → Daily Report → SystemAuditor
  3. earnings — Financial Crawl → Health Engine v2 → Valuation → Governor
                → SystemAuditor

Nguyên tắc:
  - Orchestrate bằng lời gọi programmatic (import trực tiếp), KHÔNG subprocess.
  - Mỗi node là một đơn vị cô lập: lỗi của node này KHÔNG kéo sập node khác.
  - stale_window (ngày): nếu dữ liệu còn mới (đã chạy OK trong window) → SKIP.
  - MỌI workflow kết thúc bằng SystemAuditor.run() → System Health Report kèm
    veto_reasons + confidence_score.
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

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

# ── Central constant (fix hardcode 3× trong daily_updater) ──────────
TARGET_SYMBOLS: List[str] = [
    "FPT", "ACB", "HDB", "MBB", "VCB",
    "HPG", "VHM", "DGC", "MWG", "GAS",
]


def _cycle_db_path() -> str:
    """Mặc định trỏ vào calibration.db — test monkeypatch để cô lập."""
    return str(PROJECT_ROOT / "backend" / "data" / "calibration.db")


# ════════════════════════════════════════════════════════════════════
# ExecutionGraph
# ════════════════════════════════════════════════════════════════════

class GraphCycleError(ValueError):
    """Phát hiện vòng lặp trong ExecutionGraph."""


class NodeSpec:
    """Khai báo một node trong workflow.

    fn(ctx) → dict: kết quả node. Graph tự gắn "status" (OK/SKIPPED/FAILED).
    stale_window=None → node LUÔN chạy (không bao giờ skip, dù data còn mới).
    """

    def __init__(self, id: str, deps: Optional[List[str]] = None,
                 stale_window: Optional[int] = None,
                 desc: str = "", fn: Optional[Callable[[Dict], Dict]] = None):
        self.id = id
        self.deps = deps or []
        self.stale_window = stale_window
        self.desc = desc
        self.fn = fn


class ExecutionGraph:
    """DAG các node; chạy theo thứ tự topo, cô lập lỗi, skip node còn mới."""

    def __init__(self):
        self.nodes: Dict[str, NodeSpec] = {}

    def add_node(self, nid: str, run: Callable[[Dict], Dict],
                 deps: Optional[List[str]] = None,
                 stale_window: Optional[int] = None,
                 desc: str = ""):
        self.nodes[nid] = NodeSpec(
            id=nid, deps=deps, stale_window=stale_window,
            desc=desc, fn=run)
        return self

    def order(self) -> List[str]:
        """Topo sort (DFS). Raise GraphCycleError nếu có vòng lặp."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {nid: WHITE for nid in self.nodes}
        order: List[str] = []

        def visit(nid: str):
            color[nid] = GRAY
            for dep in self.nodes[nid].deps:
                if dep not in self.nodes:
                    continue
                if color[dep] == GRAY:
                    raise GraphCycleError(
                        f"cycle detected: {dep} → {nid}")
                if color[dep] == WHITE:
                    visit(dep)
            color[nid] = BLACK
            order.append(nid)

        for nid in self.nodes:
            if color[nid] == WHITE:
                visit(nid)
        return order

    def run(self, ctx: Optional[Dict] = None, skip_fresh: bool = True,
            fresh_checker: Optional[Callable[[str], bool]] = None) -> Dict[str, Dict]:
        ctx = ctx if ctx is not None else {}
        results: Dict[str, Dict] = {}
        for nid in self.order():
            node = self.nodes[nid]
            if skip_fresh and node.stale_window is not None \
               and fresh_checker is not None and fresh_checker(nid):
                results[nid] = {"status": "SKIPPED"}
                continue
            try:
                out = node.fn(ctx) if node.fn else {}
                if out is None:
                    out = {}
                out = dict(out)
                out.setdefault("status", "OK")
            except Exception as e:  # noqa: BLE001 — cô lập lỗi từng node
                out = {"status": "FAILED", "error": f"{type(e).__name__}: {e}"}
            results[nid] = out
            ctx[nid] = out
        return results


# ════════════════════════════════════════════════════════════════════
# Run log (freshness tracking) — bảng daily_cycle_run_log
# ════════════════════════════════════════════════════════════════════

def record_run_log(scenario: str, node_id: str, status: str,
                   meta: Optional[Dict] = None, db_path: Optional[str] = None):
    db = db_path or _cycle_db_path()
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_cycle_run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario TEXT NOT NULL,
            node_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            meta TEXT
        )""")
        conn.execute(
            "INSERT INTO daily_cycle_run_log "
            "(scenario, node_id, status, started_at, meta) VALUES (?,?,?,?,?)",
            (scenario, node_id, status, datetime.now().isoformat(timespec="seconds"),
             json.dumps(meta, ensure_ascii=False) if meta else None))
        conn.commit()
    finally:
        conn.close()


def _is_fresh(scenario: str, node_id: str, window_days: int,
              db_path: Optional[str] = None) -> bool:
    """True nếu node đã chạy OK trong window_days gần nhất."""
    if not window_days:
        return False
    db = db_path or _cycle_db_path()
    if not Path(db).exists():
        return False
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT started_at FROM daily_cycle_run_log "
            "WHERE scenario=? AND node_id=? AND status='OK' "
            "ORDER BY id DESC LIMIT 1",
            (scenario, node_id)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    try:
        started = datetime.fromisoformat(row[0])
    except (ValueError, TypeError):
        return False
    return datetime.now() - started <= timedelta(days=window_days)


# ════════════════════════════════════════════════════════════════════
# Node runners (function-level imports để không nạp module nặng sớm)
# ════════════════════════════════════════════════════════════════════

def _run_sbv(ctx):
    from src.services.macro.interbank_seeder import _try_sbv
    return {"type": _try_sbv().get("type", "unknown")}


def _run_world_sensors(ctx):
    from src.sensors.world_sensor import WorldSensor
    pulse = WorldSensor().fetch()
    return {"pulse_keys": sorted(pulse.keys()) if isinstance(pulse, dict) else []}


def _run_macro_state(ctx):
    from src.core.macro.macro_state_classifier import MacroStateClassifier
    state = MacroStateClassifier().classify()
    return {"state": str(state)}


def _run_transmission(ctx):
    from src.core.macro.economic_transmission_engine import EconomicTransmissionEngine
    state = EconomicTransmissionEngine().compute()
    return {"transmission": str(state)}


def _run_sector(ctx):
    from src.core.macro.sector_state_engine import SectorStateEngine
    report = SectorStateEngine().analyze()
    n = 0
    if isinstance(report, dict):
        n = len(report.get("sectors") or report.get("rows") or report)
    return {"n_sectors": n}


def _run_breadth(ctx):
    from src.engine.breadth_engine import run_breadth_analysis
    report = run_breadth_analysis()
    return {"breadth": str(report)}


def _run_market_data(ctx):
    from src.engine.eod_runner import run_eod_pipeline
    return run_eod_pipeline()


def _run_financial_crawl(ctx):
    from src.audit.data_integrity_auditor import DataIntegrityAuditor
    auditor = DataIntegrityAuditor()
    results = auditor.audit_and_heal(TARGET_SYMBOLS, auto_backfill=True)
    return {"density_audited": len(results), "healed": [s for s, r in results.items() if r.healed]}


def _run_health_v2(ctx):
    from src.financial.company_health_v2 import CompanyHealthV2
    states = CompanyHealthV2().analyze_many(TARGET_SYMBOLS)
    return {"n_analyzed": sum(1 for v in states.values() if v is not None)}


def _run_valuation(ctx):
    from src.financial.valuation_engine import ValuationEngine
    engine = ValuationEngine()
    return engine.compare_valuations(TARGET_SYMBOLS)


def _run_vn20_builder(ctx):
    """Tier 4: Build PTCK_VN20 dynamic index via 4-tier pipeline."""
    from src.ptck_vn20_builder import build_vn20
    return build_vn20(dry_run=False)


def _run_governor(ctx):
    from src.governor.company_state import BayesianGovernor
    engine = BayesianGovernor()
    try:
        analysis = engine.analyze(TARGET_SYMBOLS)
        return {"analysis": str(analysis)}
    finally:
        engine.close()


# ════════════════════════════════════════════════════════════════════
# SCENARIOS — 3 kịch bản vận hành
# ════════════════════════════════════════════════════════════════════

def _make_audit_runner(days: int, persist: bool) -> Callable:
    def _run_system_audit(ctx):
        from calibration.system_auditor import SystemAuditor
        return SystemAuditor().run(days=days, persist=persist)
    return _run_system_audit


SCENARIOS: Dict[str, List[NodeSpec]] = {
    "morning": [
        NodeSpec("sbv_update", deps=[], stale_window=1,
                 desc="SBV Update — interbank rates", fn=_run_sbv),
        NodeSpec("world_sensors", deps=[], stale_window=1,
                 desc="Fed/World sensors", fn=_run_world_sensors),
        NodeSpec("macro_state", deps=["world_sensors"], stale_window=1,
                 desc="MacroState classifier", fn=_run_macro_state),
        NodeSpec("transmission", deps=["macro_state"], stale_window=1,
                 desc="Economic transmission", fn=_run_transmission),
        NodeSpec("governor", deps=["transmission"], stale_window=1,
                 desc="Bayesian Governor — Morning Brief", fn=_run_governor),
        NodeSpec("system_audit", deps=["governor"], stale_window=None,
                 desc="System Health Report (CRO)", fn=None),
    ],
    "close": [
        NodeSpec("market_data", deps=[], stale_window=1,
                 desc="EOD pipeline — market data", fn=_run_market_data),
        NodeSpec("breadth", deps=["market_data"], stale_window=1,
                 desc="Breadth engine", fn=_run_breadth),
        NodeSpec("sector", deps=["breadth"], stale_window=1,
                 desc="Sector flow / rotation", fn=_run_sector),
        NodeSpec("governor", deps=["sector"], stale_window=1,
                 desc="Bayesian Governor — Daily Report", fn=_run_governor),
        NodeSpec("system_audit", deps=["governor"], stale_window=None,
                 desc="System Health Report (CRO)", fn=None),
    ],
"earnings": [
         NodeSpec("financial_crawl", deps=[], stale_window=7,
                  desc="Financial crawl (BCTT)", fn=_run_financial_crawl),
         NodeSpec("health_v2", deps=["financial_crawl"], stale_window=1,
                  desc="Company Health Engine v2", fn=_run_health_v2),
         NodeSpec("valuation", deps=["health_v2"], stale_window=7,
                  desc="Valuation Engine", fn=_run_valuation),
         NodeSpec("vn20_builder", deps=["valuation"], stale_window=7,
                  desc="PTCK_VN20 Dynamic Index Builder (LAW-008 Cluster Compression)",
                  fn=_run_vn20_builder),
         NodeSpec("governor", deps=["valuation"], stale_window=1,
                  desc="Bayesian Governor", fn=_run_governor),
         NodeSpec("system_audit", deps=["governor"], stale_window=None,
                  desc="System Health Report (CRO)", fn=None),
     ],
}


# ════════════════════════════════════════════════════════════════════
# DailyCycleOrchestrator
# ════════════════════════════════════════════════════════════════════

class DailyCycleOrchestrator:
    """Điều phối 3 kịch bản; mọi workflow kết thúc bằng SystemAuditor."""

    def __init__(self, db_path: Optional[str] = None,
                 scenarios: Optional[Dict[str, List[NodeSpec]]] = None):
        self.db_path = db_path or _cycle_db_path()
        self.scenarios = scenarios or SCENARIOS

    def build_graph(self, scenario: str,
                    audit_days: int = 90, persist_audit: bool = False) -> ExecutionGraph:
        if scenario not in self.scenarios:
            raise ValueError(f"unknown scenario: {scenario}")
        g = ExecutionGraph()
        for spec in self.scenarios[scenario]:
            fn = spec.fn
            if spec.id == "system_audit" and fn is None:
                fn = _make_audit_runner(audit_days, persist_audit)
            g.add_node(spec.id, run=fn, deps=spec.deps,
                       stale_window=spec.stale_window, desc=spec.desc)
        return g

    def _fresh_checker(self, scenario: str) -> Callable[[str], bool]:
        windows = {s.id: s.stale_window for s in self.scenarios[scenario]}
        def _check(nid: str) -> bool:
            w = windows.get(nid)
            if not w:
                return False
            return _is_fresh(scenario, nid, w, db_path=self.db_path)
        return _check

    def run(self, scenario: str, skip_fresh: bool = True,
            audit_days: int = 90, persist_audit: bool = False,
            persist_log: bool = True) -> Dict:
        graph = self.build_graph(scenario,
                                 audit_days=audit_days,
                                 persist_audit=persist_audit)
        results = graph.run(skip_fresh=skip_fresh,
                            fresh_checker=self._fresh_checker(scenario))

        if persist_log:
            for nid, res in results.items():
                record_run_log(scenario, nid, res.get("status", "OK"),
                               db_path=self.db_path)

        health_report = results.get("system_audit")
        confidence_score = None
        veto_reasons: List[str] = []
        if isinstance(health_report, dict):
            confidence_score = health_report.get("overall_score")
            veto_reasons = _extract_veto_reasons(health_report)

        return {
            "workflow": scenario,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "nodes": results,
            "health_report": health_report,
            "confidence_score": confidence_score,
            "veto_reasons": veto_reasons,
            "status": (health_report or {}).get("status", "NO_DATA"),
        }


def _extract_veto_reasons(health_report: Dict) -> List[str]:
    """Tập hợp findings HIGH/CRITICAL từ audit → lý do veto/quyết định."""
    reasons: List[str] = []
    for domain, rep in (health_report.get("domains") or {}).items():
        for f in rep.get("findings") or []:
            if f.get("severity") in ("HIGH", "CRITICAL"):
                reasons.append(f"{domain}: {f.get('message', '')}")
    return reasons


# ════════════════════════════════════════════════════════════════════
# Report printing
# ════════════════════════════════════════════════════════════════════

def print_cycle_report(report: Dict):
    print(f"\n  {'='*100}")
    print(f"  DAILY CYCLE — {report.get('workflow', '?')} | "
          f"{report.get('generated_at', '')}")
    print(f"  {'='*100}")
    nodes = report.get("nodes", {})
    for nid, res in nodes.items():
        icon = {"OK": "✅", "SKIPPED": "⏭", "FAILED": "❌"}.get(
            res.get("status"), "•")
        print(f"  {icon} {nid:<16} {res.get('status', ''):<8} "
              f"{res.get('error', '')}")
    print(f"  {'─'*100}")
    hc = report.get("confidence_score")
    print(f"  Confidence: {hc:.1%}" if isinstance(hc, float) else "  Confidence: n/a")
    veto = report.get("veto_reasons", [])
    print(f"  Veto reasons ({len(veto)}):")
    for r in veto:
        print(f"    ⛔ {r}")
    print(f"  {'─'*100}")


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "morning"
    orch = DailyCycleOrchestrator()
    report = orch.run(scenario, skip_fresh=False,
                      persist_audit=True, persist_log=True)
    print_cycle_report(report)
