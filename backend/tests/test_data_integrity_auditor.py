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


# ── Crawl-time date-format-drift guard (fix 06/08/2026) ─────────────────────
# WHY: 59,219 dòng daily_ohlcv từng bị lưu date 'YYYY-MM-DD 07:00:00' (nguồn kbs) vì
# `save_data_upsert` chạy .astype(str) trên cột pandas datetime64[ns]. Bất kỳ crawler mới
# nào tương lai cũng đi qua save_data_upsert — nếu guard phá, lỗi datetime tái sinh ngay.
def _make_ohlcv_tmp_db(tmp_path):
    db = tmp_path / "ohlcv_drift.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, adj_close REAL,
            volume INTEGER CHECK(volume >= 0),
            source TEXT,
            PRIMARY KEY (symbol, date)
        );
    """)
    conn.commit()
    return db, conn


def test_save_data_upsert_normalizes_datetime_dates(tmp_path):
    """Crawler mới đẩy cột datetime64[ns] → save_data_upsert phải lưu 'YYYY-MM-DD'."""
    import pandas as pd

    from src.database.db_core import save_data_upsert

    _, conn = _make_ohlcv_tmp_db(tmp_path)
    df = pd.DataFrame(
        {
            "symbol": ["__CRAWLER1__", "__CRAWLER1__"],
            "date": pd.to_datetime(["2026-04-08 07:00:00", "2026-04-09 07:00:00"]),
            "open": [10000.0, 10100.0],
            "high": [10200.0, 10300.0],
            "low": [9900.0, 10000.0],
            "close": [10100.0, 10200.0],
            "adj_close": [10100.0, 10200.0],
            "volume": [1_000_000, 1_100_000],
            "source": ["NEWCRAWLER", "NEWCRAWLER"],
        }
    )
    save_data_upsert("daily_ohlcv", df, conn)
    dates = {r[0] for r in conn.execute("SELECT date FROM daily_ohlcv WHERE symbol='__CRAWLER1__'").fetchall()}
    assert dates == {"2026-04-08", "2026-04-09"}


def test_crawl_with_datetime_cannot_create_duplicate_keys(tmp_path):
    """Crawl lặp với datetime không được tạo nhóm (symbol,date) trùng (PRIMARY KEY)."""
    import pandas as pd

    from src.database.db_core import save_data_upsert

    _, conn = _make_ohlcv_tmp_db(tmp_path)
    for _ in range(2):  # crawl 2 lần
        df = pd.DataFrame(
            {
                "symbol": ["__CRAWLER2__"],
                "date": pd.to_datetime(["2026-04-08 07:00:00"]),
                "open": [10000.0],
                "high": [10200.0],
                "low": [9900.0],
                "close": [10100.0],
                "adj_close": [10100.0],
                "volume": [1_000_000],
                "source": ["NEWCRAWLER"],
            }
        )
        save_data_upsert("daily_ohlcv", df, conn)
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT symbol, date FROM daily_ohlcv GROUP BY symbol, date HAVING COUNT(*)>1)"
    ).fetchone()[0]
    assert dup == 0


def test_detect_datetime_rows_flags_drift(tmp_path):
    """Guard quét phải bắt được dòng date lệch định dạng (drift từ crawler tương lai)."""
    from src.database.data_integrity import detect_datetime_rows

    _, conn = _make_ohlcv_tmp_db(tmp_path)
    conn.execute(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
        ("__CRAWLER3__", "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
    )
    conn.commit()
    res = detect_datetime_rows(conn)
    assert res["count"] == 1
    conn.close()


def test_sanitize_fixes_drift_preserving_gapfill(tmp_path):
    """Sanitize: xóa datetime trùng plain, cắt giờ giữ gapfill — không rescale."""
    from src.database.data_integrity import sanitize_datetime_rows

    _, conn = _make_ohlcv_tmp_db(tmp_path)
    # dup datetime (cùng bản plain tồn tại)
    conn.execute(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
        ("__CRAWLER4__", "2026-04-08", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
    )
    conn.execute(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
        ("__CRAWLER4__", "2026-04-08 07:00:00", 10000.0, 10200.0, 9900.0, 10100.0, 10100.0, 1_000_000, "kbs"),
    )
    # gapfill độc bản
    conn.execute(
        "INSERT INTO daily_ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
        ("__CRAWLER4__", "2026-04-13 07:00:00", 3200.0, 3400.0, 3100.0, 3300.0, 3300.0, 50_000, "kbs"),
    )
    conn.commit()

    res = sanitize_datetime_rows(conn, dry_run=False)
    assert res["removed_duplicates"] == 1
    assert res["preserved_gapfills"] == 1
    assert res["remaining_datetime_rows"] == 0
    close = conn.execute("SELECT close FROM daily_ohlcv WHERE symbol='__CRAWLER4__' AND date='2026-04-13'").fetchone()[0]
    assert close == 3300.0  # không rescale
    conn.close()
