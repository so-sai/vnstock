"""selection_layer.py — Selection Layer v0: per-day ranking + budget allocation.

Thay thế FIFO decide() cho CAPITAL_DEPLOYMENT_ACTIONS:
  - Trong mỗi ngày, xếp hạng deployment candidates theo score (mặc định P_raw).
  - Chỉ giữ top-K với K = min(daily_cap, remaining budget của năm).
  - Candidates dưới floor tuyệt đối → WATCH (không tiêu slot). KHÔNG "vét đĩa".

PIT-safe: chỉ dùng thông tin decision-time (p_gain, action, date). Budget rolling
xuyên ngày theo thứ tự ngày tăng dần — candidate hôm nay không biết ngày mai.

Invariants:
  - N_EXECUTE/năm ≤ budget (ceiling, không phải quota; có thể < budget).
  - daily_cap ≥ 1 chặn việc nướng hết slot trong 1 phiên (2022-01-04 case).
  - p_gain_min là floor TUYỆT ĐỐI (bất biến), không fit từ IS.
"""

from __future__ import annotations

from calibration.evidence_ledger import CAPITAL_DEPLOYMENT_ACTIONS

EXECUTE = "EXECUTE"
WATCH = "WATCH"
REJECT = "REJECT"

DEFAULT_DAILY_CAP = 1
DEFAULT_P_GAIN_MIN = 0.55


def is_deployment(action: str | None) -> bool:
    """Action tiêu NEW_CAPITAL_DEPLOYMENT slot hay không."""
    return (action or "").strip().upper() in CAPITAL_DEPLOYMENT_ACTIONS


def rank_day(
    candidates: list[dict],
    *,
    remaining: int,
    daily_cap: int = DEFAULT_DAILY_CAP,
    p_gain_min: float = DEFAULT_P_GAIN_MIN,
    already_executed: set[str] | None = None,
) -> dict:
    """Xếp hạng deployment candidates của MỘT ngày.

    Args:
        candidates: list[dict] với key bắt buộc "p_gain", "action", "decision_id",
            "symbol".
        remaining: số slot còn lại của năm TRƯỚC khi xử lý ngày này.
        daily_cap: tối đa EXECUTE trong ngày.
        p_gain_min: floor tuyệt đối — candidate dưới mốc này → WATCH.
        already_executed: set symbol đã EXECUTE trong năm (dedup 1 lần/năm).
            Candidate thuộc set này → WATCH, không tiêu slot.

    Returns:
        {decision_id: decision} chỉ cho deployment candidates của ngày.
    """
    if daily_cap < 1:
        raise ValueError("daily_cap must be >= 1")
    done = already_executed or set()
    eligible = [c for c in candidates if c.get("symbol") not in done]
    ordered = sorted(eligible, key=lambda c: c.get("p_gain") or 0.0, reverse=True)
    n_take = min(daily_cap, max(0, remaining))
    result: dict = {}
    for i, c in enumerate(ordered):
        if (c.get("p_gain") or 0.0) < p_gain_min or i >= n_take:
            result[c["decision_id"]] = WATCH
        else:
            result[c["decision_id"]] = EXECUTE
    for c in candidates:
        if c["decision_id"] not in result:
            result[c["decision_id"]] = WATCH
    return result


def select_year(
    rows: list[dict],
    *,
    budget: int = 20,
    daily_cap: int = DEFAULT_DAILY_CAP,
    p_gain_min: float = DEFAULT_P_GAIN_MIN,
) -> dict:
    """Áp selection cho toàn bộ rows MỘT năm (đã có sẵn trong ledger).

    Args:
        rows: list[dict] đầy đủ của năm, mỗi row có "date", "decision_id",
            "action", "decision", "p_gain". Không cần sắp thứ tự sẵn — hàm
            group theo ngày và xử lý ngày theo thứ tự tăng dần.
        budget: tổng slot năm (ceiling).
        daily_cap: tối đa EXECUTE/ngày.
        p_gain_min: floor tuyệt đối.

    Returns:
        {decision_id: decision} — chỉ override deployment candidates;
        non-deployment giữ nguyên decision ban đầu.
    """
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)

    remaining = budget
    executed_symbols: set[str] = set()
    out: dict = {}
    for date in sorted(by_day):
        day_rows = by_day[date]
        # REJECT = quality gate (LOW_CONVICTION) đã chặn từ trước → không vào
        # deployment pool, giữ nguyên decision.
        deploy = [r for r in day_rows if is_deployment(r.get("action")) and r.get("decision") != REJECT]
        for r in day_rows:
            if not is_deployment(r.get("action")) or r.get("decision") == REJECT:
                out[r["decision_id"]] = r.get("decision")
        if not deploy:
            continue
        ranked = rank_day(
            deploy,
            remaining=remaining,
            daily_cap=daily_cap,
            p_gain_min=p_gain_min,
            already_executed=executed_symbols,
        )
        for did, dec in ranked.items():
            if dec == EXECUTE:
                sym = next(r["symbol"] for r in deploy if r["decision_id"] == did)
                executed_symbols.add(sym)
        n_exec = sum(1 for d in ranked.values() if d == EXECUTE)
        remaining -= n_exec
        out.update(ranked)
    return out
