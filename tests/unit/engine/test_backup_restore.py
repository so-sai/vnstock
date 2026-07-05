"""Stress-test: corrupt files → backup → restore → verify integrity."""
import json, shutil, tempfile
from pathlib import Path
import pytest


@pytest.fixture
def sandbox():
    """Tạo alert_dir + backup_dir + probe_cache với 2 file thật."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        alert_dir = root / "alerts"
        backup_dir = alert_dir / "backup"
        probe_dir = root / "probe_cache"
        alert_dir.mkdir(parents=True)
        backup_dir.mkdir(parents=True)
        probe_dir.mkdir(parents=True)

        # Alert gốc
        orig_alert = {"timestamp": 1000000.0, "error_type": "SBVStructureChanged"}
        (alert_dir / "sbv_structure_changed.json").write_text(
            json.dumps(orig_alert), encoding="utf-8")

        # Recall_state gốc
        orig_recall = {"last_recall": 1000000.0, "cooldown_until": 1003600.0}
        (probe_dir / "recall_state.json").write_text(
            json.dumps(orig_recall), encoding="utf-8")

        # Backup (copy từ gốc)
        shutil.copy2(alert_dir / "sbv_structure_changed.json",
                     backup_dir / "sbv_structure_changed.json")
        shutil.copy2(probe_dir / "recall_state.json",
                     backup_dir / "recall_state.json")

        yield alert_dir, backup_dir, probe_dir


def _run_restore(backup_dir: Path, alert_dir: Path, probe_dir: Path):
    """Giả lập restore-backup: copy từ backup_dir → alert_dir + probe_cache."""
    count = 0
    for f in backup_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, alert_dir / f.name)
            count += 1
    recall_bak = backup_dir / "recall_state.json"
    if recall_bak.exists():
        shutil.copy2(recall_bak, probe_dir / "recall_state.json")
        count += 1
    return count


class TestBackupRestoreStress:
    """Corrupt files → restore → verify original state recovered."""

    def test_corrupted_alert_json(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        (alert_dir / "sbv_structure_changed.json").write_text("corrupted !!", encoding="utf-8")
        _run_restore(backup_dir, alert_dir, probe_dir)
        restored = json.loads((alert_dir / "sbv_structure_changed.json").read_text(encoding="utf-8"))
        assert restored["error_type"] == "SBVStructureChanged"
        assert restored["timestamp"] == 1000000.0

    def test_deleted_alert_file(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        (alert_dir / "sbv_structure_changed.json").unlink()
        _run_restore(backup_dir, alert_dir, probe_dir)
        assert (alert_dir / "sbv_structure_changed.json").exists()
        restored = json.loads((alert_dir / "sbv_structure_changed.json").read_text(encoding="utf-8"))
        assert restored["error_type"] == "SBVStructureChanged"

    def test_corrupted_recall_state(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        recall_file = probe_dir / "recall_state.json"
        recall_file.write_text("garbage", encoding="utf-8")
        _run_restore(backup_dir, alert_dir, probe_dir)
        assert recall_file.exists()
        restored = json.loads(recall_file.read_text(encoding="utf-8"))
        assert restored["last_recall"] == 1000000.0

    def test_deleted_recall_state(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        recall_file = probe_dir / "recall_state.json"
        recall_file.unlink()
        _run_restore(backup_dir, alert_dir, probe_dir)
        assert recall_file.exists()
        restored = json.loads(recall_file.read_text(encoding="utf-8"))
        assert restored["last_recall"] == 1000000.0

    def test_both_deleted(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        (alert_dir / "sbv_structure_changed.json").unlink()
        (probe_dir / "recall_state.json").unlink()
        _run_restore(backup_dir, alert_dir, probe_dir)
        assert (alert_dir / "sbv_structure_changed.json").exists()
        assert (probe_dir / "recall_state.json").exists()

    def test_no_backup_does_not_crash(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        for f in backup_dir.iterdir():
            f.unlink()
        count = _run_restore(backup_dir, alert_dir, probe_dir)
        assert count == 0  # Không crash, 0 file restored

    def test_extra_files_in_backup(self, sandbox):
        alert_dir, backup_dir, probe_dir = sandbox
        (backup_dir / "extraneous.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
        count = _run_restore(backup_dir, alert_dir, probe_dir)
        assert (alert_dir / "extraneous.json").exists()
