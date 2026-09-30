"""Test backend/src/governor/unfreeze_gates_v2.py (Muc 2 — shadow unfreeze gates).

Zero-network: pure function test + tmp SQLite; khong doc screener_cache.db that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from governor import unfreeze_gates_v2 as ug

T = ug.THRESHOLDS


def _series(values: list[float]) -> list[float]:
    return values


def test_adv_ratio_math_and_insufficient_data():
    turnover = [100.0] * 17 + [90.0, 90.0, 90.0]  # ADV20 = (1700+270)/20 = 98.5, ADV3 = 90
    res = ug.adv_ratio(turnover)
    assert res is not None
    assert abs(res["adv3"] - 90.0) < 1e-9
    assert abs(res["adv20"] - 98.5) < 1e-9
    assert abs(res["ratio"] - 90.0 / 98.5) < 1e-9
    assert ug.adv_ratio([100.0] * 10) is None  # thieu 20 phien -> None, khong uoc luong
    assert ug.adv_ratio([]) is None


def _index_rows(ret_pct: float, vol_mult: float, n: int = 25) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append({"date": f"2026-01-{i + 1:02d}", "close": 1000.0 + i, "volume": 100.0, "ret_pct": 0.0})
    adv20 = 100.0
    rows[-1] = {"date": "2026-01-25", "close": 1200.0, "volume": adv20 * vol_mult, "ret_pct": ret_pct}
    return rows


def test_gate1_pass_via_ratio_branch():
    turnover = [100.0] * 17 + [160.0, 160.0, 160.0]  # ADV20 = 109, ADV3 = 160 -> 1.47x > 1.4x
    res = ug.gate1(_series(turnover), _index_rows(ret_pct=-0.2, vol_mult=0.8))
    assert res["pass"] is True
    assert res["branches"] == {"adv_ratio": True, "breakout": False}


def test_gate1_pass_via_breakout_branch():
    turnover = [100.0] * 20  # ratio = 1.0x -> FAIL chi so ADV
    # vol hom nay = 1.3 x ADV20 (20 phien TRUOC do = 100) -> vol_ok; ret 1.6% >= 1.5%
    res = ug.gate1(_series(turnover), _index_rows(ret_pct=1.6, vol_mult=1.3))
    assert res["pass"] is True
    assert res["branches"] == {"adv_ratio": False, "breakout": True}
    assert abs(res["readings"]["breakout"]["vol_mult"] - 1.3) < 1e-9


def test_gate1_fail_when_both_branches_fail():
    turnover = [100.0] * 20
    res = ug.gate1(_series(turnover), _index_rows(ret_pct=1.6, vol_mult=1.2))  # vol duong nhung khong dat 1.3x
    assert res["pass"] is False
    assert res["branches"]["breakout"] is False
    assert res["branches"]["adv_ratio"] is False


def test_adv_share_series_skips_invalid_totals():
    rows = [
        ("2026-01-05", 60, 40, 20.0),
        ("2026-01-06", 0, 0, 0.0),
        ("2026-01-07", None, 10, None),
        ("2026-01-08", 30, 70, -40.0),
    ]
    out = ug.adv_share_series(rows)
    assert [r["date"] for r in out] == ["2026-01-05", "2026-01-08"]
    assert out[0]["share"] == 60.0
    assert out[1]["share"] == 30.0


def _shares(values: list[float]) -> list[dict]:
    return [{"date": f"2026-01-{i + 1:02d}", "share": v, "net_pp": None} for i, v in enumerate(values)]


def test_gate2_streak_branch():
    res = ug.gate2(_shares([35.0, 40.0, 50.0, 55.0]))  # streak cuoi = 2 phien >= 50%
    assert res["pass"] is True
    assert res["branches"]["streak"] is True
    assert res["readings"]["streak"] == 2


def test_gate2_threshold_is_inclusive_at_50():
    res = ug.gate2(_shares([49.9, 50.0, 50.0]))  # chinh xac 50.0% van tinh la dat
    assert res["pass"] is True
    assert res["branches"]["streak"] is True


def test_gate2_delta_is_reference_not_gate():
    # streak chi 1 phien (44 < 50) nhung delta +26pp -> van FAIL (delta chi tham khao)
    res = ug.gate2(_shares([25.0, 50.0, 44.0, 51.0]))
    assert res["branches"]["streak"] is False
    assert res["branches"]["delta_ref"] is True
    assert res["pass"] is False
    assert abs(res["readings"]["delta_pp"] - 26.0) < 1e-9
    assert "THAM KHAO" in res["reason"]


def test_gate2_both_branches_fail():
    res = ug.gate2(_shares([50.0, 44.0, 44.0, 51.0]))  # streak 1; delta = 51-50 = +1pp
    assert res["pass"] is False
    assert res["branches"] == {"streak": False, "delta_ref": False}


def test_gate2_insufficient_data_is_fail_not_estimate():
    res = ug.gate2(_shares([50.0]))  # can >= 2 phien lien tiep
    assert res["pass"] is False
    assert "thieu du lieu" in res["reason"]


def test_spread_series_joins_and_sorts():
    vgb = [("2026-01-06", 4.5), ("2026-01-05", 4.6), ("2026-01-07", 4.5)]
    us = [("2026-01-05", 5.0), ("2026-01-06", 5.0), ("2026-01-08", 5.0)]
    out = ug.spread_series(vgb, us)
    assert [r["date"] for r in out] == ["2026-01-05", "2026-01-06"]
    assert round(out[0]["bps"], 6) == -40.0
    assert round(out[1]["bps"], 6) == -50.0


def _spreads(bps_values: list[float]) -> list[dict]:
    return [{"date": f"2026-01-{i + 1:02d}", "vgb": 4.5, "us10y": 5.0, "bps": b} for i, b in enumerate(bps_values)]


def test_gate3_delta_reference_does_not_gate():
    # READING_A delta 5 phien = -40bps < -30 -> van khong PASS (da bac bo); level -90bps FAIL
    spreads = _spreads([-50.0, -55.0, -60.0, -70.0, -80.0, -90.0])
    dxy = [(f"2026-01-{i + 1:02d}", 100.0) for i in range(60)]  # MA50 = 100, latest 100 -> khong thap hon
    res = ug.gate3(spreads, dxy)
    assert res["branches"]["spread_level"] is False
    assert res["branches"]["macro_triple"] is False
    assert res["pass"] is False  # khong con OR voi delta
    assert abs(res["readings"]["delta_bps_ref"] - (-40.0)) < 1e-9
    assert "THAM KHAO" in res["reason"]


def test_gate3_level_canonical_passes_without_macro():
    spreads = _spreads([-50.0, -45.0, -40.0, -35.0, -32.0, -20.0])  # level -20 >= -30 -> PASS
    dxy = [(f"2026-01-{i + 1:02d}", 100.0) for i in range(60)]  # DXY khong dat nhung level da dat
    res = ug.gate3(spreads, dxy)
    assert res["branches"]["spread_level"] is True
    assert res["pass"] is True


def test_gate3_macro_triple_passes_when_all_three_cool():
    spreads = _spreads([-70.0] * 6)  # level FAIL
    dxy = [(f"2026-01-{i + 1:02d}", 100.0 - i * 0.1) for i in range(60)]  # latest = 94.1 < MA50
    res = ug.gate3(spreads, dxy, {"us10y": 4.0, "usdvnd": 25000.0})
    assert res["branches"]["macro_triple"] is True
    assert res["pass"] is True
    assert res["partial_data"] is False


def test_gate3_macro_triple_needs_all_three_members():
    spreads = _spreads([-70.0] * 6)
    dxy = [(f"2026-01-{i + 1:02d}", 100.0 - i * 0.1) for i in range(60)]  # DXY < MA50 OK
    res = ug.gate3(spreads, dxy, {"us10y": 5.24, "usdvnd": 25000.0})  # US10Y > 4.5% -> bo ba huy
    assert res["branches"]["macro_triple"] is False
    assert res["pass"] is False
    res = ug.gate3(spreads, dxy, {"us10y": 4.0, "usdvnd": 25969.0})  # USD_VND > 25800 -> bo ba huy
    assert res["branches"]["macro_triple"] is False
    assert res["pass"] is False


def test_gate3_missing_dxy_marks_partial_data():
    spreads = _spreads([-50.0, -55.0, -60.0, -70.0, -80.0, -90.0])
    res = ug.gate3(spreads, [])
    assert res["branches"]["dxy_ma"] is False
    assert "dxy_error" in res["readings"]
    assert res["partial_data"] is True
    assert res["missing"] == ["dxy", "us10y", "usdvnd"]
    assert res["pass"] is False  # level FAIL + bo ba thieu du lieu -> dung ngoai (khong uoc luong)
    assert "NO_DATA" in res["reason"]


def test_deployment_ladder_gate_counts():
    # <=1 gate -> 0% cash defense
    lad = ug.deployment_ladder(1, "RANGING", None)
    assert (lad["tranche_pct"], lad["mode"]) == (0, "100% CASH DEFENSE")
    assert lad["action"] == "HOLD_CASH_EARN_INTEREST"
    assert "0%" in lad["reset_rule"] and "trailing stop" in lad["reset_rule"]  # reset_rule dinh kem moi bac
    # 2/3 -> 30%
    lad = ug.deployment_ladder(2, "RANGING", None)
    assert lad["tranche_pct"] == 30
    assert lad["action"] == "DEPLOY_30PCT_MANDATORY_IN_3_SESSIONS"


def test_deployment_ladder_tranche2_requires_profit():
    # 3/3 nhung tranche1 khong loi -> giuong 30% va ghi ro bi chan
    lad = ug.deployment_ladder(3, "UPTREND", False)
    assert lad["tranche_pct"] == 30
    assert any("loi" in b for b in lad["blocked_by"])
    # 3/3 + loi dem nhung khong UPTREND -> 70%
    lad = ug.deployment_ladder(3, "RANGING", True)
    assert lad["tranche_pct"] == 70
    assert lad["mode"] == "TRANCHE 2 (EXPANSION)"
    assert any("UPTREND" in b for b in lad["blocked_by"])
    # 3/3 + loi dem + UPTREND -> 100%
    lad = ug.deployment_ladder(3, "UPTREND", True)
    assert lad["tranche_pct"] == 100
    assert lad["mode"] == "TRANCHE 3 (FULL DEPLOYMENT)"
    assert lad["blocked_by"] == []


def test_deployment_ladder_no_data_profit_blocks_upgrade():
    lad = ug.deployment_ladder(3, "UPTREND", None)  # tranche1_in_profit = NO_DATA
    assert lad["tranche_pct"] == 30
    assert any("NO_DATA" in b for b in lad["blocked_by"])
    assert lad["conditions"]["tranche1_in_profit"] is None


def test_load_tranche1_state(tmp_path: Path):
    assert ug.load_tranche1_state(tmp_path / "missing.json") is None  # khong co state -> NO_DATA
    p = tmp_path / "tranche1_state.json"
    p.write_text('{"in_profit": true}', encoding="utf-8")
    assert ug.load_tranche1_state(p) is True
    p.write_text('{"in_profit": false}', encoding="utf-8")
    assert ug.load_tranche1_state(p) is False
    p.write_text("khong phai json", encoding="utf-8")
    assert ug.load_tranche1_state(p) is None  # JSON hong -> None, khong swallow ngam


def test_append_log_writes_jsonl_lines(tmp_path: Path):
    log = tmp_path / "sub" / "shadow.jsonl"
    result = {
        "as_of": "2026-09-29",
        "generated_at": "2026-09-29T16:00:00",
        "divergence": {"v1_passes": 3, "v2_passes": 0, "divergent": True},
        "ladder": {"tranche_pct": 0, "mode": "100% CASH DEFENSE", "action": "HOLD_CASH_EARN_INTEREST", "blocked_by": []},
        "gates": [{"gate": "G1", "pass": False}, {"gate": "G2", "pass": False}, {"gate": "G3", "pass": False}],
    }
    ug.append_log(log, result)
    ug.append_log(log, result)
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2  # append, khong ghi de
    assert rows[0]["v1_passes"] == 3 and rows[0]["divergent"] is True
    assert rows[0]["gates"] == {"G1": False, "G2": False, "G3": False}


def test_passes_summary_two_of_three():
    gates = [{"pass": True}, {"pass": False}, {"pass": True}]
    ok, n = ug.passes_summary(gates)
    assert (ok, n) == (True, 2)
    ok, n = ug.passes_summary([{"pass": True}, {"pass": False}, {"pass": False}])
    assert (ok, n) == (False, 1)


def _make_tmp_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL, volume REAL)")
    con.execute(
        "CREATE TABLE market_wide_breadth_daily ("
        "date TEXT, advances INTEGER, declines INTEGER, breadth_pct REAL, degraded INTEGER DEFAULT 0)"
    )
    con.execute("CREATE TABLE macro_history (date TEXT, variable TEXT, value REAL)")
    con.execute("CREATE TABLE regime_history (date TEXT, breadth_pct REAL, status TEXT)")
    dates = [f"2026-01-{d:02d}" for d in range(1, 26)]
    for i, d in enumerate(dates):
        con.execute("INSERT INTO daily_ohlcv VALUES ('AAA', ?, ?, ?)", (d, 100.0 + i, 1_000_000))
        con.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX', ?, ?, ?)", (d, 1000.0 + i, 50_000_000))
        con.execute("INSERT INTO market_wide_breadth_daily VALUES (?, ?, ?, ?, 0)", (d, 300, 200, 0.0))
        con.execute("INSERT INTO macro_history VALUES (?, 'DXY', ?)", (d, 100.0))
    for d in dates[:8]:
        con.execute("INSERT INTO macro_history VALUES (?, 'VGB10Y', 4.5)", (d,))
        con.execute("INSERT INTO macro_history VALUES (?, 'US10Y', 5.0)", (d,))
    for d in dates[:3]:
        con.execute("INSERT INTO regime_history VALUES (?, ?, 'RANGING')", (d, 45.0))
    con.commit()
    con.close()
    return path


def test_evaluate_end_to_end_on_tmp_db(tmp_path: Path):
    db = _make_tmp_db(tmp_path / "c.db")
    result = ug.evaluate(db, as_of="2026-01-25")
    assert result["shadow"] is True
    assert result["as_of"] == "2026-01-25"
    assert [g["gate"] for g in result["gates"]] == ["G1", "G2", "G3"]
    assert set(result["v1"]) == {"G1_entry_lock", "G3_breadth_gt40x2", "G4_confidence_ge30"}
    assert result["data_notes"]["turnover_sessions"] >= 21
    assert result["data_notes"]["dxy_obs"] == 25  # chi co 25 phien trong fixture
    assert isinstance(result["ladder"]["tranche_pct"], int)
    text = ug.format_report(result)
    assert "SHADOW" in text
    assert "DEPLOYMENT LADDER" in text
    assert "can 2/3" in text
    assert "DIVERGENCE" in text
    assert "RESET RULE" in text
    assert result["data_notes"]["macro_latest"]["us10y"] is not None


def test_main_writes_json_without_touching_db(tmp_path: Path):
    db = _make_tmp_db(tmp_path / "c.db")
    out = tmp_path / "shadow.json"
    before = db.read_bytes()
    code = ug.main(["--db", str(db), "--as-of", "2026-01-25", "--json", str(out)])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["shadow"] is True
    assert db.read_bytes() == before  # read-only
