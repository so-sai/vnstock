"""scoring.py — Log-Loss, Brier Score, ECE, MCE for P4 Calibration.

Conventions:
  p  = predicted P(Gain) from BayesianGovernor  [0, 1]
  y  = actual outcome: 1 if gain, 0 if loss
  acc_bin = fraction of y=1 within a confidence bin
  conf_bin = mean predicted p within that bin
"""

import math


def log_loss(p: float, y: float) -> float:
    """Strictly proper scoring rule.

    LL = -[y * ln(p) + (1-y) * ln(1-p)]

    Penalty diverges as p→0 or p→1 when wrong.
    """
    p = max(min(p, 1 - 1e-15), 1e-15)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def brier_score(p: float, y: float) -> float:
    """Bounded [0, 1] secondary metric."""
    return (p - y) ** 2


def _make_bins(ps: list[float], ys: list[float], n_bins: int = 10) -> list[dict]:
    """Bin predictions by confidence decile."""
    paired = sorted(zip(ps, ys), key=lambda x: x[0])
    n = len(paired)
    bins = []
    for i in range(n_bins):
        lo = i * n // n_bins
        hi = (i + 1) * n // n_bins if i < n_bins - 1 else n
        if lo >= hi:
            continue
        batch = paired[lo:hi]
        conf = sum(p for p, _ in batch) / (hi - lo)
        acc = sum(y for _, y in batch) / (hi - lo)
        bins.append({"lo": lo, "hi": hi, "n": hi - lo, "conf": conf, "acc": acc})
    return bins


def ece(ps: list[float], ys: list[float], n_bins: int = 10) -> float:
    """Expected Calibration Error — weighted |acc - conf|."""
    bins = _make_bins(ps, ys, n_bins)
    if not bins:
        return 0.0
    total = sum(b["n"] for b in bins)
    return sum(b["n"] / total * abs(b["acc"] - b["conf"]) for b in bins)


def mce(ps: list[float], ys: list[float], n_bins: int = 10) -> float:
    """Maximum Calibration Error — worst-case |acc - conf|."""
    bins = _make_bins(ps, ys, n_bins)
    if not bins:
        return 0.0
    return max(abs(b["acc"] - b["conf"]) for b in bins)


def reliability_curve(ps: list[float], ys: list[float], n_bins: int = 10) -> list[dict]:
    """Return reliability diagram data: bin, conf, acc, count."""
    bins = _make_bins(ps, ys, n_bins)
    return [
        {
            "bin": i,
            "n": b["n"],
            "confidence": round(b["conf"], 4),
            "accuracy": round(b["acc"], 4),
            "gap": round(b["acc"] - b["conf"], 4),
        }
        for i, b in enumerate(bins)
    ]
