import sys
from collections import Counter

import numpy as np


class StateSpaceValidator:
    """
    Validates regime dynamics via Markov transition matrix, dwell times, entropy.
    Compares two regime sequences for behavioral equivalence (not metric similarity).
    """

    REGIMES = ["CRISIS", "RANGING", "TRENDING"]

    def __init__(self, label: str = ""):
        self.label = label

    # ── Analysis ──────────────────────────────────────────────────────

    def analyze(self, regimes: list[str]) -> dict:
        n = len(regimes)
        if n == 0:
            return {}

        # Count transitions
        transitions = Counter()
        for i in range(n - 1):
            transitions[(regimes[i], regimes[i + 1])] += 1

        # Transition matrix P(A -> B) for each A
        transition_matrix = {}
        for r in self.REGIMES:
            total = sum(v for (a, b), v in transitions.items() if a == r)
            row = {}
            for r2 in self.REGIMES:
                row[r2] = (transitions.get((r, r2), 0) / total) if total > 0 else 0.0
            transition_matrix[r] = row

        # Dwell time: consecutive days in same regime
        dwell = {r: [] for r in self.REGIMES}
        count = 1
        for i in range(1, n):
            if regimes[i] == regimes[i - 1]:
                count += 1
            else:
                dwell[regimes[i - 1]].append(count)
                count = 1
        dwell[regimes[-1]].append(count)

        dwell_stats = {}
        for r in self.REGIMES:
            vals = dwell[r]
            dwell_stats[r] = {
                "count": len(vals),
                "min": min(vals) if vals else 0,
                "max": max(vals) if vals else 0,
                "mean": round(np.mean(vals), 2) if vals else 0,
                "median": round(np.median(vals), 2) if vals else 0,
            }

        # Regime distribution
        regime_counts = Counter(regimes)
        distribution = {}
        for r in self.REGIMES:
            distribution[r] = {
                "days": regime_counts.get(r, 0),
                "pct": round(regime_counts.get(r, 0) / n * 100, 1),
            }

        # Shannon entropy of regime distribution
        probs = [regime_counts.get(r, 0) / n for r in self.REGIMES]
        probs = [p for p in probs if p > 0]
        entropy = -sum(p * np.log2(p) for p in probs)

        # Persistence metrics
        same_count = sum(1 for i in range(n - 1) if regimes[i] == regimes[i + 1])
        persist_ratio = same_count / (n - 1) if n > 1 else 0

        return {
            "total_days": n,
            "regime_distribution": distribution,
            "transition_matrix": transition_matrix,
            "dwell_stats": dwell_stats,
            "entropy": round(entropy, 4),
            "persist_ratio": round(persist_ratio, 4),
            "num_transitions": sum(1 for i in range(n - 1) if regimes[i] != regimes[i + 1]),
        }

    # ── Comparison ────────────────────────────────────────────────────

    def compare(self, actual: dict, predicted: dict) -> dict:
        keys = ["entropy", "persist_ratio", "num_transitions"]
        diffs = {}
        for k in keys:
            a = actual.get(k, 0)
            p = predicted.get(k, 0)
            diffs[k] = {
                "actual": a,
                "predicted": p,
                "delta": round(abs(a - p), 4),
                "delta_pct": round(abs(a - p) / max(a, 0.001) * 100, 1),
            }

        # Compare transition matrices
        tm_actual = actual.get("transition_matrix", {})
        tm_pred = predicted.get("transition_matrix", {})
        tm_diff = {}
        for r1 in self.REGIMES:
            row = {}
            for r2 in self.REGIMES:
                a = tm_actual.get(r1, {}).get(r2, 0)
                p = tm_pred.get(r1, {}).get(r2, 0)
                row[r2] = {"actual": round(a, 4), "predicted": round(p, 4), "delta": round(abs(a - p), 4)}
            tm_diff[r1] = row
        diffs["transition_matrix"] = tm_diff

        return diffs

    # ── Print ─────────────────────────────────────────────────────────

    def print_report(self, result: dict):
        _out = sys.stdout
        _enc = getattr(_out, "encoding", "utf-8") or "utf-8"

        def _p(s: str):
            try:
                print(s)
            except UnicodeEncodeError:
                safe = s.encode(_enc, errors="replace").decode(_enc)
                print(safe)

        _p(f"\n{'=' * 55}")
        _p(f"  State-Space Analysis: {self.label}")
        _p(f"{'=' * 55}")
        _p(f"  Total days: {result['total_days']}")
        _p(f"  Entropy:    {result['entropy']}")

        _p("\n  Regime Distribution:")
        for r in self.REGIMES:
            d = result["regime_distribution"][r]
            _p(f"    {r:10s} {d['days']:4d} days ({d['pct']:5.1f}%)")

        _p("\n  Transition Matrix P(next | current):")
        _p(f"    {'':>10s} {'CRISIS':>10s} {'RANGING':>10s} {'TRENDING':>10s}")
        for r1 in self.REGIMES:
            vals = [result["transition_matrix"].get(r1, {}).get(r2, 0) for r2 in self.REGIMES]
            _p(f"    {r1:>10s} {vals[0]:10.4f} {vals[1]:10.4f} {vals[2]:10.4f}")

        _p("\n  Dwell Time (consecutive days in regime):")
        for r in self.REGIMES:
            d = result["dwell_stats"][r]
            _p(
                f"    {r:10s} count={d['count']:3d} mean={d['mean']:6.1f}d median={d['median']:4.1f}d range={d['min']}-{d['max']}"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
            )

        _p(f"\n  Persist Ratio:          {result['persist_ratio']:.2%}")
        _p(f"  Total Regime Changes:   {result['num_transitions']}")
        _p(f"  Transitions per 100d:   {result['num_transitions'] / result['total_days'] * 100:.1f}")

    def print_comparison(self, diffs: dict):
        _out = sys.stdout
        _enc = getattr(_out, "encoding", "utf-8") or "utf-8"

        def _p(s: str):
            try:
                print(s)
            except UnicodeEncodeError:
                safe = s.encode(_enc, errors="replace").decode(_enc)
                print(safe)

        _p(f"\n{'=' * 55}")
        _p("  Comparison: State-Space Delta")
        _p(f"{'=' * 55}")
        for k, v in diffs.items():
            if k == "transition_matrix":
                _p("\n  Transition Matrix Delta:")
                _p(f"    {'':>10s} {'CRISIS':>10s} {'RANGING':>10s} {'TRENDING':>10s}")
                for r1 in self.REGIMES:
                    vals = [v.get(r1, {}).get(r2, {}).get("delta", 0) for r2 in self.REGIMES]
                    _p(f"    {r1:>10s} {vals[0]:10.4f} {vals[1]:10.4f} {vals[2]:10.4f}")
            else:
                a = v["actual"]
                p = v["predicted"]
                dp = v["delta_pct"]
                _p(f"  {k:25s} actual={a:>8}  predicted={p:>8}  delta={dp:5.1f}%")
