"""Test backend/scripts/repull_macro_series.py (Muc 4 RE-PULL, 2026-09-28).

Zero-network: fake fetcher duoc inject vao main(fetchers=...).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import repull_macro_series as rp

SCHEMA = """
CREATE TABLE macro_history (
    date TEXT, variable TEXT, value REAL, is_stale INTEGER DEFAULT 0, source TEXT
)
"""


def _make_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(str(path))
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO macro_history (date, variable, value, is_stale, source) VALUES (?,?,?,?,?)",
        [(d, v, val, 0, src) for d, v, val, src in rows],
    )
    con.commit()
    con.close()


def _rows(path: Path) -> dict[tuple[str, str], tuple]:
    con = sqlite3.connect(str(path))
    out = {(r[1], r[0]): (r[2], r[3]) for r in con.execute("SELECT date, variable, value, source FROM macro_history")}
    con.close()
    return out


def _fetcher(data: dict[str, dict[str, float]]):
    def _fake(mapping, *args, **kwargs):
        return {k: data.get(k, {}) for k in mapping}

    return _fake


def test_dry_run_khong_doi_file(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, None)])
    before = db.read_bytes()
    rc = rp.main(["--db", str(db)], fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2600.0}})})
    assert rc == 0
    assert db.read_bytes() == before


def test_apply_ghi_de_va_audit(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    con = sqlite3.connect(str(db))
    con.execute(SCHEMA)
    con.execute(
        "INSERT INTO macro_history (date, variable, value, is_stale, source) VALUES (?,?,?,?,?)",
        ("2026-09-25", "KOSPI", 2500.0, 1, None),
    )
    con.commit()
    con.close()
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2600.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2600.0, "yahoo")
    con = sqlite3.connect(str(db))
    stale = con.execute("SELECT is_stale FROM macro_history").fetchone()[0]
    audit = con.execute("SELECT status, old_value, new_value, new_source FROM macro_history_repull_audit").fetchall()
    backup = con.execute("SELECT value, source FROM macro_history_bak_repull").fetchall()
    con.close()
    assert stale == 0
    assert audit == [(rp.STATUS_OVERWRITTEN, 2500.0, 2600.0, "yahoo")]
    assert backup == [(2500.0, None)]


def test_gia_trong_giong_chi_stamp_source(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, None)])
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2500.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2500.0, "yahoo")


def test_provider_khac_qua_nhieu_bi_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    rows = [(f"2026-09-{20 + i:02d}", "KOSPI", 100.0 + i, None) for i in range(12)]
    _make_db(db, rows)
    prov = {f"2026-09-{20 + i:02d}": 999.0 + i for i in range(12)}
    con = sqlite3.connect(str(db))
    con.executemany(
        "INSERT INTO macro_history (date, variable, value, is_stale, source) VALUES (?,?,?,0,'yahoo')",
        [(f"2026-08-{20 + i:02d}", "KOSPI", 500.0 + i) for i in range(12)],
    )
    con.commit()
    con.close()
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": prov})},
    )
    assert rc == 0
    after = _rows(db)
    assert after[("KOSPI", "2026-09-20")] == (100.0, None)
    con = sqlite3.connect(str(db))
    statuses = {r[0] for r in con.execute("SELECT status FROM macro_history_repull_audit")}
    con.close()
    assert statuses == {rp.STATUS_MISMATCH}


def test_khong_co_provider_giu_nguyen(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "INTERBANK_ON", 4.5, None)])
    rc = rp.main(["--db", str(db), "--apply"], fetchers={"yahoo": _fetcher({})})
    assert rc == 0
    assert _rows(db)[("INTERBANK_ON", "2026-09-25")] == (4.5, None)


def test_dong_co_source_gia_trung_khong_doi(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, "yahoo")])
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE macro_history_bak_e9 AS SELECT * FROM macro_history")
    con.executemany(
        "INSERT INTO macro_history_bak_e9 (date, variable, value, is_stale, source) VALUES (?,?,?,0,'yahoo')",
        [("2026-09-25", "KOSPI", 2501.0), ("2026-09-25", "KOSPI", 2499.0)],
    )
    con.commit()
    con.close()
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2500.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2500.0, "yahoo")
    con = sqlite3.connect(str(db))
    status = con.execute("SELECT status FROM macro_history_repull_audit").fetchone()[0]
    con.close()
    assert status == rp.STATUS_MATCHED


def test_provider_thieu_ngay_bao_no_provider_data(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, None)])
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-24": 2500.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2500.0, None)


def test_overlap_it_van_tin_mapping_repo(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, None), ("2026-09-24", "KOSPI", 2490.0, None)])
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2500.0, "2026-09-24": 2490.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2500.0, "yahoo")


def test_scope_bao_gom_khoa_tranh_chap_tu_bak(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, "yahoo")])
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE macro_history_bak_e9 AS SELECT * FROM macro_history")
    con.executemany(
        "INSERT INTO macro_history_bak_e9 (date, variable, value, is_stale, source) VALUES (?,?,?,0,'yahoo')",
        [("2026-09-25", "KOSPI", 2501.0), ("2026-09-25", "KOSPI", 2499.0)],
    )
    con.commit()
    con.close()
    rc = rp.main(
        ["--db", str(db), "--apply"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2505.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2505.0, "yahoo")


def test_idempotent_lan_hai_khong_ghi_nua(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2500.0, None)])
    fetchers = {"yahoo": _fetcher({"KOSPI": {"2026-09-25": 2600.0}})}
    rp.main(["--db", str(db), "--apply"], fetchers=fetchers)
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM macro_history_repull_audit")
    con.commit()
    con.close()
    rp.main(["--db", str(db), "--apply"], fetchers=fetchers)
    con = sqlite3.connect(str(db))
    statuses = [r[0] for r in con.execute("SELECT status FROM macro_history_repull_audit")]
    con.close()
    assert rp.STATUS_OVERWRITTEN not in statuses
    assert rp.STATUS_STAMPED not in statuses


def test_thieu_db_tra_ve_2(tmp_path: Path) -> None:
    assert rp.main(["--db", str(tmp_path / "khong_ton_tai.db")]) == 2


def test_force_vars_bo_qua_agreement_guard(tmp_path: Path) -> None:
    """--force-vars: lich su khop provider (62-82%) van duoc ghi de."""
    rows = [(f"2026-09-{20 + i:02d}", "VIX", 20.0 + i, None) for i in range(12)]
    prov = {f"2026-09-{20 + i:02d}": 500.0 + i for i in range(12)}
    db_a = tmp_path / "khong_force.db"
    _make_db(db_a, rows)
    rp.main(
        ["--db", str(db_a), "--apply"],
        fetchers={"yahoo": _fetcher({"VIX": prov})},
    )
    assert _rows(db_a)[("VIX", "2026-09-20")] == (20.0, None)

    db_b = tmp_path / "force.db"
    _make_db(db_b, rows)
    rc = rp.main(
        ["--db", str(db_b), "--apply", "--force-vars", "VIX,ES_FUTURES"],
        fetchers={"yahoo": _fetcher({"VIX": prov})},
    )
    assert rc == 0
    assert _rows(db_b)[("VIX", "2026-09-20")] == (500.0, "yahoo")
    con = sqlite3.connect(str(db_b))
    status = con.execute("SELECT status FROM macro_history_repull_audit").fetchone()[0]
    con.close()
    assert status == rp.STATUS_OVERWRITTEN


def test_stamp_legacy_interbank_tach_theo_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "interbank.csv"
    csv_path.write_text(
        "date,variable,value\n2026-06-30,INTERBANK_ON,4.5\n2026-06-30,INTERBANK_1W,5.1\n",
        encoding="utf-8",
    )
    db = tmp_path / "screener_cache.db"
    _make_db(
        db,
        [
            ("2026-06-30", "INTERBANK_ON", 4.5, None),  # khop CSV -> synthetic
            ("2026-09-25", "INTERBANK_1W", 5.1, None),  # sau ngay CSV -> seeder
            ("2026-09-25", "INTERBANK_3M", 6.0, None),  # tenor bootstrap khong tao -> seeder
            ("2026-06-29", "INTERBANK_1M", 9.99, None),  # khong khop CSV -> khong doan
        ],
    )
    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy", "--interbank-csv", str(csv_path)],
        fetchers={"yahoo": _fetcher({})},
    )
    assert rc == 0
    after = _rows(db)
    assert after[("INTERBANK_ON", "2026-06-30")] == (4.5, "synthetic:bootstrap_v1")
    assert after[("INTERBANK_1W", "2026-09-25")] == (5.1, "interbank_seeder")
    assert after[("INTERBANK_3M", "2026-09-25")] == (6.0, "interbank_seeder")
    assert after[("INTERBANK_1M", "2026-06-29")] == (9.99, None)


def test_stamp_legacy_fred_khong_keo_duoc(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(
        db,
        [
            ("2026-09-25", "US10Y_YIELD", 5.18, None),
            ("2026-09-25", "FOMC_DISSENT", 0, None),
        ],
    )
    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy", "--skip-fred"],
        fetchers={"yahoo": _fetcher({})},
    )
    assert rc == 0
    after = _rows(db)
    assert after[("US10Y_YIELD", "2026-09-25")] == (5.18, "fred:unreachable_no_key")
    assert after[("FOMC_DISSENT", "2026-09-25")] == (0.0, "world_sensor:no_fred_series")
    con = sqlite3.connect(str(db))
    statuses = {r[0] for r in con.execute("SELECT status FROM macro_history_repull_audit")}
    con.close()
    assert statuses == {rp.STATUS_LEGACY_STAMPED}


def test_stamp_legacy_fallback_default(tmp_path: Path) -> None:
    """FED_TARGET_RATE = 5.5 (DEFAULT_FED_RATE) khop FRED 4.0 -> MISMATCH -> hang so mac dinh."""
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "FED_TARGET_RATE", 5.5, None)])

    def _fred(mapping, *args, **kwargs):
        return ({"FED_TARGET_RATE": {"2026-09-25": 4.0}}, {"FED_TARGET_RATE"})

    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy"],
        fetchers={"yahoo": _fetcher({}), "fred": _fred},
    )
    assert rc == 0
    assert _rows(db)[("FED_TARGET_RATE", "2026-09-25")] == (5.5, "world_sensor:fallback_default")


def test_skip_fred_khong_stamp_khi_thieu_co(tmp_path: Path) -> None:
    """Khong --stamp-legacy -> FRED_UNREACHABLE van la trang thai bao cao, source van NULL."""
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "US10Y_YIELD", 5.18, None)])
    rc = rp.main(
        ["--db", str(db), "--apply", "--skip-fred"],
        fetchers={"yahoo": _fetcher({})},
    )
    assert rc == 0
    assert _rows(db)[("US10Y_YIELD", "2026-09-25")] == (5.18, None)
    con = sqlite3.connect(str(db))
    status = con.execute("SELECT status FROM macro_history_repull_audit").fetchone()[0]
    con.close()
    assert status == rp.STATUS_FRED_UNREACHABLE


def test_stamp_legacy_yahoo_thieu_bar(tmp_path: Path) -> None:
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "KOSPI", 2600.0, None)])
    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy"],
        fetchers={"yahoo": _fetcher({"KOSPI": {"2026-09-24": 2500.0}})},
    )
    assert rc == 0
    assert _rows(db)[("KOSPI", "2026-09-25")] == (2600.0, "yahoo:no_bar_for_date")


def test_ingest_fred_transform_don_vi_va_bounds() -> None:
    out = {"QT_BALANCE_TR": {}, "RESERVES_TR": {}}
    ok: set[str] = set()
    rp._ingest_fred_series(out, ok, "QT_BALANCE_TR", "DATE,VALUE\n2026-09-23,6747704\n2026-09-22,99999999\n")
    rp._ingest_fred_series(out, ok, "RESERVES_TR", "DATE,VALUE\n2026-09-23,3300\n")
    assert out["QT_BALANCE_TR"] == {"2026-09-23": 6.75}  # millions -> trieu ty
    assert out["RESERVES_TR"] == {"2026-09-23": 3.3}  # ty USD -> trieu ty
    assert ok == {"QT_BALANCE_TR", "RESERVES_TR"}


def test_fetch_fred_probe_that_bai_tra_rong(monkeypatch) -> None:
    def _boom(series_id, timeout):
        raise TimeoutError("fred down")

    monkeypatch.setattr(rp, "_fred_get", _boom)
    data, ok = rp.fetch_fred({"US10Y_YIELD": "DGS10"})
    assert data == {"US10Y_YIELD": {}}
    assert ok == set()


def test_fred_get_retry_3_lan(monkeypatch) -> None:
    import requests

    calls = {"n": 0}

    class _Resp:
        text = "DATE,VALUE\n2026-09-25,5.18\n"

        def raise_for_status(self) -> None:
            return None

    def _fake_get(url, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("chong len")
        return _Resp()

    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    text = rp._fred_get("DGS10", 5.0)
    assert calls["n"] == 3
    assert text.startswith("DATE")


def test_stamp_legacy_khong_de_nguon_da_co(tmp_path: Path) -> None:
    csv_path = tmp_path / "interbank.csv"
    csv_path.write_text("date,variable,value\n2026-06-30,INTERBANK_ON,4.5\n", encoding="utf-8")
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-06-30", "INTERBANK_ON", 4.5, "interbank_seeder")])
    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy", "--interbank-csv", str(csv_path)],
        fetchers={"yahoo": _fetcher({})},
    )
    assert rc == 0
    assert _rows(db)[("INTERBANK_ON", "2026-06-30")] == (4.5, "interbank_seeder")


def test_stamp_legacy_fed_target_khong_phai_default(tmp_path: Path) -> None:
    """FED_TARGET_RATE khong phai hang so 5.5 -> nhan nguon theo nguon that (CME), khong phai yfinance."""
    db = tmp_path / "screener_cache.db"
    _make_db(db, [("2026-09-25", "FED_TARGET_RATE", 4.5, None)])

    def _fred(mapping, *args, **kwargs):
        return ({"FED_TARGET_RATE": {"2026-09-25": 5.0}}, {"FED_TARGET_RATE"})

    rc = rp.main(
        ["--db", str(db), "--apply", "--stamp-legacy"],
        fetchers={"yahoo": _fetcher({}), "fred": _fred},
    )
    assert rc == 0
    assert _rows(db)[("FED_TARGET_RATE", "2026-09-25")] == (4.5, "world_sensor:cme_fedwatch")
