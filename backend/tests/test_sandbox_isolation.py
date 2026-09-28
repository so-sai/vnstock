"""E10 (2026-09-28) — Test Sandbox guard: toàn bộ suite không ghi vào backend/data thật.

Bằng chứng (full suite trước fix): 16 file backend/data đổi nội dung, 2 thêm, 2 xóa.
Fixture `isolate_data_artifacts` trong conftest.py copy snapshot 1 lần/session vào
tmp và redirect mọi điểm ghi; các test dưới đây khoá rằng điểm ghi KHÔNG còn
trỏ vào backend/data.
"""
import importlib
import pathlib

from src.config import DATA_DIR
from src.database.db_core import DB_PATH as _DB_PATH_AT_IMPORT

DATA = pathlib.Path(DATA_DIR)

_MODULES = {
    "prediction_log": "src.calibration.prediction_log",
    "evidence_ledger": "src.calibration.evidence_ledger",
    "replay": "src.backtest.replay_ledger_2022",
    "orchestrator": "src.orchestration.daily_cycle_orchestrator",
    "market_snapshot": "src.core.market_snapshot",
    "psr_audit": "src.core.psr.audit",
    "psr_snapshot": "src.core.psr.snapshot",
    "shadow_storage": "src.shadow_cao.storage",
    "db_core": "src.database.db_core",
    "structure_evolution": "src.engine.structure_evolution",
    "stale_tracker": "src.engine.macro_stale_tracker",
}


def _mod(key: str):
    return importlib.import_module(_MODULES[key])


def _alias_mod(key: str):
    return importlib.import_module(_MODULES[key].split(".", 1)[-1])


def _not_prod(path) -> bool:
    resolved = pathlib.Path(str(path)).resolve()
    return DATA not in resolved.parents and resolved != DATA


class TestWritePointsAreShadowed:
    def test_calibration_db_all_aliases(self):
        calib = pathlib.Path(_mod("prediction_log").CALIB_DB)
        assert _not_prod(calib)
        assert calib.name == "calibration.db"
        for key in ("evidence_ledger", "replay"):
            other = pathlib.Path(getattr(_mod(key), "CALIB_DB" if key == "evidence_ledger" else "LEDGER_DB"))
            assert other == calib, key

    def test_calibration_db_alias_modules_see_same_shadow(self):
        prod = DATA / "calibration.db"
        for key in ("prediction_log", "evidence_ledger"):
            for mod in (_mod(key), _alias_mod(key)):
                assert pathlib.Path(mod.CALIB_DB) != prod
                assert pathlib.Path(mod.CALIB_DB) == pathlib.Path(_mod("prediction_log").CALIB_DB)

    def test_cycle_db_path_points_to_shadow(self):
        path = pathlib.Path(_mod("orchestrator")._cycle_db_path())
        assert _not_prod(path)
        assert path.exists() or not (DATA / "calibration.db").exists()

    def test_macro_warmup_state_is_shadowed(self):
        tracker = _mod("stale_tracker")
        prod = DATA / "macro_warmup_state.json"
        for mod in (tracker, _alias_mod("stale_tracker")):
            cls = mod.StaleTracker
            t = cls(db_path=str(DATA / "screener_cache.db"))
            warmup = pathlib.Path(t._warmup_path())
            assert _not_prod(warmup)
            assert warmup != prod
            assert warmup.name == "macro_warmup_state.json"

    def test_snapshot_output_dir_is_shadowed(self):
        for mod in (_mod("market_snapshot"), _alias_mod("market_snapshot")):
            out = pathlib.Path(mod.SNAPSHOT_OUTPUT_DIR)
            assert _not_prod(out)
            assert out.name == "output"

    def test_psr_paths_are_shadowed(self):
        audit = _mod("psr_audit")
        snap = _mod("psr_snapshot")
        for p in (audit.AUDIT_DIR, audit.AUDIT_FILE, snap.PSR_DIR, snap.DATA_DIR):
            assert _not_prod(p), p
        assert audit.AUDIT_FILE.name == "audit.jsonl"

    def test_shadow_cao_db_is_shadowed(self):
        for mod in (_mod("shadow_storage"), _alias_mod("shadow_storage")):
            assert _not_prod(mod.SHADOW_DB_PATH)
            assert pathlib.Path(mod.SHADOW_DB_PATH).name == "shadow_cao.db"

    def test_structure_evolution_files_are_shadowed(self):
        for mod in (_mod("structure_evolution"), _alias_mod("structure_evolution")):
            for attr in ("W1_LIBRARY_FILE", "REGIME_LIBRARY_FILE", "STATE_DIR"):
                assert _not_prod(getattr(mod, attr)), attr


