"""
CAO Readiness Gate — Critical Pre-Flight Module
================================================
Validates 3 pre-conditions before CAO Phase 1 (learning) can be safely executed.

Checks:
  A — Engine Independence: correlation matrix between engine signals
  B — Counterfactual Injectability: does each engine measurably affect decisions?
  C — Regime Stability Index: is the market regime stable enough for Bayesian learning?

Usage:
  from src.cao_readiness import run_readiness_check
  verdict = run_readiness_check()
  if verdict.overall_pass:
      print("CAO Phase 1 is SAFE to execute")
"""
from src.cao_readiness.orchestrator import run_readiness_check, print_verdict
from src.cao_readiness.models import ReadinessVerdict, GateResult

__all__ = [
    "run_readiness_check",
    "print_verdict",
    "ReadinessVerdict",
    "GateResult",
]
