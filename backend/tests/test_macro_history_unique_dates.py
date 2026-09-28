"""Tests — E6: macro_state_history.json không được trùng date (2026-09-25).

Phát hiện: _save_state() append không điều kiện theo date → file thật có
365 records nhưng chỉ 328 ngày unique (2026-09-24 ×4, 2022-01-07 ×31),
trong đó 2026-09-24 có US10Y 5.006 vs 5.114 TRANH CHẤP.

3 tầng phòng vệ:
  1. Writer dedupe tại chỗ (MacroStateClassifier._save_state) — chống tái phát.
  2. Script backend/scripts/dedupe_macro_history.py — dọn dữ liệu cũ.
  3. Test này — chặn dữ liệu tái nhiễm.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

from backend.scripts.dedupe_macro_history import dedupe

from src.core.macro.macro_state_classifier import (
    ClassifiedMacroState,
    MacroStateClassifier,
)

HISTORY_PATH = PROJECT_ROOT / "backend" / "data" / "macro" / "macro_state_history.json"


def _state(snap_date: str, posterior: float = 0.5, us10y: float = 5.0) -> ClassifiedMacroState:
    return ClassifiedMacroState(
        date=snap_date,
        macro_state="CREDIT_STRESS",
        posterior=posterior,
        entropy=0.7,
        drivers={"Risk": -0.8},
        raw_drivers={"US10Y": us10y},
        phase_label="UNCERTAIN",
        phase_confidence=0.0,
        novelty_flag=False,
        spectral_stress="UNKNOWN",
        position_scalar=1.0,
        risk_on_scalar=1.0,
        defensive_scalar=1.0,
        raw_narrative_probs={"NORMAL": 0.5},
        raw_regime_weights=[1.0],
        raw_n_regime_components=1,
    )


class TestDataInvariant:
    def test_history_file_has_unique_dates(self):
        assert HISTORY_PATH.exists(), f"thiếu {HISTORY_PATH}"
        records = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        dates = [r.get("date") for r in records]
        assert None not in dates, "có record thiếu field date"
        dup = {d for d in dates if dates.count(d) > 1}
        assert not dup, f"date trùng lặp: {sorted(dup)}"

    def test_history_within_365_cap(self):
        records = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        assert len(records) <= 365, "vi phạm keep-last-365"

    def test_latest_record_is_the_newest_date(self):
        records = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        assert records[-1]["date"] == max(r["date"] for r in records)


class TestSaveStateDedupes:
    def test_same_date_saved_twice_keeps_one(self, tmp_path):
        clf = MacroStateClassifier(persistence_dir=tmp_path)
        clf._save_state(_state("2026-09-24", posterior=0.489, us10y=5.006))
        clf._save_state(_state("2026-09-24", posterior=0.512, us10y=5.114))

        records = json.loads((tmp_path / "macro_state_history.json").read_text(encoding="utf-8"))
        assert len(records) == 1, "ghi 2 lần cùng ngày phải gộp thành 1"
        assert records[0]["raw_drivers"]["US10Y"] == 5.114, "bản flush mới nhất phải thắng"
        assert records[0]["posterior"] == 0.512

    def test_repeated_flush_does_not_grow_file(self, tmp_path):
        clf = MacroStateClassifier(persistence_dir=tmp_path)
        for i in range(10):
            clf._save_state(_state("2026-09-25", posterior=0.1 * i))
        records = json.loads((tmp_path / "macro_state_history.json").read_text(encoding="utf-8"))
        assert len(records) == 1

    def test_first_seen_position_is_preserved(self, tmp_path):
        clf = MacroStateClassifier(persistence_dir=tmp_path)
        clf._save_state(_state("2026-09-23"))
        clf._save_state(_state("2026-09-24"))
        clf._save_state(_state("2026-09-25"))
        clf._save_state(_state("2026-09-23", us10y=9.99))  # flush lại ngày cũ

        records = json.loads((tmp_path / "macro_state_history.json").read_text(encoding="utf-8"))
        assert [r["date"] for r in records] == ["2026-09-23", "2026-09-24", "2026-09-25"]
        assert records[0]["raw_drivers"]["US10Y"] == 9.99

    def test_distinct_dates_are_appended(self, tmp_path):
        clf = MacroStateClassifier(persistence_dir=tmp_path)
        clf._save_state(_state("2026-09-24"))
        clf._save_state(_state("2026-09-25"))
        records = json.loads((tmp_path / "macro_state_history.json").read_text(encoding="utf-8"))
        assert len(records) == 2


class TestDedupeScript:
    def test_keeps_last_content_at_first_position(self):
        recs = [
            {"date": "2026-09-24", "posterior": 0.489},
            {"date": "2026-09-25", "posterior": 0.51},
            {"date": "2026-09-24", "posterior": 0.512},
        ]
        kept, report = dedupe(recs)
        assert [r["date"] for r in kept] == ["2026-09-24", "2026-09-25"]
        assert kept[0]["posterior"] == 0.512, "phải lấy bản xuất hiện CUỐI"
        assert report["total_in"] == 3
        assert report["total_out"] == 2
        assert report["removed"] == 1
        assert report["unique_dates"] == 2

    def test_reports_conflicting_fields(self):
        recs = [
            {"date": "2026-09-24", "raw_drivers": {"US10Y": 5.006, "DXY": 100.23}},
            {"date": "2026-09-24", "raw_drivers": {"US10Y": 5.114, "DXY": 100.23}},
        ]
        _, report = dedupe(recs)
        assert len(report["duplicates"]) == 1
        conflicts = report["duplicates"][0]["conflicts"]
        assert "raw_drivers" in conflicts
        assert "US10Y" in conflicts["raw_drivers"]
        assert "DXY" not in conflicts["raw_drivers"], "DXY không đổi thì không phải conflict"

    def test_identical_duplicates_report_no_conflict(self):
        recs = [{"date": "2022-01-07", "posterior": 0.3246}] * 5
        kept, report = dedupe(recs)
        assert len(kept) == 1
        assert report["removed"] == 4
        assert report["duplicates"][0]["conflicts"] == {}

    def test_dedupe_is_idempotent(self):
        recs = [
            {"date": "2026-09-24", "posterior": 0.489},
            {"date": "2026-09-24", "posterior": 0.512},
            {"date": "2026-09-25", "posterior": 0.51},
        ]
        once, _ = dedupe(recs)
        twice, report2 = dedupe(once)
        assert twice == once
        assert report2["removed"] == 0

    def test_records_without_date_are_kept(self):
        recs = [{"date": "2026-09-24"}, {"oops": True}, {"date": "2026-09-24"}]
        kept, report = dedupe(recs)
        assert {"oops": True} in kept
        assert report["total_out"] == 2
