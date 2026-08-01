"""test_data_integrity_auditor.py — TDD cho DataIntegrityAuditor (Deep Data Density Scanner & Self-Healing).

Kiểm thử:
  - Quét mật độ dữ liệu 30 quý gần nhất (2019Q1 - 2026Q2) cho danh mục cổ phiếu
  - Phân loại trạng thái Audit Status (PERFECT / GAP_FOUND / SEVERE_GAP)
  - Tự động kích hoạt crawler bù nạp (Auto-Backfill / Self-Healing) khi phát hiện lỗ hổng
"""

import pytest


def test_audit_symbol_density_metrics():
    from audit.data_integrity_auditor import DataIntegrityAuditor

    auditor = DataIntegrityAuditor()
    res = auditor.audit_symbol("VCB", start_year=2019, end_year=2026)

    assert res.symbol == "VCB"
    assert res.required_quarters > 0
    assert 0.0 <= res.density_pct <= 100.0
    assert res.status in ("PERFECT", "GAP_FOUND", "SEVERE_GAP")


def test_audit_many_classifies_statuses():
    from audit.data_integrity_auditor import DataIntegrityAuditor

    auditor = DataIntegrityAuditor()
    results = auditor.audit_many(["VCB", "ACB", "FPT"], start_year=2019, end_year=2026)

    assert len(results) == 3
    assert "VCB" in results
    assert "ACB" in results
    assert "FPT" in results


def test_audit_and_heal_triggers_backfill(monkeypatch):
    from audit.data_integrity_auditor import DataIntegrityAuditor

    healed_symbols = []

    def fake_seed(symbol):
        healed_symbols.append(symbol)
        return {"status": "DONE"}

    auditor = DataIntegrityAuditor()
    # Inject fake crawler seeder
    monkeypatch.setattr(auditor, "_seed_symbol", fake_seed)

    results = auditor.audit_and_heal(["IJC"], auto_backfill=True)

    assert len(results) == 1
    assert "IJC" in healed_symbols
