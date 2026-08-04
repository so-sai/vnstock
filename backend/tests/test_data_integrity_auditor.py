"""test_data_integrity_auditor.py — TDD cho DataIntegrityAuditor (Deep Data Density Scanner & Self-Healing).

WHY dữ liệu phải là THẬT (100% provenance, không bịa):
  Mệnh lệnh của hệ thống là "ĐỨNG NGOÀI" (bảo toàn vốn thô) khi thiếu thông tin. Mỗi con
  số giải ngân (sa 100%) — GOV dựa trên dữ liệu giả sẽ là quyết định giải ngân/đứng ngoài
  trăm tỷ bằng ảo giác, gây vỡ quỹ. Do đó:
    - Dữ liệu KHÔNG có xuất xứ hợp lệ = vô giá trị (No Provenance = No Trust), dù trông
      "đầy đủ" (density 100%) hay "đúng độ lớn" (trong dải nghìn tỷ).
    - Khóa sinh dữ liệu giả, trả mảng rỗng khi nguồn thật chết để Governor ép ĐỨNG NGOÀI.
  Lỗi quay về trạng thái an toàn (bon không ra tiêu tiền) — đúng tôn chỉ của sắc lệnh.

Kiểm thử:
  - Quét mật độ dữ liệu 30 quý gần nhất (2019Q1 - 2026Q2) cho danh mục cổ phiếu
  - Phân loại trạng thái Audit Status (PERFECT / GAP_FOUND / SEVERE_GAP)
  - Tự động kích hoạt crawler bù nạp (Auto-Backfill / Self-Healing) khi phát hiện lỗ hổng
  - Provenance audit: dữ liệu bịa (is_synthetic=1) bị trừ khỏi density → không bao giờ
    được nhận nhãn PERFECT nhờ số vẽ lấp đầy quý (No Provenance = No Trust).
"""

import sqlite3


def _make_tmp_db(tmp_path):
    db = tmp_path / "audit_test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE financial_facts (
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            source TEXT DEFAULT 'vnstock',
            is_synthetic INTEGER DEFAULT 0,
            ingested_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (symbol, period, metric)
        );
    """)
    conn.commit()
    return db, conn


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


# ── Provenance audit (Zero-Hallucination guard) ─────────────────────────
def test_synthetic_facts_are_excluded_from_density(tmp_path):
    """Mọi quý chứa fact is_synthetic=1 phải bị trừ khỏi available density.

    WHY: lỗ hổng Density-vs-Provenance — data bịa lấp đầy quý rỗng → 100% → PERFECT.
    Giờ auditor phải ghi nhận quý đó là thiếu, không cho synthetic được tính là hợp lệ.
    """
    from audit.data_integrity_auditor import DataIntegrityAuditor

    db, conn = _make_tmp_db(tmp_path)
    # 30 quý chuẩn 2019Q1→2026Q2: quý chẵn = real (vci), quý lẻ = synthetic (bịa)
    all_qs = [f"{y}Q{q}" for y in range(2019, 2027) for q in range(1, 5)]
    all_qs = [q for q in all_qs if q <= "2026Q2"]
    for i, q in enumerate(all_qs):
        if i % 2 == 0:
            conn.execute(
                "INSERT INTO financial_facts(symbol, period, metric, value, source, is_synthetic) VALUES(?,?,?,?,?,?)",
                ("AAA", q, "REVENUE", 1e12, "vci", 0),
            )
        else:
            conn.execute(
                "INSERT INTO financial_facts(symbol, period, metric, value, source, is_synthetic) VALUES(?,?,?,?,?,?)",
                ("AAA", q, "REVENUE", 9.85e12, "synthetic", 1),
            )
    conn.commit()
    conn.close()

    auditor = DataIntegrityAuditor(db_path=db)
    res = auditor.audit_symbol("AAA", start_year=2019, end_year=2026)

    # Synthetic không được tính → density thấp hơn khi chỉ đếm thật
    assert res.available_quarters < res.required_quarters
    assert res.status in ("GAP_FOUND", "SEVERE_GAP")


def test_all_synthetic_quarters_flagged_missing(tmp_path):
    """Data hoàn toàn bịa → audit phải báo SEVERE_GAP, tuyệt đối không nhận PERFECT."""
    from audit.data_integrity_auditor import DataIntegrityAuditor

    db, conn = _make_tmp_db(tmp_path)
    conn.execute(
        "INSERT INTO financial_facts(symbol, period, metric, value, source, is_synthetic) VALUES(?,?,?,?,?,?)",
        ("FAKE", "2026Q2", "REVENUE", 25e12, "synthetic", 1),
    )
    conn.commit()
    conn.close()

    auditor = DataIntegrityAuditor(db_path=db)
    res = auditor.audit_symbol("FAKE", start_year=2019, end_year=2026)

    assert "2026Q2" in res.missing_quarters
    assert res.status == "SEVERE_GAP"
