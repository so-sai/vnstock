"""CAO Trust Bridge — CLI Entry Point

Usage:
    python -m src.cao_validation                  # Run full validation
    python -m src.cao_validation --check-trust     # Show trust states
    python -m src.cao_validation --check-matrix    # Show promotion matrix
    python -m src.cao_validation --json            # Machine-readable output
"""
import sys
import json
from pathlib import Path


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
from src.cao_validation.regime_promotion_matrix import get_matrix


def main():
    check_trust = "--check-trust" in sys.argv
    check_matrix = "--check-matrix" in sys.argv
    json_output = "--json" in sys.argv
    quiet = "--quiet" in sys.argv
    if check_trust:
        acc = get_accumulator()
        states = acc.get_state()
        if json_output:
            data = {
                regime: {
                    "confidence": s.confidence,
                    "mean_consistency": s.mean_consistency,
                    "total_samples": s.total_samples,
                    "drift_score": s.drift_score,
                    "structural_shift": s.structural_shift,
                }
                for regime, s in states.items()
            }
            print(json.dumps(data, indent=2))
        else:
            if not states:
                print("No trust states accumulated yet.")
            for regime, s in states.items():
                print(f"  {regime}: confidence={s.confidence:.4f} "
                      f"consistency={s.mean_consistency:.4f} "
                      f"samples={s.total_samples} drift={s.drift_score:.4f} "
                      f"{'SHIFT' if s.structural_shift else 'stable'}")
        return
    if check_matrix:
        matrix = get_matrix()
        if json_output:
            data = {
                "frozen": matrix.frozen_regimes,
                "thresholds": {
                    r: {
                        "required_consistency": t.required_consistency,
                        "required_samples": t.required_samples,
                        "strictness": t.strictness,
                    }
                    for r, t in matrix.matrix.items()
                },
            }
            print(json.dumps(data, indent=2))
        else:
            print("Regime Promotion Matrix:")
            for r, t in matrix.matrix.items():
                frozen = " [FROZEN]" if matrix.is_frozen(r) else ""
                print(f"  {r}: consistency>={t.required_consistency} "
                      f"samples>={t.required_samples} "
                      f"strictness={t.strictness}{frozen}")
        return
    if not quiet:
        print("=" * 56)
        print("  CAO TRUST BRIDGE — Validation Pipeline")
        print("=" * 56)
    report = run_full_validation()
    if json_output:
        print(json.dumps({
            "timestamp": report.timestamp,
            "overall_promotable": report.overall_promotable,
            "summary": report.summary,
            "verdicts": [
                {"regime": v.regime, "can_promote": v.can_promote,
                 "failures": v.failures}
                for v in report.promotion_verdicts
            ],
            "distribution_tests": [
                {"regime": t.regime, "test": t.test_name,
                 "equivalent": t.equivalent, "statistic": t.statistic,
                 "p_value": t.p_value}
                for t in report.distribution_tests
            ],
        }, indent=2, ensure_ascii=False))
    else:
        print()
        for v in report.promotion_verdicts:
            icon = "[PROMOTABLE]" if v.can_promote else "[BLOCKED]"
            print(f"  {icon} {v.regime}: {' | '.join(v.failures) if v.failures else 'ALL GATES PASS'}")
            print(f"     {v.message}")
        print()
        if report.distribution_tests:
            print("  Distribution Tests:")
            for t in report.distribution_tests:
                icon = "[EQ]" if t.equivalent else "[DIFF]"
                print(f"    {icon} {t.regime} {t.test_name}: "
                      f"D={t.statistic:.4f} p={t.p_value:.4f} "
                      f"(n_shadow={t.n_shadow} n_real={t.n_real})")
        print()
        print(f"  SUMMARY: {report.summary}")


if __name__ == "__main__":
    main()
