"""Tests — E7: test KHÔNG được ghi macro_state_history.json PRODUCTION (P1, 2026-09-28).

Bối cảnh: 31 bản ghi trùng ngày 2022-01-07 (đã dedupe ở E6) sinh ra vì
MacroStateClassifier() bind default persistence_dir lúc import →
test_replay_ledger_2022 → company_state._load_macro_pit → classify()
ghi thẳng vào backend/data/macro THẬT, recompute record mỗi lần chạy test
(posterior flip-flop 0.324 → 0.3237 → 0.3242).

3 tầng: conftest fixture redirect (isolate_macro_state_history) +
__init__ resolve mặc định tại thời gọi + test guard này.
"""

import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.core.macro import macro_state_classifier as msc
from src.core.macro.macro_state_classifier import (
    ClassifiedMacroState,
    MacroStateClassifier,
)

PRODUCTION_DIR = PROJECT_ROOT / "backend" / "data" / "macro"
PRODUCTION_FILE = PRODUCTION_DIR / "macro_state_history.json"


def _state(snap_date: str = "2022-01-07", posterior: float = 0.5) -> ClassifiedMacroState:
    return ClassifiedMacroState(
        date=snap_date,
        macro_state="AI_BOOM",
        posterior=posterior,
        entropy=0.9,
        drivers={"Risk": -0.9},
        raw_drivers={"US10Y": 1.771},
        phase_label="UNCERTAIN",
        phase_confidence=0.0,
        novelty_flag=True,
        spectral_stress="UNKNOWN",
        position_scalar=1.0,
        risk_on_scalar=1.0,
        defensive_scalar=1.0,
        raw_narrative_probs={"AI_BOOM": 0.32},
        raw_regime_weights=[1.0],
        raw_n_regime_components=1,
    )


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class TestIsolationFixture:
    def test_module_dir_redirected_away_from_production(self):
        assert msc.MACRO_STATE_DIR != PRODUCTION_DIR, (
            "conftest isolate_macro_state_history không chạy → test sẽ ghi vào macro history PRODUCTION"
        )

    def test_classifier_default_uses_module_dir(self):
        clf = MacroStateClassifier()
        assert clf._dir == msc.MACRO_STATE_DIR
        assert clf._dir != PRODUCTION_DIR

    def test_shadow_snapshot_exists_and_is_readable(self):
        shadow = msc.MACRO_STATE_DIR / "macro_state_history.json"
        assert shadow.exists(), "shadow copy chưa được tạo"
        records = json.loads(shadow.read_text(encoding="utf-8"))
        assert len(records) == len(json.loads(PRODUCTION_FILE.read_text(encoding="utf-8"))), (
            "shadow phải copy nguyên trạng dữ liệu production"
        )

    def test_explicit_persistence_dir_still_honored(self, tmp_path):
        clf = MacroStateClassifier(persistence_dir=tmp_path)
        assert clf._dir == tmp_path


class TestProductionNeverMutated:
    def test_save_state_leaves_production_file_untouched(self):
        before = _md5(PRODUCTION_FILE)
        clf = MacroStateClassifier()  # default → shadow (nhờ fixture)
        clf._save_state(_state("2022-01-07", posterior=0.1234))
        clf._save_state(_state("2026-09-25", posterior=0.9999))
        assert _md5(PRODUCTION_FILE) == before, "test đã ghi vào backend/data/macro/macro_state_history.json THẬT"

    def test_write_only_goes_to_shadow(self):
        clf = MacroStateClassifier()
        clf._save_state(_state("2099-12-31", posterior=0.42))
        shadow = json.loads((msc.MACRO_STATE_DIR / "macro_state_history.json").read_text(encoding="utf-8"))
        dates = [r["date"] for r in shadow]
        assert "2099-12-31" in dates
        prod = json.loads(PRODUCTION_FILE.read_text(encoding="utf-8"))
        assert "2099-12-31" not in [r["date"] for r in prod]

    def test_production_file_still_has_unique_dates(self):
        dates = [r["date"] for r in json.loads(PRODUCTION_FILE.read_text(encoding="utf-8"))]
        assert len(dates) == len(set(dates)), "production macro history bị trùng date"