class TestShadowCopiesSeedRealData:
    """Test phải đọc được dữ liệu như thật (copy 1 lần/session), không phải rỗng."""

    def test_calibration_db_copy_exists(self):
        if (DATA / "calibration.db").exists():
            assert pathlib.Path(_mod("prediction_log").CALIB_DB).exists()

    def test_w1_library_copy_exists(self):
        mod = _mod("structure_evolution")
        if (DATA / "probe_cache" / "sel_w1_history.json").exists():
            assert pathlib.Path(mod.W1_LIBRARY_FILE).exists()

    def test_psr_audit_copy_exists(self):
        mod = _mod("psr_audit")
        if (DATA / "psr" / "audit.jsonl").exists():
            assert pathlib.Path(mod.AUDIT_FILE).exists()


class TestWriterWritesToShadow:
    """Ghi thật 1 lần → file production không đổi nội dung."""

    def test_stale_tracker_save_warmup_leaves_production_untouched(self):
        prod = DATA / "macro_warmup_state.json"
        before = prod.read_bytes() if prod.exists() else None

        mod = _mod("stale_tracker")
        tracker = mod.StaleTracker(db_path=str(DATA / "screener_cache.db"))
        tracker._warmup["__TEST_SANDBOX__"] = 1
        tracker._save_warmup()

        after = prod.read_bytes() if prod.exists() else None
        assert after == before
        shadow = pathlib.Path(tracker._warmup_path())
        assert shadow.exists() and _not_prod(shadow)


class TestConfigureTimePatchPrecedesImport:
    """pytest_configure phải chạy TRƯỚC khi test module import (value-copy an toàn)."""

    def test_db_path_value_imported_by_this_module_is_shadow(self):
        assert pathlib.Path(_DB_PATH_AT_IMPORT).name == "screener_cache.db"
        assert _not_prod(_DB_PATH_AT_IMPORT)

    def test_screener_shadow_copy_exists(self):
        assert pathlib.Path(_mod("db_core").DB_PATH) != DATA / "screener_cache.db"
        if (DATA / "screener_cache.db").exists():
            assert pathlib.Path(_DB_PATH_AT_IMPORT).exists()


class TestOutputDirWritersShadowed:
    def test_all_output_writers_share_shadow(self):
        outs = {
            "market_snapshot": pathlib.Path(_mod("market_snapshot").SNAPSHOT_OUTPUT_DIR),
        }
        for key, mod_name in (
            ("breadth_engine", "src.engine.breadth_engine"),
            ("confidence_layer", "src.engine.confidence_layer"),
            ("index_reality_unifier", "src.engine.index_reality_unifier"),
        ):
            mod = importlib.import_module(mod_name)
            assert hasattr(mod, "OUTPUT_DIR") or hasattr(mod, "PULSE_OUTPUT_DIR")
            attr = "PULSE_OUTPUT_DIR" if key == "breadth_engine" else "OUTPUT_DIR"
            outs[key] = pathlib.Path(getattr(mod, attr))
        for key, out in outs.items():
            assert _not_prod(out), key
            assert out == outs["market_snapshot"], key

    def test_market_pulse_copy_present(self):
        out = pathlib.Path(_mod("market_snapshot").SNAPSHOT_OUTPUT_DIR)
        if (DATA / "output" / "market_pulse.json").exists():
            assert (out / "market_pulse.json").exists()


class TestAblationAndCalibrationWritersShadowed:
    def test_reports_dirs_shadowed(self):
        for mod_name in (
            "src.backtest.walk_forward_harness",
            "src.backtest.grid_search_v2",
            "src.backtest.ablation_5model",
            "src.backtest.unified_system_replay",
        ):
            mod = importlib.import_module(mod_name)
            assert _not_prod(mod.REPORTS_DIR), mod_name

    def test_model_registry_and_causal_graph_path_shadowed(self):
        for mod_name, cls_name in (
            ("src.calibration.model_registry", "ModelRegistry"),
            ("src.calibration.causal_edge", "CausalGraph"),
        ):
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            assert _not_prod(cls._db_path()), mod_name
