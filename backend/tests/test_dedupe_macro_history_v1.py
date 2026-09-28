"""Tests — E9: dedupe macro_history v1 + backfill source (data hygiene, không đổi quyết định)."""

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from backend.scripts.dedupe_macro_history_v1 import (
    BACKUP_TABLE,
    analyze,
    backfill_source,
    dedupe,
    main,
)

DDL_V1 = """CREATE TABLE macro_history(
    date TEXT, variable TEXT, value REAL,
    is_stale INTEGER DEFAULT 0, source TEXT)"""
DDL_V2 = """CREATE TABLE macro_history_v2 (
    variable TEXT NOT NULL, date TEXT NOT NULL, value REAL,
    asset_class TEXT NOT NULL, unit TEXT NOT NULL, source TEXT NOT NULL,
    raw_value REAL, raw_unit TEXT, confidence REAL DEFAULT 1.0,
    valid_from TEXT, valid_to TEXT, freshness_score REAL, latency_ms INTEGER,
    ingested_at TEXT, metadata TEXT)"""


@pytest.fixture()
def con() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(DDL_V1)
    c.execute(DDL_V2)
    rows = [
        ("2026-09-25", "GOLD_XAU", 4328.1, 0, None),
        ("2026-09-24", "GOLD_XAU", 4301.7, 0, None),  # bản đầu (rowid nhỏ)
        ("2026-09-24", "GOLD_XAU", 4302.0, 0, None),  # bản sau = classifier đọc
        ("2026-09-24", "BRENT_OIL", 100.48, 0, None),
        ("2026-09-24", "BRENT_OIL", 100.48, 0, None),  # trùng giá trị
        ("2021-01-05", "DXY", 90.0, 0, "old_source"),  # đã có source -> không đụng
    ]
    c.executemany("INSERT INTO macro_history (date, variable, value, is_stale, source) VALUES (?,?,?,?,?)", rows)
    c.executemany(
        "INSERT INTO macro_history_v2 (variable, date, value, asset_class, unit, source) VALUES (?,?,?,?,?,?)",
        [
            ("GOLD_XAU", "2026-09-24", 4302.0, "COMMODITY", "USD/oz", "yahoo"),
            ("GOLD_XAU", "2026-09-25", 4328.1, "COMMODITY", "USD/oz", "yahoo"),
            ("BRENT_OIL", "2026-09-24", 100.48, "COMMODITY", "USD/bbl", "yahoo"),
        ],
    )
    yield c
    c.close()


class TestAnalyze:
    def test_counts(self, con):
        s = analyze(con)
        assert s["rows"] == 6
        assert s["distinct_keys"] == 4
        assert s["duplicate_keys"] == 2
        assert s["duplicate_rows"] == 2
        assert s["source_null"] == 5
        assert s["keys_with_differing_values"] == 1  # GOLD_XAU 2026-09-24

    def test_backfillable_from_v2(self, con):
        # 5 dòng v1 (source NULL) khớp (variable, date) với v2:
        # GOLD_XAU 09-24 x2 + 09-25, BRENT_OIL 09-24 x2
        assert analyze(con)["rows_backfillable_from_v2"] == 5


class TestDedupe:
    def test_keeps_max_rowid_value(self, con):
        """Giữ đúng dòng mà classifier đọc (ROW_NUMBER() ORDER BY rowid DESC)."""
        rep = dedupe(con)
        assert rep["removed_rows"] == 2
        rows = con.execute("SELECT value FROM macro_history WHERE date='2026-09-24' AND variable='GOLD_XAU'").fetchall()
        assert [r[0] for r in rows] == [4302.0], "phải giữ bản rowid lớn nhất (4302.0)"

    def test_no_duplicate_keys_remain(self, con):
        dedupe(con)
        assert analyze(con)["duplicate_rows"] == 0

    def test_idempotent(self, con):
        first = dedupe(con)["removed_rows"]
        second = dedupe(con)["removed_rows"]
        assert first == 2 and second == 0

    def test_identical_duplicates_collapsed(self, con):
        dedupe(con)
        n = con.execute("SELECT COUNT(*) FROM macro_history WHERE date='2026-09-24' AND variable='BRENT_OIL'").fetchone()[0]
        assert n == 1


class TestBackfillSource:
    def test_fills_null_from_v2(self, con):
        rep = backfill_source(con)
        # 5 dòng v1 source NULL có bản ghi v2 tương ứng (chưa dedupe nên vẫn còn bản trùng)
        assert rep["rows_updated"] == 5
        src = con.execute("SELECT source FROM macro_history WHERE date='2026-09-25' AND variable='GOLD_XAU'").fetchone()[0]
        assert src == "yahoo"

    def test_does_not_overwrite_existing_source(self, con):
        backfill_source(con)
        src = con.execute("SELECT source FROM macro_history WHERE variable='DXY'").fetchone()[0]
        assert src == "old_source"

    def test_idempotent(self, con):
        backfill_source(con)
        assert backfill_source(con)["rows_updated"] == 0

    def test_rows_without_v2_stay_null(self, con):
        """Không có bản ghi v2 → giữ NULL, không bịa nguồn."""
        con.execute(
            "INSERT INTO macro_history (date, variable, value, source) VALUES (?,?,?,?)",
            ("2020-01-02", "USD_VND", 23000.0, None),
        )
        backfill_source(con)
        nulls = con.execute("SELECT variable FROM macro_history WHERE source IS NULL").fetchall()
        assert [r[0] for r in nulls] == ["USD_VND"]


class TestMainDryRun:
    @pytest.fixture()
    def db(self, tmp_path) -> Path:
        p = tmp_path / "screener_cache.db"
        c = sqlite3.connect(p)
        c.execute(DDL_V1)
        c.execute(DDL_V2)
        c.executemany(
            "INSERT INTO macro_history (date, variable, value, source) VALUES (?,?,?,?)",
            [
                ("2026-09-24", "GOLD_XAU", 4301.7, None),
                ("2026-09-24", "GOLD_XAU", 4302.0, None),
                ("2026-09-25", "GOLD_XAU", 4328.1, None),
            ],
        )
        c.execute(
            "INSERT INTO macro_history_v2 (variable, date, value, asset_class, unit, source) VALUES (?,?,?,?,?,?)",
            ("GOLD_XAU", "2026-09-25", 4328.1, "COMMODITY", "USD/oz", "yahoo"),
        )
        c.commit()
        c.close()
        return p

    def _rows(self, p: Path) -> list:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        out = c.execute("SELECT date, value, source FROM macro_history ORDER BY rowid").fetchall()
        c.close()
        return out

    def test_dry_run_changes_nothing(self, db):
        before = self._rows(db)
        assert main(["--db", str(db)]) == 0
        assert self._rows(db) == before

    def test_apply_dedupes_backfills_and_backs_up(self, db):
        before = self._rows(db)
        assert main(["--db", str(db), "--apply"]) == 0
        after = self._rows(db)

        assert len(before) == 3 and len(after) == 2, "phải bỏ 1 dòng trùng"
        assert [r[1] for r in after if r[0] == "2026-09-24"] == [4302.0]
        gold25 = [r for r in after if r[0] == "2026-09-25"]
        assert gold25[0][2] == "yahoo", "source phải được backfill từ v2"
        assert [r[2] for r in after if r[0] == "2026-09-24"] == [None], "v2 không có ngày này → giữ NULL"

        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        bak = c.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0]
        c.close()
        assert bak == 3, "backup phải giữ nguyên bản gốc"

    def test_missing_db_returns_2(self, tmp_path):
        assert main(["--db", str(tmp_path / "nope.db")]) == 2
