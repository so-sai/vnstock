"""Test backend/scripts/audit_veto_returns.py (Muc 1 — Audit VETO/REDUCE, 2026-09-29).

Zero-network: toan bo dung tmp fixture (JSON + SQLite nho), khong doc DB that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_veto_returns as av


def _write_csi(path: Path, records: list[dict]) -> Path:
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def _make_db(path: Path, rows: list[tuple[str, str, float]]) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL)")
    con.executemany("INSERT INTO daily_ohlcv VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return path


def test_load_signals_filters_actions_and_dedupes(tmp_path: Path):
    csi = _write_csi(
        tmp_path / "csi.json",
        [
            {"symbol": "HPG", "date": "2026-07-31", "csi": {"action": "VETO", "p_gain": 0.3}},
            {"symbol": "HPG", "date": "2026-07-31", "csi": {"action": "VETO", "p_gain": 0.9}},
            {"symbol": "ACB", "date": "2026-08-03", "csi": {"action": "REDUCE", "p_gain": 0.4}},
            {"symbol": "VCB", "date": "2026-08-03", "csi": {"action": "HOLD", "p_gain": 0.8}},
        ],
    )
    signals = av.load_signals(csi)
    assert [s["symbol"] for s in signals] == ["HPG", "ACB"]
    assert signals[0]["p_gain"] == 0.3  # giu lan dau, bo lan lap
    assert {s["action"] for s in signals} == {"VETO", "REDUCE"}


def test_breach_dates_flags_beyond_band_only():
    series = (
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"],
        [100.0, 109.0, 101.0, 93.0],  # +9.00%, -7.34%, -7.92%
    )
    assert av.breach_dates(series, band_pct=8.0) == ["2026-01-06"]
    assert av.breach_dates(series, band_pct=7.0) == ["2026-01-06", "2026-01-07", "2026-01-08"]
    assert av.breach_dates(None, band_pct=8.0) == []


def test_row_forward_no_data_when_horizon_exceeds_calendar():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07"]
    closes = (calendar, [100.0, 101.0, 102.0])
    out = av.row_forward(calendar, closes, closes, "2026-01-05", [2, 5], breaches=[], entry_offset=0)
    assert out["2"] is not None
    assert out["5"] is None  # khong du phien phia truoc -> NO_DATA, khong uoc luong


def test_row_forward_defaults_to_next_session_no_lookahead():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    closes = (calendar, [100.0, 101.0, 102.0, 103.0])
    # signal 2026-01-05 -> entry PHAI sau phien do (T0+1), khong mua duoc tai close T0
    out = av.row_forward(calendar, closes, closes, "2026-01-05", [2], breaches=[])["2"]
    assert out is not None
    assert out["entry_date"] == "2026-01-06"
    assert out["target_date"] == "2026-01-08"  # entry + 2 phien
    assert round(out["ret"], 6) == round((103.0 / 101.0 - 1) * 100, 6)


def test_row_forward_entry_offset_zero_keeps_signal_close():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07"]
    closes = (calendar, [100.0, 101.0, 102.0])
    out = av.row_forward(calendar, closes, closes, "2026-01-05", [2], breaches=[], entry_offset=0)["2"]
    assert out is not None
    assert out["entry_date"] == "2026-01-05"  # legacy: chi dung de doi chieu
    assert out["target_date"] == "2026-01-07"


def test_row_forward_uses_same_window_for_symbol_and_benchmark():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    symbol = (calendar, [100.0, 100.0, 100.0, 90.0])
    bench = (calendar, [100.0, 100.0, 100.0, 110.0])
    out = av.row_forward(calendar, symbol, bench, "2026-01-05", [3], breaches=[], entry_offset=0)["3"]
    assert out is not None
    assert out["entry_date"] == "2026-01-05"
    assert out["target_date"] == "2026-01-08"
    assert round(out["ret"], 6) == -10.0
    assert round(out["vnindex_ret"], 6) == 10.0
    assert out["anomaly"] == []


def test_row_forward_marks_anomaly_window():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    symbol = (calendar, [100.0, 100.0, 50.0, 51.0])  # phien bat thuong o 2026-01-07
    bench = (calendar, [100.0, 100.0, 100.0, 101.0])
    out = av.row_forward(calendar, symbol, bench, "2026-01-05", [3], breaches=["2026-01-07"], entry_offset=0)["3"]
    assert out is not None
    assert out["anomaly"] == ["2026-01-07"]


def test_classify_matrix_quadrants():
    assert av.classify(None, None).startswith("NO_DATA")
    assert av.classify(-1.0, -2.0).startswith("BAO VE VON co dieu kien")
    assert av.classify(-1.0, 2.0).startswith("BAO VE VON (")
    assert av.classify(3.0, -1.0).startswith("CHI PHI CO HOI")
    assert av.classify(3.0, 1.0).startswith("TAO GIA TRI")


def test_summarize_splits_clean_subset():
    rows = [
        {"action": "VETO", "20D": -5.0, "VNINDEX_20D": 1.0, "20D_excess": -6.0, "20D_anomaly": ["2026-01-07"]},
        {"action": "VETO", "20D": -1.0, "VNINDEX_20D": 1.0, "20D_excess": -2.0, "20D_anomaly": []},
        {"action": "REDUCE", "20D": 2.0, "VNINDEX_20D": 1.0, "20D_excess": 1.0, "20D_anomaly": []},
    ]
    summary = av.summarize(rows, [20])["20"]
    veto = summary["VETO"]
    assert veto["cover"] == 2
    assert veto["cover_clean"] == 1
    assert veto["anomaly_rows"] == 1
    assert round(veto["mean_ret"], 6) == -3.0
    assert round(veto["mean_ret_clean"], 6) == -1.0
    assert summary["ALL"]["anomaly_rows"] == 1
    assert summary["REDUCE"]["mean_excess_clean"] == 1.0


def test_audit_end_to_end_on_tmp_db(tmp_path: Path):
    db = _make_db(
        tmp_path / "c.db",
        [
            ("AAA", "2026-01-05", 100.0),
            ("AAA", "2026-01-06", 92.0),  # -8% -> bat thuong
            ("AAA", "2026-01-07", 93.0),
            ("AAA", "2026-01-08", 94.0),
            ("VNINDEX", "2026-01-05", 100.0),
            ("VNINDEX", "2026-01-06", 100.0),
            ("VNINDEX", "2026-01-07", 101.0),
            ("VNINDEX", "2026-01-08", 102.0),
        ],
    )
    csi = _write_csi(
        tmp_path / "csi.json",
        [{"symbol": "AAA", "date": "2026-01-05", "csi": {"action": "VETO", "p_gain": 0.2}}],
    )
    result = av.audit(av.load_signals(csi), db, [2, 3], band_pct=7.5, entry_offset=0)
    assert result["n_signals"] == 1
    row = result["rows"][0]
    assert row["3D"] is not None
    assert row["3D_window"] == "2026-01-05->2026-01-08"
    assert row["3D_anomaly"] == ["2026-01-06"]
    assert result["summary"]["3"]["ALL"]["cover_clean"] == 0
    assert "AAA" in result["breaches"]
    text = av.report(result, [2, 3])
    assert "AUDIT VETO/REDUCE" in text
    assert "DU LIEU BAT THUONG" in text


def test_audit_default_entry_shifts_window_to_next_session(tmp_path: Path):
    db = _make_db(
        tmp_path / "c.db",
        [
            ("AAA", "2026-01-05", 100.0),
            ("AAA", "2026-01-06", 101.0),
            ("AAA", "2026-01-07", 102.0),
            ("AAA", "2026-01-08", 103.0),
            ("VNINDEX", "2026-01-05", 100.0),
            ("VNINDEX", "2026-01-06", 101.0),
            ("VNINDEX", "2026-01-07", 102.0),
            ("VNINDEX", "2026-01-08", 103.0),
        ],
    )
    csi = _write_csi(
        tmp_path / "csi.json",
        [{"symbol": "AAA", "date": "2026-01-05", "csi": {"action": "VETO", "p_gain": 0.2}}],
    )
    result = av.audit(av.load_signals(csi), db, [1])  # entry_offset mac dinh = 1 (T0+1)
    row = result["rows"][0]
    assert row["1D_window"] == "2026-01-06->2026-01-07"  # vao sau phien signal
    assert result["entry_offset"] == 1
    assert "T0+1" in result["entry_mode"]


def test_audit_collects_attribution(tmp_path: Path):
    db = _make_db(
        tmp_path / "c.db",
        [
            ("AAA", "2026-01-05", 100.0),
            ("AAA", "2026-01-06", 100.0),
            ("VNINDEX", "2026-01-05", 100.0),
            ("VNINDEX", "2026-01-06", 100.0),
        ],
    )
    csi = _write_csi(
        tmp_path / "csi.json",
        [
            {
                "symbol": "AAA",
                "date": "2026-01-05",
                "csi": {"action": "VETO", "p_gain": 0.2},
                "attribution": {"primary": "policy_rate", "message": "CSI bi kim boi LAI SUAT"},
            },
            {"symbol": "AAA", "date": "2026-01-06", "csi": {"action": "REDUCE", "p_gain": 0.3}},
        ],
    )
    result = av.audit(av.load_signals(csi), db, [1])
    assert result["attribution"]["policy_rate"]["VETO"] == 1
    assert result["attribution"]["policy_rate"]["total"] == 1
    assert result["attribution"]["KHONG_RO"]["REDUCE"] == 1
    text = av.report(result, [1])
    assert "ATTRIBUTION" in text
    assert "policy_rate" in text


def test_main_reports_without_writing_db(tmp_path: Path):
    db = _make_db(
        tmp_path / "c.db",
        [
            ("BBB", "2026-01-05", 100.0),
            ("BBB", "2026-01-06", 100.0),
            ("VNINDEX", "2026-01-05", 100.0),
            ("VNINDEX", "2026-01-06", 100.0),
        ],
    )
    csi = _write_csi(
        tmp_path / "csi.json",
        [{"symbol": "BBB", "date": "2026-01-05", "csi": {"action": "REDUCE", "p_gain": 0.5}}],
    )
    before = db.read_bytes()
    code = av.main(["--csi", str(csi), "--db", str(db), "--horizons", "1"])
    assert code == 0
    assert db.read_bytes() == before  # read-only: DB khong doi
