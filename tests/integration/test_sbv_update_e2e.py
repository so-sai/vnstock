"""End-to-end: corrupted fixture → sbv-update → verify self-healing."""
import json, shutil, tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

import src.services.macro.interbank_seeder as ib
import src.engine.partial_data_entropy as ent


@pytest.fixture
def e2e_sandbox():
    """Tạo sandbox + patch module globals để thao tác trên temp dir."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        alert_dir = root / "alerts"
        backup_dir = alert_dir / "backup"
        probe_dir = root / "probe_cache"
        fixture_dir = root / "fixtures" / "sbv"
        for d in (alert_dir, backup_dir, probe_dir, fixture_dir):
            d.mkdir(parents=True)

        # Alert giả
        (alert_dir / "sbv_structure_changed.json").write_text(
            json.dumps({"timestamp": 1e6, "error_type": "SBVStructureChanged",
                        "raw_html_snapshot": "<html>corrupted</html>"}),
            encoding="utf-8")

        # Recall state giả
        (probe_dir / "recall_state.json").write_text(
            json.dumps({"last_recall": 1e6, "cooldown_until": 1003600.0}),
            encoding="utf-8")

        # Fixture cũ
        (fixture_dir / "live.html").write_text(
            "<html><body><table>old</table></body></html>", encoding="utf-8")

        # Patch module-level paths
        original_alert_dir = ib.ALERT_DIR
        original_alert_file = ib.ALERT_FILE
        original_calendar = ent.CALENDAR_PATH

        ib.ALERT_DIR = alert_dir
        ib.ALERT_FILE = alert_dir / "sbv_structure_changed.json"
        ent.CALENDAR_PATH = Path("dummy")

        yield alert_dir, backup_dir, probe_dir, fixture_dir

        # Restore
        ib.ALERT_DIR = original_alert_dir
        ib.ALERT_FILE = original_alert_file
        ent.CALENDAR_PATH = original_calendar


class TestSbvUpdateE2E:
    """Giả lập sbv-update + restore-backup trên sandbox filesystem."""

    def test_successful_update_clears_alert(self, e2e_sandbox):
        alert_dir, backup_dir, probe_dir, fixture_dir = e2e_sandbox

        with patch.object(ib, "_try_sbv") as mock:
            mock.return_value = {
                "type": "SUCCESS", "data": {"ON": 5.0, "1W": 4.5},
                "http_status": 200,
                "raw_html": "<html><body><table>new</table></body></html>",
            }

            # Backup (Step 0)
            shutil.copy2(alert_dir / "sbv_structure_changed.json",
                         backup_dir / "sbv_structure_changed.json")
            shutil.copy2(probe_dir / "recall_state.json",
                         backup_dir / "recall_state.json")

            # Scrape (Step 3)
            result = ib._try_sbv(force=True)
            assert result["type"] == "SUCCESS"
            assert result["data"]["ON"] == 5.0

            # Snapshot (Step 3b)
            if result.get("raw_html"):
                (fixture_dir / "live.html").write_text(
                    result["raw_html"], encoding="utf-8")
            assert "new" in (fixture_dir / "live.html").read_text(encoding="utf-8")

            # Clear alert (Step 4)
            ib._clear_sbv_alert()
            assert not (alert_dir / "sbv_structure_changed.json").exists()

            # Backup vẫn còn
            assert (backup_dir / "sbv_structure_changed.json").exists()
            assert (backup_dir / "recall_state.json").exists()

    def test_failed_update_preserves_alert(self, e2e_sandbox):
        alert_dir, backup_dir, probe_dir, _ = e2e_sandbox

        with patch.object(ib, "_try_sbv") as mock:
            mock.return_value = {
                "type": "STRUCTURE_CHANGED", "data": {}, "http_status": 200,
            }

            shutil.copy2(alert_dir / "sbv_structure_changed.json",
                         backup_dir / "sbv_structure_changed.json")
            shutil.copy2(probe_dir / "recall_state.json",
                         backup_dir / "recall_state.json")

            result = ib._try_sbv(force=True)
            assert result["type"] != "SUCCESS"
            assert (alert_dir / "sbv_structure_changed.json").exists()
            assert (backup_dir / "sbv_structure_changed.json").exists()

    def test_restore_after_failed_update(self, e2e_sandbox):
        alert_dir, backup_dir, probe_dir, _ = e2e_sandbox
        orig_alert = (alert_dir / "sbv_structure_changed.json").read_bytes()
        orig_recall = (probe_dir / "recall_state.json").read_bytes()

        # Backup
        shutil.copy2(alert_dir / "sbv_structure_changed.json",
                     backup_dir / "sbv_structure_changed.json")
        shutil.copy2(probe_dir / "recall_state.json",
                     backup_dir / "recall_state.json")

        # Corrupt files
        (alert_dir / "sbv_structure_changed.json").unlink()

        # Restore (giả lập restore-backup)
        for f in backup_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, alert_dir / f.name)
        recall_bak = backup_dir / "recall_state.json"
        if recall_bak.exists():
            shutil.copy2(recall_bak, probe_dir / "recall_state.json")

        assert (alert_dir / "sbv_structure_changed.json").read_bytes() == orig_alert
        assert (probe_dir / "recall_state.json").read_bytes() == orig_recall

    def test_self_healing_cycle(self, e2e_sandbox):
        alert_dir, backup_dir, probe_dir, fixture_dir = e2e_sandbox

        # Phase 1: STRUCTURE_UNKNOWN active
        assert ib._is_sbv_alert_active() is True

        # Phase 2: sbv-update thành công
        with patch.object(ib, "_try_sbv") as mock:
            mock.return_value = {
                "type": "SUCCESS", "data": {"ON": 5.0},
                "http_status": 200,
                "raw_html": "<html>recovered</html>",
            }

            shutil.copy2(alert_dir / "sbv_structure_changed.json",
                         backup_dir / "sbv_structure_changed.json")
            shutil.copy2(probe_dir / "recall_state.json",
                         backup_dir / "recall_state.json")

            result = ib._try_sbv(force=True)
            assert result["type"] == "SUCCESS"

            (fixture_dir / "live.html").write_text(
                result.get("raw_html", ""), encoding="utf-8")
            ib._clear_sbv_alert()

        # Phase 3: Hồi phục hoàn toàn
        assert not (alert_dir / "sbv_structure_changed.json").exists()
        assert ib._is_sbv_alert_active() is False
        assert (fixture_dir / "live.html").exists()
        assert (backup_dir / "sbv_structure_changed.json").exists()
