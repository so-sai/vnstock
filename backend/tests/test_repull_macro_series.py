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
