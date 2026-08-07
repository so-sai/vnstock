"""calibrator.py — Bayesian conjugate update of Likelihood Ratios + Outcome Resolution.

Each evidence level maintains a Beta posterior:
  alpha = 1 + N_gains_when_evidence_active
  beta  = 1 + N_losses_when_evidence_active

LR = [alpha/(alpha+beta)] / [1 - alpha/(alpha+beta)] / prior_odds

The new LRs can be reloaded into P3 Governor to replace hard-coded LR_MACRO etc.
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from calibration.prediction_log import (
    get_beta_posteriors,
    get_outcomes_for_calibration,
    get_unresolved_predictions,
    init_calibration_history,
    insert_calibration_snapshot,
    resolve_outcome,
    upsert_beta,
)
from calibration.scoring import brier_score, ece, log_loss, mce

# Prior odds from P3 Governor (must match company_state.PRIOR_ODDS)
PRIOR_ODDS = 0.53 / (1.0 - 0.53)

# Evidence keys map from evidence_column_name → (table_name in prediction_log, value_column)
EVIDENCE_COLUMNS = [
    "macro_state",
    "transmission_phase",
    "sector_phase",
    "health_archetype",
    "valuation_zone",
    "behavior_position",
]


# ═══════════════════════════════════════════════════════════════
# OUTCOME RESOLUTION — gán nhãn thực tế từ dữ liệu giá
# ═══════════════════════════════════════════════════════════════


def _get_price_db() -> sqlite3.Connection:
    """Kết nối đến screener_cache.db để tra cứu giá."""
    _candidate = Path(sys.executable).resolve().parent
    if Path(sys.executable).stem.lower().startswith("python"):
        _p = Path(__file__).resolve().parent.parent.parent.parent
        for _par in [_p] + list(_p.parents):
            if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
                _candidate = _par
                break
    db_path = _candidate / "backend" / "data" / "screener_cache.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def resolve_pending_outcomes(
    lookback_days: int = 90,
    hold_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Resolve unresolved predictions when future price data is available.

    Logic:
      1. Lấy tất cả unresolved predictions từ prediction_log
      2. Với mỗi prediction có date + hold_days <= today:
         - Tra cứu close price tại prediction date (entry_price)
         - Tra cứu close price tại prediction date + hold_days (exit_price)
         - Nếu exit_price > entry_price → outcome=1 (gain), else 0 (loss)
         - Tính log_loss và ghi vào prediction_log
      3. Cập nhật Beta posteriors
      4. Ghi calibration_history snapshot

    Args:
        lookback_days: Chỉ xem xét predictions trong N ngày gần đây
        hold_days: Số ngày nắm giữ để xác định outcome (mặc định 30 phiên)
        dry_run: Nếu True, chỉ báo cáo mà không ghi DB

    Returns:
        Dict với thống kê số lượng resolved, accuracy, log_loss
    """
    today = date.today()
    unresolved = get_unresolved_predictions()

    # Lọc predictions đã đủ thời gian hold
    eligible = []
    for p in unresolved:
        try:
            pred_date = datetime.strptime(p["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - pred_date).days >= hold_days:
            eligible.append(p)

    if not eligible:
        return {
            "status": "NO_ELIGIBLE",
            "n_unresolved": len(unresolved),
            "n_eligible": 0,
            "n_resolved": 0,
        }

    price_db = _get_price_db()
    resolved_count = 0
    gain_count = 0
    loss_count = 0
    ps = []
    ys = []

    for p in eligible:
        pred_id = p["id"]
        symbol = p["symbol"]
        pred_date = p["date"]
        p_gain = p["p_gain"]

        try:
            pred_dt = datetime.strptime(pred_date, "%Y-%m-%d")
        except ValueError:
            continue

        # Entry price: close at prediction date
        entry = price_db.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?",
            (symbol, pred_date),
        ).fetchone()
        if not entry:
            continue
        entry_price = entry["close"]

        # Exit price: close at pred_date + hold_days
        exit_dt = pred_dt + timedelta(days=hold_days)
        exit_dt.strftime("%Y-%m-%d")

        # Find closest available trading day (look forward up to 10 days)
        exit_price = None
        for offset in range(15):
            check_dt = pred_dt + timedelta(days=hold_days + offset)
            check_date = check_dt.strftime("%Y-%m-%d")
            row = price_db.execute(
                "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?",
                (symbol, check_date),
            ).fetchone()
            if row:
                exit_price = row["close"]
                break

        if exit_price is None or entry_price is None or entry_price == 0:
            continue

        # Outcome
        outcome = 1.0 if exit_price > entry_price else 0.0
        ll = log_loss(p_gain, outcome)

        if not dry_run:
            resolve_outcome(pred_id, outcome, ll)
            ps.append(p_gain)
            ys.append(outcome)

        if outcome == 1.0:
            gain_count += 1
        else:
            loss_count += 1
        resolved_count += 1

    price_db.close()

    if dry_run:
        for p in eligible[:5]:
            print(f"  Would resolve: {p['symbol']} @ {p['date']} P(Gain)={p['p_gain']:.3f}")
        return {
            "status": "DRY_RUN",
            "n_unresolved": len(unresolved),
            "n_eligible": len(eligible),
            "n_resolved": resolved_count,
            "n_gain": gain_count,
            "n_loss": loss_count,
        }

    if resolved_count == 0:
        return {
            "status": "NO_PRICE_DATA",
            "n_unresolved": len(unresolved),
            "n_eligible": len(eligible),
            "n_resolved": 0,
            "n_gain": 0,
            "n_loss": 0,
        }

    # Compute calibration metrics
    mean_ll = sum(log_loss(p, y) for p, y in zip(ps, ys)) / len(ps)
    mean_br = sum(brier_score(p, y) for p, y in zip(ps, ys)) / len(ps)
    ece_val = ece(ps, ys)
    mce_val = mce(ps, ys)
    accuracy = sum(ys) / len(ys)

    # Update Beta posteriors
    update_beta_posteriors(days=lookback_days)

    # Record calibration history snapshot
    init_calibration_history()
    insert_calibration_snapshot(
        date_str=str(today),
        n_resolved=resolved_count,
        n_unresolved=len(unresolved) - resolved_count,
        mean_log_loss=round(mean_ll, 4),
        mean_brier=round(mean_br, 4),
        ece=round(ece_val, 4),
        mce=round(mce_val, 4),
        accuracy=round(accuracy, 4),
    )

    return {
        "status": "OK",
        "n_unresolved_pre": len(unresolved),
        "n_eligible": len(eligible),
        "n_resolved": resolved_count,
        "n_unresolved_post": len(unresolved) - resolved_count,
        "n_gain": gain_count,
        "n_loss": loss_count,
        "accuracy": round(accuracy, 4),
        "mean_log_loss": round(mean_ll, 4),
        "mean_brier": round(mean_br, 4),
        "ece": round(ece_val, 4),
        "mce": round(mce_val, 4),
    }


def print_resolve_report(result: dict, lang_mode: str = "full"):
    """In báo cáo outcome resolution ra console (song ngữ)."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except ImportError, AttributeError, TypeError, KeyError:

        def localize_label(label, m="full"):
            return label

    def _(x):
        return localize_label(x, lang_mode)

    status = result.get("status", "UNKNOWN")
    print(f"\n  {'=' * 60}")
    print(f"  P4 {_('Outcome')} {_('Resolution')} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {'=' * 60}")
    if status == "NO_ELIGIBLE":
        print(f"  ⏳ {_('Chưa có prediction nào đủ')} {result.get('n_unresolved', 0)} {_('Hold Days')}.")
        print(f"     {_('Unresolved')}: {result['n_unresolved']} | {_('Eligible')}: {result['n_eligible']}")
        return
    if status == "DRY_RUN":
        print(f"  🟡 DRY RUN — {_('không ghi DB')}")
        print(f"     {_('Unresolved')}: {result['n_unresolved']} | {_('Eligible')}: {result['n_eligible']}")
        print(f"     {_('Sẽ resolve')}: {result['n_resolved']}")
        return
    if status == "NO_PRICE_DATA":
        print(f"  ⚠️ {_('Không tìm thấy dữ liệu giá để resolve')}.")
        return
    print(f"  ✅ {_('Resolved')}: {result['n_resolved']} predictions")
    print(f"     {_('Gain')}: {result['n_gain']} | {_('Loss')}: {result['n_loss']}")
    print(f"     {_('Accuracy')}: {result['accuracy']:.2%}")
    print(f"     Mean {_('Log-Loss')}: {result['mean_log_loss']:.4f}")
    print(f"     Mean {_('Brier Score')}:    {result['mean_brier']:.4f}")
    print(f"     {_('ECE')}:           {result['ece']:.4f}")
    print(f"     {_('MCE')}:           {result['mce']:.4f}")
    if result.get("n_unresolved_post", 0) > 0:
        print(f"  ⏳ {_('Còn')} {result['n_unresolved_post']} predictions {_('chưa đủ hạn resolve')}.")


def update_beta_posteriors(days_back: int = 90):
    """Scan resolved predictions, update Beta(alpha, beta) per evidence level."""
    outcomes = get_outcomes_for_calibration(days_back)
    if not outcomes:
        return

    # Accumulate gains/losses per evidence level
    counts: dict[str, tuple[float, float]] = {}
    for row in outcomes:
        y = row["outcome"]
        for col in EVIDENCE_COLUMNS:
            val = str(row.get(col, "UNKNOWN")).strip().upper()
            key = f"{col}::{val}"
            a, b = counts.get(key, (0.0, 0.0))
            if y == 1.0:
                counts[key] = (a + 1.0, b)
            else:
                counts[key] = (a, b + 1.0)

    # Retrieve existing posteriors, add new counts, persist
    existing = get_beta_posteriors()
    for key, (add_a, add_b) in counts.items():
        old_a, old_b = existing.get(key, (1.0, 1.0))
        new_a = old_a + add_a
        new_b = old_b + add_b
        upsert_beta(key, new_a, new_b)


def compute_lr_from_beta(alpha: float, beta: float) -> float:
    """Compute LR from Beta posterior.

    LR = (alpha/(alpha+beta)) / (1 - alpha/(alpha+beta)) / PRIOR_ODDS
    """
    prob = alpha / (alpha + beta)
    if prob <= 0.0 or prob >= 1.0:
        return 1.0
    odds = prob / (1.0 - prob)
    lr = odds / PRIOR_ODDS
    return round(lr, 4)


def get_calibrated_lrs() -> dict[str, float]:
    """Return dict of evidence_key → calibrated LR."""
    posteriors = get_beta_posteriors()
    lrs = {}
    for key, (alpha, beta) in posteriors.items():
        lrs[key] = compute_lr_from_beta(alpha, beta)
    return lrs


def calibration_trend_report(days: int = 90) -> dict:
    """Return time-series trend of calibration metrics.

    Phát hiện degradation: nếu mean_log_loss tăng dần qua các tuần,
    đó là tín hiệu mô hình đang mất calibration.
    """
    from calibration.prediction_log import get_calibration_history

    history = get_calibration_history(days)
    if not history:
        return {"status": "NO_DATA", "n_snapshots": 0}

    # Tính trend: so sánh 2 nửa
    n = len(history)
    mid = n // 2
    recent = history[:mid]
    older = history[mid:]

    def avg_ll(h):
        vals = [r["mean_log_loss"] for r in h if r["mean_log_loss"] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    recent_ll = avg_ll(recent)
    older_ll = avg_ll(older)
    degradation = recent_ll > older_ll + 0.05  # ngưỡng 0.05

    return {
        "status": "OK",
        "n_snapshots": n,
        "latest": {
            "date": history[0]["date"],
            "mean_log_loss": history[0]["mean_log_loss"],
            "ece": history[0]["ece"],
            "accuracy": history[0]["accuracy"],
            "n_resolved": history[0]["n_resolved"],
        },
        "trend": {
            "recent_avg_log_loss": round(recent_ll, 4),
            "older_avg_log_loss": round(older_ll, 4),
            "degradation_detected": degradation,
            "direction": "DEGRADING" if degradation else "STABLE_OR_IMPROVING",
        },
        "snapshots": history[:10],  # 10 gần nhất
    }


def print_trend_report(report: dict, lang_mode: str = "full"):
    """In báo cáo xu hướng calibration (song ngữ)."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except ImportError, AttributeError, TypeError, KeyError:

        def localize_label(label, m="full"):
            return label

    def _(x):
        return localize_label(x, lang_mode)

    print(f"\n  {'=' * 60}")
    print(f"  P4 {_('Calibration')} {_('Trend')} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {'=' * 60}")
    if report["status"] == "NO_DATA":
        print(f"  {_('Chưa có dữ liệu calibration history')}.")
        print(f"  {_('Chạy')} 'calibrate resolve' {_('để tạo snapshot đầu tiên')}.")
        return
    print(f"  {_('Tổng số snapshot')}: {report['n_snapshots']}")
    print(f"\n  {_('Latest')}:")
    lat = report["latest"]
    print(f"    Date:      {lat['date']}")
    print(f"    {_('Resolved')}:  {lat['n_resolved']} predictions")
    print(f"    {_('Log-Loss')}:  {lat['mean_log_loss']:.4f}" if lat["mean_log_loss"] else f"    {_('Log-Loss')}:  N/A")
    print(f"    {_('ECE')}:       {lat['ece']:.4f}" if lat["ece"] else f"    {_('ECE')}:       N/A")
    print(f"    {_('Accuracy')}:  {lat['accuracy']:.2%}" if lat["accuracy"] else f"    {_('Accuracy')}:  N/A")
    print(f"\n  {_('Trend')} ({len(report.get('snapshots', []))} {_('snapshots gần nhất')}):")
    tr = report.get("trend", {})
    if tr:
        print(f"    Recent avg {_('Log-Loss')}: {tr.get('recent_avg_log_loss', 'N/A')}")
        print(f"    Older avg {_('Log-Loss')}:  {tr.get('older_avg_log_loss', 'N/A')}")
        flag = "🔴 " + _("Degradation") if tr.get("degradation_detected") else "🟢 STABLE"
        print(f"    {_('Xu hướng')}: {flag}")


def calibration_summary(days_back: int = 90) -> dict:
    """Return a full calibration diagnostic report."""
    outcomes = get_outcomes_for_calibration(days_back)
    if not outcomes:
        return {"status": "NO_DATA", "n_outcomes": 0}

    # Group by evidence column
    groups: dict[str, dict[str, dict]] = {}
    for col in EVIDENCE_COLUMNS:
        groups[col] = {}

    for row in outcomes:
        for col in EVIDENCE_COLUMNS:
            val = str(row.get(col, "UNKNOWN")).strip().upper()
            counts = groups[col]
            if val not in counts:
                counts[val] = {"n": 0, "gains": 0}
            counts[val]["n"] += 1
            if row["outcome"] == 1.0:
                counts[val]["gains"] += 1

    details = {}
    for col in EVIDENCE_COLUMNS:
        entries = []
        for val, cnt in sorted(groups[col].items()):
            key = f"{col}::{val}"
            posteriors = get_beta_posteriors()
            alpha, beta = posteriors.get(key, (1.0, 1.0))
            lr = compute_lr_from_beta(alpha, beta)
            accuracy = cnt["gains"] / cnt["n"] if cnt["n"] > 0 else 0.5
            entries.append(
                {
                    "value": val,
                    "n": cnt["n"],
                    "gains": cnt["gains"],
                    "accuracy": round(accuracy, 4),
                    "alpha": alpha,
                    "beta": beta,
                    "lr_calibrated": lr,
                }
            )
        details[col] = entries

    return {
        "status": "OK",
        "n_outcomes": len(outcomes),
        "window_days": days_back,
        "details": details,
    }
