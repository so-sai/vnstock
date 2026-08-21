"""test_ftse_event_ledger.py — Unit tests FTSE Event Observation Layer (READ-ONLY).

No network. Dùng tmp SQLite + fixture JSON để kiểm tra LOGIC:
  - classify_surprise: 4 lớp EXPECTED_INCLUDED / SURPRISE_INCLUDED /
    EXPECTED_EXCLUDED / NON_EVENT_CONTROL;
  - compute_event_alpha: mean(selected) - mean(controls);
  - evaluate_gates: FTSE_EVENT_ROTATION vs MARKET_REGIME_CHANGE_CANDIDATE vs
    INSUFFICIENT_EVIDENCE — và KHÔNG BAO GIỜ trả action mua/bán;
  - freeze_baseline: đọc artifact hệ thống, idempotent-guard (freeze 2 lần raise);
  - record_observation / record_surprise: provenance bắt buộc, append-only;
  - module READ-ONLY: không import engine/governor/decision pipeline.
"""

import inspect
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.research.ftse_event_ledger as ftse
from src.research.ftse_event_ledger import (
    GATE_INSUFFICIENT,
    GATE_REGIME_CHANGE,
    GATE_ROTATION,
    NON_EVENT_CONTROL,
    SURPRISE_EXPECTED_EXCLUDED,
    SURPRISE_EXPECTED_INCLUDED,
    SURPRISE_INCLUDED,
    BaselineFrozenError,
    ProvenanceError,
    classify_surprise,
    compute_event_alpha,
    evaluate_gates,
    freeze_baseline,
    load_baseline,
    record_observation,
    record_surprise,
)


@pytest.fixture(autouse=True)
def _tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(ftse, "LEDGER_DB", tmp_path / "ftse_event_ledger.db")
    return tmp_path


def _mk_output_dir(tmp_path: Path) -> Path:
    """Fixture artifact hệ thống mô phỏng data/output EOD 2026-08-20."""
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "final_decision.json").write_text(
        json.dumps(
            {
                "ngay": "2026-08-20",
                "quyet_dinh": "QUAN SAT",
                "quyet_dinh_raw": "DUNG NGOAI",
                "chi_tiet": {
                    "cau_truc": "VO CAU TRUC",
                    "regime": "RANGING",
                    "so_tru_cau_truc": 0,
                    "entropy": 2.285,
                    "adx": 23.4,
                },
                "d?_tin_c?y_sau_hi?u_ch?nh": {"di?m_s?": 0.28, "m?c": "THAP"},
                "he_so_giam_ty_trong": 0.0,
                "delta_divergence": {
                    "delta_sa": 0.273,
                    "healing_illusion": True,
                    "action_filter": "caution",
                },
                "recovery_status": "PILOT_ABORT",
                "params_hash": "9dd929b52363f4f0",
            },
        ),
        encoding="utf-8",
    )
    (out / "structural_state.json").write_text(
        json.dumps(
            {
                "ngay": "2026-08-20",
                "trang_thai": "VO CAU TRUC",
                "so_tru_ok": 1,
                "tong_so_tru": 3,
                "entropy": 2.285,
            },
        ),
        encoding="utf-8",
    )
    (out / "snapshot_index.json").write_text(
        json.dumps(
            [
                {
                    "ngay": "2026-08-19",
                    "regime": "CRISIS",
                    "diem_so": 0.34,
                    "delta_sa": 0.31,
                    "action_filter": "caution",
                    "params_hash": "aaa",
                },
                {
                    "ngay": "2026-08-20",
                    "regime": "RANGING",
                    "diem_so": 0.36,
                    "delta_sa": 0.273,
                    "action_filter": "caution",
                    "params_hash": "9dd929b52363f4f0",
                },
            ],
        ),
        encoding="utf-8",
    )
    return out


# ── Layer A: Surprise classification ────────────────────────────────────────


def test_classify_surprise_four_classes():
    expected = ["AAA", "BBB", "CCC"]
    actual = ["BBB", "CCC", "DDD"]
    controls = ["ZZZ"]
    res = classify_surprise(expected, actual, controls=controls)
    assert res["BBB"] == SURPRISE_EXPECTED_INCLUDED
    assert res["CCC"] == SURPRISE_EXPECTED_INCLUDED
    assert res["DDD"] == SURPRISE_INCLUDED
    assert res["AAA"] == SURPRISE_EXPECTED_EXCLUDED
    assert res["ZZZ"] == NON_EVENT_CONTROL


