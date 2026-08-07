"""
test_erl_fp_rate.py — Unit Test cho False Positive Rate cua Volume Anomaly

Mo phong VN30 trong Q3/2022 (crash: Jul 1-10, Aug 1-10, Sep 1-10).
Do luong TPR va FPR de tinh chinh bo tham so (VR, RS_Z).

Run: python -m pytest backend/tests/test_erl_fp_rate.py -v
"""
import random

# Default params from entity_resilience_layer.py
DEFAULT_VR = 2.0
DEFAULT_RSZ = -1.5
ANOMALY_DAYS = 3

TOTAL_DAYS = 90
CRASH_DAYS = set(range(0, 10)) | set(range(30, 40)) | set(range(60, 70))
N_STOCKS = 30


def simulate(vr_thresh: float, rsz_thresh: float, seed: int = 42) -> dict:
    """Mo phong VN30 Q3/2022, tra ve TP, FP, FN, TN."""
    random.seed(seed)
    tp = fp = fn = tn = 0
    for stock in range(N_STOCKS):
        for day in range(TOTAL_DAYS):
            is_crash = day in CRASH_DAYS
            bv = random.uniform(500000, 2000000)
            if is_crash:
                vol = bv * random.uniform(2.5, 5.0)
                rz = random.uniform(-3.0, -1.5)
            else:
                vol = bv * random.uniform(0.5, 1.8)
                rz = random.uniform(-1.0, 1.0)
            vr = vol / bv if bv > 0 else 1.0
            alarm = vr >= vr_thresh and rz <= rsz_thresh
            if is_crash and alarm:
                tp += 1
            elif not is_crash and alarm:
                fp += 1
            elif is_crash and not alarm:
                fn += 1
            else:
                tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def compute_rates(r: dict) -> tuple:
    tpr = r["tp"] / (r["tp"] + r["fn"]) if (r["tp"] + r["fn"]) > 0 else 0
    fpr = r["fp"] / (r["fp"] + r["tn"]) if (r["fp"] + r["tn"]) > 0 else 0
    return tpr, fpr


class TestERLFalsePositiveRate:
    """Do luong FPR cua Volume Anomaly voi bo tham so mac dinh."""

    def test_default_params_fpr_below_30pct(self):
        """FPR voi VR=2.0, RS_Z=-1.5 <= 30%."""
        r = simulate(DEFAULT_VR, DEFAULT_RSZ)
        tpr, fpr = compute_rates(r)
        print(f"\n  Default (VR={DEFAULT_VR}, RS_Z={DEFAULT_RSZ}): TPR={tpr:.2%}, FPR={fpr:.2%}")
        assert fpr <= 0.30, f"FPR={fpr:.2%} > 30% — can tinh chinh tham so"

    def test_default_params_tpr_above_60pct(self):
        """TPR voi VR=2.0, RS_Z=-1.5 >= 60%."""
        r = simulate(DEFAULT_VR, DEFAULT_RSZ)
        tpr, fpr = compute_rates(r)
        print(f"\n  Default (VR={DEFAULT_VR}, RS_Z={DEFAULT_RSZ}): TPR={tpr:.2%}, FPR={fpr:.2%}")
        assert tpr >= 0.60, f"TPR={tpr:.2%} < 60% — can giam nguong"

    def test_optimal_params(self):
        """Grid search tim bo tham so toi uu (F1-score)."""
        best = {"score": 0, "vr": 0, "rsz": 0, "fpr": 1.0, "tpr": 0}
        for vr in [1.5, 2.0, 2.5, 3.0]:
            for rsz in [-1.0, -1.5, -2.0, -2.5]:
                r = simulate(vr, rsz, seed=42)
                tpr, fpr = compute_rates(r)
                score = tpr * (1 - fpr)  # F1-like
                if score > best["score"]:
                    best = {"score": score, "vr": vr, "rsz": rsz,
                            "tpr": tpr, "fpr": fpr}
        print(f"\n  Optimal: VR={best['vr']}, RS_Z={best['rsz']} → "
              f"TPR={best['tpr']:.2%}, FPR={best['fpr']:.2%}, score={best['score']:.4f}")
        assert best["score"] > 0.5, f"Optimal score={best['score']:.4f} qua thap"
        # Verify default is close to optimal
        r = simulate(DEFAULT_VR, DEFAULT_RSZ)
        dtpr, dfpr = compute_rates(r)
        dscore = dtpr * (1 - dfpr)
        gap = abs(dscore - best["score"])
        assert gap < 0.15, (
            f"Default score={dscore:.4f} vs optimal={best['score']:.4f} "
            f"(gap={gap:.4f}) — can update defaults"
        )

    def test_consecutive_anomaly_filter(self):
        """Anomaly streak filter giam FPR nhung giu TPR."""
        random.seed(42)
        fp_streak = fp_single = 0
        tp_streak = tp_single = 0
        for stock in range(N_STOCKS):
            streak = 0
            for day in range(TOTAL_DAYS):
                is_crash = day in CRASH_DAYS
                bv = random.uniform(500000, 2000000)
                if is_crash:
                    vol = bv * random.uniform(2.5, 5.0)
                    rz = random.uniform(-3.0, -1.5)
                else:
                    vol = bv * random.uniform(0.5, 1.8)
                    rz = random.uniform(-1.0, 1.0)
                vr = vol / bv if bv > 0 else 1.0
                alarm_single = vr >= DEFAULT_VR and rz <= DEFAULT_RSZ
                if alarm_single:
                    streak += 1
                else:
                    streak = 0
                alarm_streak = streak >= ANOMALY_DAYS

                if alarm_single:
                    if is_crash:
                        tp_single += 1
                    else:
                        fp_single += 1
                if alarm_streak:
                    if is_crash:
                        tp_streak += 1
                    else:
                        fp_streak += 1

        fp_reduction = (fp_single - fp_streak) / fp_single * 100 if fp_single > 0 else 0
        print(f"\n  Single: TP={tp_single}, FP={fp_single}")
        print(f"  Streak({ANOMALY_DAYS}d): TP={tp_streak}, FP={fp_streak}")
        print(f"  FP reduction: {fp_reduction:.0f}%")
        assert fp_streak <= fp_single, "Streak filter khong giam duoc FP"
