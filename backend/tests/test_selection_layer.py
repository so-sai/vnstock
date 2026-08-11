"""test_selection_layer.py — TDD cho Selection Layer v0 (per-day ranking + budget).

Chứng minh 3 quy tắc bọc thép:
  1. Rank theo p_gain nội ngày (thay FIFO alphabet) → VSC (0.591) thắng AAA (0.553).
  2. daily_cap chặn nướng hết slot trong 1 phiên.
  3. p_gain_min floor tuyệt đối → dưới mốc là WATCH, không "vét đĩa".
"""

import pytest

from calibration.selection_layer import (
    DEFAULT_DAILY_CAP,
    DEFAULT_P_GAIN_MIN,
    EXECUTE,
    WATCH,
    is_deployment,
    rank_day,
    select_year,
)


def _c(did, date, symbol, p_gain, action="SCALE_IN", decision="EXECUTE"):
    return {
        "decision_id": did,
        "date": date,
        "symbol": symbol,
        "p_gain": p_gain,
        "action": action,
        "decision": decision,
    }


# ── rank_day ────────────────────────────────────────────────────────────────


def test_rank_day_picks_highest_p_gain_not_first_in_list():
    # FIFO (alphabet) sẽ lấy AAA trước; ranking phải chọn VSC.
    candidates = [
        _c(1, "2022-01-04", "AAA", 0.5535),
        _c(2, "2022-01-04", "VSC", 0.5908),
        _c(3, "2022-01-04", "DGC", 0.5750),
    ]
    r = rank_day(candidates, remaining=20, daily_cap=1)
    assert r == {2: EXECUTE, 1: WATCH, 3: WATCH}


def test_rank_day_daily_cap_limits_executes():
    candidates = [
        _c(1, "2022-01-04", "A", 0.60),
        _c(2, "2022-01-04", "B", 0.59),
        _c(3, "2022-01-04", "C", 0.58),
    ]
    r = rank_day(candidates, remaining=20, daily_cap=1)
    assert r[1] == EXECUTE and r[2] == WATCH and r[3] == WATCH
    assert sum(1 for d in r.values() if d == EXECUTE) == 1


def test_rank_day_daily_cap_two():
    candidates = [
        _c(1, "2022-01-04", "A", 0.60),
        _c(2, "2022-01-04", "B", 0.59),
        _c(3, "2022-01-04", "C", 0.58),
    ]
    r = rank_day(candidates, remaining=20, daily_cap=2)
    assert r[1] == EXECUTE and r[2] == EXECUTE and r[3] == WATCH


def test_rank_day_respects_remaining_budget():
    candidates = [
        _c(1, "2022-01-04", "A", 0.60),
        _c(2, "2022-01-04", "B", 0.59),
    ]
    # remaining=1: chỉ 1 lệnh được tiêu slot
    r = rank_day(candidates, remaining=1, daily_cap=5)
    assert r[1] == EXECUTE and r[2] == WATCH


def test_rank_day_no_slot_remaining():
    candidates = [_c(1, "2022-01-04", "A", 0.60)]
    r = rank_day(candidates, remaining=0, daily_cap=1)
    assert r == {1: WATCH}


def test_rank_day_p_gain_floor_blocks_below_min():
    candidates = [
        _c(1, "2022-01-04", "A", 0.56),
        _c(2, "2022-01-04", "B", 0.543),
    ]
    r = rank_day(candidates, remaining=20, daily_cap=1, p_gain_min=0.55)
    # B (0.543) dưới floor → WATCH dù là candidate duy nhất hợp lệ sau rank
    assert r[1] == EXECUTE and r[2] == WATCH


def test_rank_day_all_below_floor_stands_outside():
    candidates = [_c(1, "2023-08-29", "DGW", 0.543), _c(2, "2023-08-29", "FPT", 0.544)]
    r = rank_day(candidates, remaining=20, daily_cap=1, p_gain_min=0.55)
    assert r == {1: WATCH, 2: WATCH}


def test_rank_day_requires_positive_daily_cap():
    with pytest.raises(ValueError):
        rank_day([_c(1, "2022-01-04", "A", 0.60)], remaining=5, daily_cap=0)


# ── is_deployment ───────────────────────────────────────────────────────────


def test_is_deployment():
    assert is_deployment("BUY") is True
    assert is_deployment("SCALE_IN") is True
    assert is_deployment("OPEN") is True
    assert is_deployment("HOLD") is False
    assert is_deployment("REDUCE") is False
    assert is_deployment(None) is False


# ── select_year (rolling budget xuyên ngày) ─────────────────────────────────