def test_classify_surprise_normalizes_tickers():
    res = classify_surprise(["aaa"], ["AAA"])
    assert res["AAA"] == SURPRISE_EXPECTED_INCLUDED


def test_classify_surprise_empty_inputs_raise():
    with pytest.raises(ValueError):
        classify_surprise([], [])
    with pytest.raises(ValueError):
        classify_surprise(["AAA"], [])


# ── Event alpha ──────────────────────────────────────────────────────────────


def test_compute_event_alpha():
    sel = [0.02, 0.03, 0.04]
    ctl = [0.00, -0.01, 0.01]
    assert abs(compute_event_alpha(sel, ctl) - 0.03) < 1e-12


def test_compute_event_alpha_empty_raise():
    with pytest.raises(ValueError):
        compute_event_alpha([], [0.0])


# ── Gates (mô tả research — KHÔNG phải decision action) ─────────────────────


def _metrics(**over):
    base = {
        "selected_return_pct": 2.0,
        "control_return_pct": 0.1,
        "breadth_pct": 40.0,
        "breadth_baseline_pct": 39.8,
        "liquidity_change_pct": -1.0,
        "structure_pillars": 0,
        "ma_reclaim": False,
        "recovery_gates_pass": False,
    }
    base.update(over)
    return base


def test_gate_rotation_when_selected_up_breadth_weak():
    res = evaluate_gates(_metrics())
    assert res["label"] == GATE_ROTATION
    assert res["label"] in {GATE_ROTATION, GATE_REGIME_CHANGE, GATE_INSUFFICIENT}


def test_gate_regime_change_requires_full_transmission():
    res = evaluate_gates(
        _metrics(
            breadth_pct=46.0,
            liquidity_change_pct=+3.0,
            structure_pillars=2,
            ma_reclaim=True,
            recovery_gates_pass=True,
        )
    )
    assert res["label"] == GATE_REGIME_CHANGE


def test_gate_insufficient_on_mixed_evidence():
    # selected tăng nhưng breadth mở trong khi liquidity vẫn co — mixed → insufficient
    res = evaluate_gates(
        _metrics(breadth_pct=47.0, liquidity_change_pct=-2.0, structure_pillars=0)
    )
    assert res["label"] == GATE_INSUFFICIENT


def test_gate_never_returns_trade_action():
    for kw in [{}, {"recovery_gates_pass": True}, {"selected_return_pct": -1.0}]:
        res = evaluate_gates(_metrics(**kw))
        assert res["label"] in {GATE_ROTATION, GATE_REGIME_CHANGE, GATE_INSUFFICIENT}
        low = res["label"].lower()
        assert "buy" not in low and "mua" not in low and "sell" not in low


def test_gate_missing_metric_raises():
    m = _metrics()
    del m["breadth_pct"]
    with pytest.raises(ProvenanceError):
        evaluate_gates(m)


# ── Freeze baseline ──────────────────────────────────────────────────────────


def test_freeze_baseline_reads_system_outputs(tmp_path):
    out = _mk_output_dir(tmp_path)
    payload = freeze_baseline(output_dir=out)
    assert payload["event_date"] == "2026-08-21"
    assert payload["pre_event_date"] == "2026-08-20"
    fd = payload["final_decision"]
    assert fd["quyet_dinh"] == "QUAN SAT"
    assert fd["he_so_giam_ty_trong"] == 0.0
    assert fd["delta_divergence"]["healing_illusion"] is True
    assert payload["snapshot_last_entry"]["ngay"] == "2026-08-20"
    assert payload["structural_state"]["tong_so_tru"] == 3
    # baseline đã ghi DB
    loaded = load_baseline()
    assert loaded is not None
    assert loaded["final_decision"]["params_hash"] == "9dd929b52363f4f0"


def test_freeze_baseline_idempotent_guard(tmp_path):
    out = _mk_output_dir(tmp_path)
    freeze_baseline(output_dir=out)
    with pytest.raises(BaselineFrozenError):
        freeze_baseline(output_dir=out)


def test_freeze_baseline_missing_artifact_raises(tmp_path):
    out = tmp_path / "empty_output"
    out.mkdir()
    with pytest.raises(FileNotFoundError):
        freeze_baseline(output_dir=out)


