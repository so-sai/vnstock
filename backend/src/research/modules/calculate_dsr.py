"""calculate_dsr.py — Deflated Sharpe Ratio (Lopez de Prado & Lewis, 2014).

Tier-2 Quant Lab utility — OHLCV-only research scope.
Pham vi: backend/src/research/modules/ (khong cham production).

Cong thuc:
  E[SR0] = sqrt(V) * ((1-g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N*e)))
  DSR    = Phi( (SR_obs - E[SR0]) * sqrt(T-1)
                / sqrt(1 - skew*SR_obs + (kurt-1)/4 * SR_obs^2) )
V = phuong sai cua N Sharpe thu nghiem; g = Euler-Mascheroni.
Chi dung stdlib (statistics.NormalDist) — khong phu thuoc scipy.

Usage:
  python backend/src/research/modules/calculate_dsr.py
      --sharpe 1.2 --t 1000 --skew -0.3 --kurt 3.5 --trials 50
      [--trials-file trials.json --output .../dsr-result.json]
trials.json: {"trial_sharpes": [...]} de uoc luong V (mac dinh: V=0 -> bao loi).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist, variance


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


GAMMA = 0.5772156649  # Euler-Mascheroni
_PHI = NormalDist()


def expected_sharpe_null(var_trials: float, n_trials: int) -> float:
    """Sharpe ky vong duoi gia thiet null (khong skill) voi N lan thu."""
    if n_trials < 2:
        raise ValueError("n_trials phai >= 2")
    if var_trials < 0:
        raise ValueError("var_trials phai >= 0")
    v = math.sqrt(var_trials)
    return v * ((1 - GAMMA) * _PHI.inv_cdf(1 - 1.0 / n_trials) + GAMMA * _PHI.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(
    sr_obs: float,
    n_returns: int,
    skew: float,
    kurt: float,
    var_trials: float,
    n_trials: int,
) -> dict:
    """Tra ve dict {dsr, expected_sr_null, ...}. kurt = kurtosis thong (normal=3)."""
    if n_returns < 3:
        raise ValueError("n_returns phai >= 3")
    e0 = expected_sharpe_null(var_trials, n_trials)
    denom = 1 - skew * sr_obs + (kurt - 1.0) / 4.0 * sr_obs * sr_obs
    if denom <= 0:
        raise ValueError(f"mau so am/khong xac dinh: {denom}")
    z = (sr_obs - e0) * math.sqrt(n_returns - 1) / math.sqrt(denom)
    return {
        "dsr": _PHI.cdf(z),
        "z": z,
        "expected_sr_null": e0,
        "sr_obs": sr_obs,
        "n_returns": n_returns,
        "skew": skew,
        "kurt": kurt,
        "n_trials": n_trials,
        "var_trials": var_trials,
        "pass_threshold_095": _PHI.cdf(z) > 0.95,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Deflated Sharpe Ratio — Tier-2")
    ap.add_argument("--sharpe", type=float, required=True)
    ap.add_argument("--t", type=int, required=True, help="so quan sat loi nhuan")
    ap.add_argument("--skew", type=float, default=0.0)
    ap.add_argument("--kurt", type=float, default=3.0)
    ap.add_argument("--trials", type=int, required=True, help="so cau hinh da thu N")
    ap.add_argument("--var-trials", type=float, default=None, help="phuong sai Sharpe qua cac lan thu (uu tien trials-file)")
    ap.add_argument("--trials-file", default=None, help='JSON {"trial_sharpes": [...]} de uoc luong V')
    ap.add_argument("--output", default=None)
    a = ap.parse_args()

    var_t = a.var_trials
    if a.trials_file:
        obj = json.loads(Path(a.trials_file).read_text(encoding="utf-8"))
        shr = obj.get("trial_sharpes", [])
        if len(shr) < 2:
            print("ERROR: trials-file can >= 2 trial_sharpes", file=sys.stderr)
            return 2
        var_t = variance(shr)
    if var_t is None:
        print("ERROR: thieu --var-trials hoac --trials-file (khong doan V).", file=sys.stderr)
        return 2

    try:
        res = deflated_sharpe(a.sharpe, a.t, a.skew, a.kurt, var_t, a.trials)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    res["data_scope"] = "OHLCV_only"
    out = json.dumps(res, indent=2, ensure_ascii=False)
    if a.output:
        p = Path(a.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