def test_select_year_rolls_budget_across_days():
    rows = [
        _c(1, "2022-01-04", "A", 0.60),
        _c(2, "2022-01-04", "B", 0.59),
        _c(3, "2022-01-05", "C", 0.62),
        _c(4, "2022-01-06", "D", 0.61),
    ]
    r = select_year(rows, budget=2, daily_cap=1, p_gain_min=0.55)
    # Ngày 04: A (0.60) thắng B; Ngày 05: C (0.62); Ngày 06: budget hết → D WATCH
    assert r[1] == EXECUTE and r[2] == WATCH
    assert r[3] == EXECUTE
    assert r[4] == WATCH


def test_select_year_stands_outside_when_nothing_qualifies():
    rows = [_c(1, "2023-08-29", "DGW", 0.543), _c(2, "2023-09-05", "FPT", 0.544)]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    assert r[1] == WATCH and r[2] == WATCH


def test_select_year_never_exceeds_budget():
    rows = []
    did = 1
    for day in range(30):
        rows.append(_c(did, f"2022-01-{day+1:02d}", f"S{day}", 0.60))
        did += 1
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    assert sum(1 for d in r.values() if d == EXECUTE) == 20


def test_select_year_keeps_non_deployment_decision():
    rows = [
        _c(1, "2022-01-04", "A", 0.60, action="SCALE_IN"),
        _c(2, "2022-01-04", "B", 0.30, action="REDUCE", decision="REDUCE"),
    ]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    assert r[1] == EXECUTE
    assert r[2] == "REDUCE"


def test_select_year_p_gain_min_applies_globally():
    rows = [
        _c(1, "2022-01-04", "A", 0.62),
        _c(2, "2022-01-05", "B", 0.60),
        _c(3, "2022-01-06", "C", 0.549),  # sát dưới floor
    ]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    assert r[1] == EXECUTE and r[2] == EXECUTE and r[3] == WATCH


# ── symbol dedup (1 lần/năm) ────────────────────────────────────────────────


def test_rank_day_skips_already_executed_symbol():
    candidates = [
        _c(1, "2022-01-04", "DCM", 0.645),
        _c(2, "2022-01-04", "FPT", 0.590),
    ]
    r = rank_day(candidates, remaining=20, daily_cap=1, p_gain_min=0.55,
                 already_executed={"DCM"})
    assert r[1] == WATCH  # DCM đã mua → bỏ qua
    assert r[2] == EXECUTE  # FPT thay thế


def test_rank_day_dedup_candidate_never_consumes_slot():
    candidates = [
        _c(1, "2022-01-04", "DCM", 0.645),
        _c(2, "2022-01-04", "FPT", 0.590),
    ]
    r = rank_day(candidates, remaining=1, daily_cap=1, p_gain_min=0.55,
                 already_executed={"DCM"})
    # DCM bị WATCH, FPT ăn slot duy nhất còn lại
    assert r[2] == EXECUTE


def test_select_year_dedup_symbol_once_per_year():
    rows = [
        _c(1, "2022-01-04", "DCM", 0.645),
        _c(2, "2022-01-05", "DCM", 0.645),  # cùng symbol, p_gain cao nhất
        _c(3, "2022-01-05", "FPT", 0.590),
        _c(4, "2022-01-06", "DCM", 0.644),
        _c(5, "2022-01-06", "VSC", 0.591),
    ]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    # DCM chỉ EXECUTE 1 lần (ngày 04); 05/06 chọn FPT/VSC thay thế
    assert r[1] == EXECUTE and r[2] == WATCH and r[3] == EXECUTE
    assert r[4] == WATCH and r[5] == EXECUTE
    exec_dids = [did for did, dec in r.items() if dec == EXECUTE]
    assert len(exec_dids) == 3


def test_select_year_dedup_when_single_symbol_stands_outside():
    rows = [
        _c(1, "2022-01-04", "DCM", 0.60),
        _c(2, "2022-01-05", "DCM", 0.61),  # DCM đã chọn → bỏ qua
    ]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    assert r[1] == EXECUTE and r[2] == WATCH


def test_select_year_keeps_reject_out_of_deployment_pool():
    rows = [
        _c(1, "2022-01-04", "A", 0.62, action="SCALE_IN", decision="REJECT"),
        _c(2, "2022-01-04", "B", 0.61, action="SCALE_IN", decision="EXECUTE"),
    ]
    r = select_year(rows, budget=20, daily_cap=1, p_gain_min=0.55)
    # A bị REJECT (Weak evidence) — không được vực dậy thành EXECUTE
    assert r[1] == "REJECT"
    assert r[2] == EXECUTE


def test_select_year_reject_never_consumes_budget():
    rows = [
        _c(1, "2022-01-04", "A", 0.65, action="SCALE_IN", decision="REJECT"),
        _c(2, "2022-01-05", "B", 0.60, action="SCALE_IN", decision="EXECUTE"),
        _c(3, "2022-01-06", "C", 0.61, action="SCALE_IN", decision="EXECUTE"),
    ]
    r = select_year(rows, budget=2, daily_cap=1, p_gain_min=0.55)
    assert r[1] == "REJECT"
    assert r[2] == EXECUTE and r[3] == EXECUTE