def test_news_inputs_require_source(tmp_path):
    out = _mk_output_dir(tmp_path)
    news = [{"foreign_flow_b": -617.0}]  # thiếu 'source'
    with pytest.raises(ProvenanceError):
        freeze_baseline(output_dir=out, news_inputs=news)
    news_ok = [
        {
            "foreign_flow_b": -617.0,
            "proprietary_flow_b": 508.0,
            "source": "USER_NEWS_2026-08-20_unverified",
        }
    ]
    payload = freeze_baseline(output_dir=out, news_inputs=news_ok)
    assert payload["news_inputs"][0]["source"].startswith("USER_NEWS")


# ── Observations: provenance bắt buộc + append-only ─────────────────────────


def test_record_observation_requires_provenance():
    with pytest.raises(ProvenanceError):
        record_observation(window="D+1", subject="VNINDEX", metrics={"return_pct": 1.0}, source="")


def test_record_observation_append_only_windows():
    record_observation(
        window="D+1", subject="VNINDEX", metrics={"return_pct": 1.0},
        source="system_db_daily_ohlcv",
    )
    record_observation(
        window="D+5", subject="VNINDEX", metrics={"return_pct": 2.0},
        source="system_db_daily_ohlcv",
    )
    rows = ftse.load_observations()
    windows = sorted(r["window"] for r in rows if r["subject"] == "VNINDEX")
    assert windows == ["D+1", "D+5"]


def test_record_observation_duplicate_window_ignored():
    ok1 = record_observation(
        window="D+1", subject="VNINDEX", metrics={"return_pct": 1.0},
        source="system_db_daily_ohlcv",
    )
    ok2 = record_observation(
        window="D+1", subject="VNINDEX", metrics={"return_pct": 9.9},
        source="system_db_daily_ohlcv",
    )
    assert ok1 is True and ok2 is False
    rows = [r for r in ftse.load_observations() if r["subject"] == "VNINDEX"]
    assert len(rows) == 1
    assert rows[0]["metrics"]["return_pct"] == 1.0  # bản ghi đầu được giữ nguyên


def test_record_observation_window_case_insensitive_key():
    """'d+1' và 'D+1' phải va chạm cùng key — không tạo row thứ hai."""
    ok1 = record_observation(
        window="D+5", subject="AAA", metrics={"return_pct": 2.0},
        source="system_db_daily_ohlcv",
    )
    ok2 = record_observation(
        window="d+5", subject="aaa", metrics={"return_pct": -9.9},
        source="system_db_daily_ohlcv",
    )
    assert ok1 is True and ok2 is False
    rows = [r for r in ftse.load_observations() if r["subject"] == "AAA"]
    assert len(rows) == 1
    assert rows[0]["window"] == "D+5"
    assert rows[0]["metrics"]["return_pct"] == 2.0


def test_record_surprise_persists_classification():
    n = record_surprise(
        expected=["AAA", "BBB"],
        actual=["BBB", "CCC"],
        controls=["ZZZ"],
        expected_source="NEWS_APRIL_2026_unverified",
        actual_source="FTSE_GEIS_2026-08-21_official",
    )
    assert n == 4
    rows = ftse.load_surprises()
    by_sym = {r["symbol"]: r["class_label"] for r in rows}
    assert by_sym["AAA"] == SURPRISE_EXPECTED_EXCLUDED
    assert by_sym["CCC"] == SURPRISE_INCLUDED
    assert by_sym["ZZZ"] == NON_EVENT_CONTROL


# ── READ-ONLY guarantee đối với decision pipeline ───────────────────────────


def test_module_is_readonly_no_decision_imports():
    src = inspect.getsource(ftse)
    import_lines = [
        ln for ln in src.splitlines() if re.match(r"^\s*(import|from)\s+\S", ln)
    ]
    forbidden = ("src.engine", "src.governor", "decision_guard", "orchestrator")
    offenders = [
        ln for ln in import_lines for tok in forbidden if tok in ln
    ]
    assert offenders == [], f"module research KHÔNG được import decision pipeline: {offenders}"


def test_schema_tables_exist():
    ftse._ensure_schema()
    conn = sqlite3.connect(str(ftse.LEDGER_DB))
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {
        "event_baseline",
        "surprise_classification",
        "observations",
        "gate_evaluations",
    } <= names
