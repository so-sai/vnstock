"""Tests cho Phase 4.2: StalePositionManager + Break-Glass + System State Lock."""
import json
from pathlib import Path
import pytest
from src.portfolio.stale_manager import StalePositionManager, StaleLayer, EscrowEntry, _set_stale_path
from src.portfolio.break_glass import BreakGlassProtocol
from src.portfolio.system_state import set_lock_path, is_locked, lock, unlock, get_state


@pytest.fixture
def tmp_all(tmp_path):
    """Chuyển system_state + stale_positions sang temp, reset per test."""
    sp = tmp_path / "system_state.json"
    st = tmp_path / "stale_positions.json"
    set_lock_path(sp)
    _set_stale_path(st)
    yield
    set_lock_path(Path("backend/data/system_state.json"))
    _set_stale_path(Path("backend/data/stale_positions.json"))


def make_mgr(**kw):
    """Tạo StalePositionManager với temp path đã setup."""
    return StalePositionManager(**kw)


class TestSystemState:
    def test_lock_unlock(self, tmp_all):
        assert is_locked() is False
        lock(0.26)
        assert is_locked() is True
        unlock()
        assert is_locked() is False

    def test_lock_survives_reload(self, tmp_all):
        lock(0.30)
        s1 = get_state()
        assert s1["locked"] is True
        s2 = get_state()
        assert s2["locked"] is True

    def test_unlock_clears_stale_pct(self, tmp_all):
        lock(0.26)
        unlock()
        s = get_state()
        assert s["total_stale_pct"] == 0.0


class TestStalePositionManager:
    def test_ingest_stale_appends_layer(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        r = mgr.ingest_stale("v1", 0.13)
        assert r["campaign_id"] == "v1"
        assert len(mgr._layers) == 1

    def test_total_stale_pct_additive(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.13)
        assert mgr.total_stale_pct == 0.26

    def test_hard_shutdown_at_25pct(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.12)
        r = mgr.ingest_stale("v3", 0.01)
        assert r["hard_shutdown"] is True
        assert mgr.hard_shutdown is True

    def test_no_hard_shutdown_below_25pct(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.12)
        r = mgr.ingest_stale("v2", 0.12)
        assert r["hard_shutdown"] is False

    def test_writeoff_lifo_removes_newest_first(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.10)
        mgr.ingest_stale("v2", 0.10)
        mgr._layers[0].quantity = 1000
        mgr._layers[0].current_price = 50
        mgr._layers[1].quantity = 1000
        mgr._layers[1].current_price = 60
        r = mgr.writeoff_lifo()
        assert r["written"][0]["campaign_id"] == "v2"
        assert mgr.total_stale_pct == 0.10

    def test_writeoff_sends_to_escrow(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr._layers[0].quantity = 100
        mgr._layers[0].current_price = 80
        mgr._layers[0].entry_price = 100
        mgr.writeoff_lifo()
        assert mgr.escrow_balance > 0
        assert len(mgr._escrow) == 1
        assert mgr._escrow[0].source == "writeoff"

    def test_reclaim_successful(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr._layers[0].quantity = 100
        mgr._layers[0].entry_price = 90
        mgr._layers[0].current_price = 95
        r = mgr.reclaim("v1")
        assert r["success"] is True
        assert r["status"] == "RECLAIMED"

    def test_reclaim_not_found(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        r = mgr.reclaim("v99")
        assert r["success"] is False

    def test_reclaim_removes_stale_pct(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.13)
        mgr._layers[0].quantity = 100
        mgr._layers[0].current_price = 50
        mgr.reclaim("v1")
        assert mgr.total_stale_pct == 0.13

    def test_corporate_dividend_goes_to_escrow(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        r = mgr.record_corporate_action("v1", "dividend", 500_000)
        assert r["source"] == "dividend"
        assert mgr.escrow_balance == 500_000

    def test_escrow_does_not_leak_to_total_equity(self, tmp_all):
        """Escrow không ảnh hưởng total_stale_pct hay hard_shutdown."""
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.26)
        assert mgr.hard_shutdown is True
        mgr.record_corporate_action("v1", "dividend", 1_000_000)
        assert mgr.total_stale_pct == 0.26
        assert mgr.hard_shutdown is True

    def test_status_output(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        s = mgr.status()
        assert s["stale_layers"] == 1
        assert s["total_stale_pct"] == 0.13
        assert "stale_pcts" in s
        assert "escrow_balance" in s

    def test_persistence_across_reload(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr._save()
        mgr2 = make_mgr(total_capital=1_000_000)
        assert mgr2.total_stale_pct == 0.13
        assert len(mgr2._layers) == 1

    def test_lock_file_created_on_hard_shutdown(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.26)
        assert is_locked() is True

    def test_lock_released_when_stale_below_threshold(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.13)
        assert is_locked() is True
        mgr._layers[0].quantity = 100
        mgr._layers[0].current_price = 50
        mgr._layers[1].quantity = 100
        mgr._layers[1].current_price = 50
        mgr.writeoff_lifo()
        # v2 write-off → stale còn 0.13 < 0.25 → unlock
        assert is_locked() is False


class TestBreakGlass:
    def test_request_creates_ticket(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        assert "ticket_id" in r
        assert r["use_time_delay"] is False

    def test_request_time_delay(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request(use_time_delay=True)
        assert r["use_time_delay"] is True
        assert r["time_delay_minutes"] == 15

    def test_verify_with_correct_passphrase(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        v = bg.verify(r["ticket_id"], passphrase="test_key")
        assert v["success"] is True

    def test_verify_wrong_passphrase(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        v = bg.verify(r["ticket_id"], passphrase="wrong_key")
        assert v["success"] is False
        assert v["reason"] == "INVALID_PASSPHRASE"

    def test_verify_missing_passphrase(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        v = bg.verify(r["ticket_id"])
        assert v["success"] is False
        assert v["reason"] == "PASSPHRASE_REQUIRED"

    def test_cancel_ticket(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        c = bg.cancel(r["ticket_id"])
        assert c["success"] is True
        v = bg.verify(r["ticket_id"], passphrase="test_key")
        assert v["success"] is False

    def test_already_approved(self, tmp_all):
        bg = BreakGlassProtocol(passphrase="test_key")
        r = bg.request()
        bg.verify(r["ticket_id"], passphrase="test_key")
        v2 = bg.verify(r["ticket_id"], passphrase="test_key")
        assert v2["success"] is False


class TestIntegration:
    def test_full_hard_shutdown_to_unlock_lifecycle(self, tmp_all):
        mgr = make_mgr(total_capital=1_000_000)
        mgr.ingest_stale("v1", 0.13)
        mgr.ingest_stale("v2", 0.13)
        assert mgr.hard_shutdown is True
        assert is_locked() is True

        bg = BreakGlassProtocol(passphrase="emergency_2026")
        ticket = bg.request()
        assert ticket["ticket_id"]

        v = bg.verify(ticket["ticket_id"], passphrase="emergency_2026")
        assert v["success"] is True
        assert is_locked() is False

        mgr._layers[0].quantity = 1000
        mgr._layers[0].current_price = 50
        mgr._layers[1].quantity = 1000
        mgr._layers[1].current_price = 60
        wo = mgr.writeoff_lifo()
        assert wo["written"][0]["campaign_id"] == "v2"
        assert mgr.escrow_balance > 0

        assert mgr.total_stale_pct == 0.13
        assert mgr.hard_shutdown is False
