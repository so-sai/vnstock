"""
CAO Readiness Gate — Orchestrator
==================================
Runs all 3 gates (A, B, C) and produces the final verdict:
  PASS → CAO Phase 1 is safe to execute
  FAIL → gates describe what must be resolved first
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
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config
from src.cao_readiness.gate_a_independence import gate_a_check
from src.cao_readiness.gate_b_injectability import gate_b_check
from src.cao_readiness.gate_c_regime_stability import gate_c_check
from src.cao_readiness.models import (
    GateResult,
    ReadinessVerdict,
)


def run_readiness_check(
    verbose: bool = True,
    save_report: bool = True,
) -> ReadinessVerdict:
    if verbose:
        print("=" * 60)
        print("  CAO READINESS GATE — Pre-Flight Validation")
        print("=" * 60)
        print()
    gate_a = gate_a_check()
    if verbose:
        _print_gate(gate_a)
    gate_b = gate_b_check()
    if verbose:
        _print_gate(gate_b)
    gate_c = gate_c_check()
    if verbose:
        _print_gate(gate_c)
    gates = [gate_a, gate_b, gate_c]
    fails = [g for g in gates if g.status == "FAIL"]
    warns = [g for g in gates if g.status == "WARN"]
    passes = [g for g in gates if g.status == "PASS"]
    overall_pass = len(fails) == 0
    parts = []
    if overall_pass:
        parts.append(f"ALL GATES PASS ({len(passes)}/{len(gates)})")
        if warns:
            parts.append(f"{len(warns)} WARNING(S)")
        summary = "OK  " + " | ".join(parts)
    else:
        parts.append(f"BLOCKED: {len(fails)} GATE(S) FAILED")
        parts.append(", ".join(g.gate_name for g in fails))
        if warns:
            parts.append(f"{len(warns)} WARNING(S)")
        summary = "FAIL " + " | ".join(parts)
    from src.cao_readiness.gate_a_independence import run_independence_test
    from src.cao_readiness.gate_b_injectability import run_injectability_test
    from src.cao_readiness.gate_c_regime_stability import run_regime_stability_test

    snapshots = None
    indep_report = run_independence_test(snapshots)
    inj_report = run_injectability_test(snapshots)
    stab_report = run_regime_stability_test()
    verdict = ReadinessVerdict(
        timestamp=datetime.now().isoformat(),
        overall_pass=overall_pass,
        independent=indep_report,
        injectable=inj_report,
        stable=stab_report,
        gates=gates,
        summary=summary,
    )
    if verbose:
        print()
        print("=" * 60)
        print(f"  FINAL VERDICT: {summary}")
        print("=" * 60)
        if not overall_pass:
            print()
            print("  [BLOCKED] CAO Phase 1 is BLOCKED.")
            print("  Resolve all FAIL gates before proceeding.")
            for g in fails:
                print(f"     - [{g.gate_name}]: {g.message}")
        else:
            print()
            print("  [OK] CAO Phase 1 is SAFE to execute.")
            if warns:
                print("  [WARN] Heed warnings before full production deployment.")
    if save_report:
        _save_verdict(verdict)
    return verdict


def _print_gate(gate: GateResult):
    icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print(f"  {icon.get(gate.status, '[?]')} {gate.gate_name}")
    print(f"     Status: {gate.status} | Score: {gate.score:.3f} | Threshold: {gate.threshold}")
    print(f"     {gate.message}")
    print()


def _save_verdict(verdict: ReadinessVerdict):
    path = src.config.DATA_DIR / "cao_readiness_report.json"
    try:
        serializable = {
            "timestamp": verdict.timestamp,
            "overall_pass": verdict.overall_pass,
            "summary": verdict.summary,
            "gates": [
                {
                    "gate_name": g.gate_name,
                    "status": g.status,
                    "score": g.score,
                    "threshold": g.threshold,
                    "message": g.message,
                    "details": g.details,
                }
                for g in verdict.gates
            ],
        }
        path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("[CAO_READINESS] Report saved to %s", path)
    except (OSError, TypeError, ValueError, KeyError) as e:
        logger.warning("[CAO_READINESS] Failed to save report: %s", e)


def print_verdict(verdict: ReadinessVerdict):
    print(
        json.dumps(
            {
                "timestamp": verdict.timestamp,
                "overall_pass": verdict.overall_pass,
                "summary": verdict.summary,
                "gates": [
                    {"name": g.gate_name, "status": g.status, "score": g.score, "message": g.message} for g in verdict.gates
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
