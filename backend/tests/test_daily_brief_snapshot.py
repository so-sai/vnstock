"""Tests for Daily Brief Snapshot Producer (`src.services.daily_brief_snapshot`).

[WHY] Bảo vệ hợp đồng dữ liệu JSON giữa Python producer (`ptck.py daily-brief`)
và Rust Zalo consumer (`minh_chung.exe`): ngăn hồi quy lọc penny, chấm sai 4 ngưỡng
mở khóa Governor, hoặc bịa số khi thiếu dữ liệu (`Missing > Fake`).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.services.daily_brief_snapshot import (
    SCHEMA_VERSION,
    build_daily_brief_snapshot,
    build_paper_portfolio_summary,
    build_sector_flow_summary,
    build_valuation_leaders,
    evaluate_governor_progress,
    filter_clean_momentum_candidates,
    write_daily_brief_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_governor_progress_all_four_thresholds() -> None:
    """[WHY] Xác nhận đúng toán tử biên (>= cho score/breadth, > chặt cho vol/trend)."""
    progress = evaluate_governor_progress(
        {
            "regime_score": 0.40,
            "breadth_pct": 59.9,
            "vol_score": 0.20,
            "trend_score": 0.01,
            "is_degraded": 0,
        }
    )
    by_key = {item["key"]: item for item in progress}
    assert len(progress) == 4
    assert by_key["regime_score"]["status"] == "PASS"
    assert by_key["breadth_pct"]["status"] == "LOCKED"
    # [WHY]: vol_score dùng dấu `>` nên đúng bằng 0.20 vẫn bị khóa (LOCKED).
    assert by_key["vol_score"]["status"] == "LOCKED"
    assert by_key["trend_score"]["status"] == "PASS"


def test_governor_progress_fail_closed_on_missing_or_degraded() -> None:
    """[WHY] Thiếu dòng regime, is_degraded != 0, hoặc giá trị NaN/None phải trả MISSING (không default 0)."""
    for bad_row in (
        None,
        {
            "regime_score": 0.80,
            "breadth_pct": 80.0,
            "vol_score": 0.70,
            "trend_score": 0.50,
            "is_degraded": 1,
        },
        {
            "regime_score": 0.80,
            "breadth_pct": 80.0,
            "vol_score": 0.70,
            "trend_score": 0.50,
            "is_degraded": None,
        },
    ):
        res = evaluate_governor_progress(bad_row)
        assert all(x["status"] == "MISSING" and x["current"] is None for x in res)

    partial = evaluate_governor_progress(
        {
            "regime_score": 0.32,
            "breadth_pct": None,
            "vol_score": float("nan"),
            "trend_score": 0.0,
            "is_degraded": 0,
        }
    )
    by_key = {item["key"]: item for item in partial}
    assert by_key["regime_score"]["status"] == "LOCKED"
    assert by_key["breadth_pct"] == {
        **by_key["breadth_pct"],
        "current": None,
        "status": "MISSING",
    }
    assert by_key["vol_score"]["status"] == "MISSING"
    assert by_key["trend_score"]["status"] == "LOCKED"


def test_filter_clean_momentum_blocks_penny_distressed_weak_illiquid() -> None:
    """[WHY] Chống lọt penny (<10k), thanh khoản yếu (<500k), RS < 85, chỉ số, mã DISTRESSED hoặc thiếu BCTC."""
    raw = [
        # Penny bị chặn dù RS cao
        {"symbol": "TTF", "close": 1070.0, "adv20_shares": 2_000_000.0, "rs": 95.0, "health_archetype": "STEADY_EARNER"},
        {"symbol": "DST", "close": 4100.0, "adv20_shares": 1_500_000.0, "rs": 92.0, "health_archetype": "CYCLICAL"},
        {"symbol": "IDJ", "close": 2400.0, "adv20_shares": 1_200_000.0, "rs": 91.0, "health_archetype": "CYCLICAL"},
        # Chỉ số không phải cổ phiếu
        {"symbol": "VNINDEX", "close": 12500.0, "adv20_shares": 5_000_000.0, "rs": 99.0, "health_archetype": "STEADY_EARNER"},
        # Thanh khoản CP < 500k
        {"symbol": "ILLQ", "close": 20000.0, "adv20_shares": 499_999.0, "rs": 95.0, "health_archetype": "STEADY_EARNER"},
        # Thanh khoản VND < 10 tỷ đồng/phiên (dù ADV20 CP đạt 500k) phải bị chặn
        {
            "symbol": "LVND",
            "close": 19_000.0,
            "adv20_shares": 500_000.0,
            "adv20_vnd": 9_999_999_999.0,
            "rs": 95.0,
            "health_archetype": "STEADY_EARNER",
        },
        # RS < 85
        {"symbol": "WKRS", "close": 25000.0, "adv20_shares": 1_000_000.0, "rs": 84.9, "health_archetype": "STEADY_EARNER"},
        # DISTRESSED bị loại
        {"symbol": "BADA", "close": 30000.0, "adv20_shares": 1_000_000.0, "rs": 99.0, "health_archetype": "DISTRESSED"},
        # Thiếu BCTC (None) bị loại theo Missing > Fake
        {"symbol": "NOFN", "close": 30000.0, "adv20_shares": 1_000_000.0, "rs": 99.0, "health_archetype": None},
        # Các mã hợp lệ (PVT bị gap nên ret_1d_pct = None vẫn phải giữ nguyên None)
        {
            "symbol": "NAB",
            "close": 13200.0,
            "adv20_shares": 800_000.0,
            "rs": 91.3,
            "health_archetype": "STEADY_EARNER",
            "ret_1d_pct": 1.15,
            "breakout_20d": False,
        },
        {
            "symbol": "NVB",
            "close": 15800.0,
            "adv20_shares": 700_000.0,
            "rs": 96.6,
            "health_archetype": "COMMODITY",
            "ret_1d_pct": 2.60,
            "breakout_20d": True,
        },
        {
            "symbol": "PVT",
            "close": 21100.0,
            "adv20_shares": 900_000.0,
            "rs": 89.6,
            "health_archetype": "LEVERAGED_GROWTH",
            "ret_1d_pct": None,
            "breakout_20d": False,
        },
    ]

    result = filter_clean_momentum_candidates(raw, top_n=10)
    assert [row["symbol"] for row in result] == ["NVB", "NAB", "PVT"]
    assert result[0]["breakout_20d"] is True
    assert result[2]["ret_1d_pct"] is None


def test_write_daily_brief_snapshot_atomic_roundtrip(tmp_path: Path) -> None:
    """[WHY] Ghi JSON nguyên tử đảm bảo UTF-8 đầy đủ và không sót file tạm `.tmp_daily_brief_*`."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": "2026-11-13",
        "governor_progress": evaluate_governor_progress(
            {
                "regime_score": 0.3203,
                "breadth_pct": 29.3,
                "vol_score": 0.6141,
                "trend_score": 0.0,
                "is_degraded": 0,
            }
        ),
        "momentum_clean": [],
    }
    target = tmp_path / "daily_brief_snapshot.json"
    written = write_daily_brief_snapshot(payload, out_path=target)
    assert written == target
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == SCHEMA_VERSION
    assert loaded["governor_progress"][0]["label"] == "Điểm sức khỏe thị trường"
    leftover_tmps = list(tmp_path.glob(".tmp_daily_brief_*"))
    assert leftover_tmps == []


def test_build_snapshot_fail_closed_when_financial_db_missing(tmp_path: Path) -> None:
    """[WHY] DB tài chính không tồn tại -> Không thể kiểm định DISTRESSED -> momentum_clean rỗng + cảnh báo."""
    screener_db = tmp_path / "screener.db"
    with contextlib.closing(sqlite3.connect(screener_db)) as conn:
        conn.execute(
            """
            CREATE TABLE daily_ohlcv (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
                volume REAL, is_stale INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE regime_history (
                date TEXT PRIMARY KEY, regime_score REAL, breadth_pct REAL,
                vol_score REAL, trend_score REAL, is_degraded INTEGER
            )
            """
        )
        conn.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX','2026-11-12',1200,1205,1195,1202,1000000,0)")
        conn.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX','2026-11-13',1202,1210,1200,1208,1100000,0)")
        conn.execute("INSERT INTO regime_history VALUES ('2026-11-13', 0.3203, 29.3, 0.6141, 0.0, 0)")
        conn.commit()

    snap = build_daily_brief_snapshot(
        as_of_date="2026-11-13",
        screener_db=screener_db,
        financial_db=tmp_path / "missing_financial.db",
        warmup=5,
    )
    assert snap["schema_version"] == SCHEMA_VERSION
    assert snap["as_of_date"] == "2026-11-13"
    assert snap["momentum_clean"] == []
    assert "FINANCIAL_DB_UNAVAILABLE" in snap["warnings"]
    assert snap["governor_progress"][0]["status"] == "LOCKED"
    assert snap["governor_progress"][2]["status"] == "PASS"


def test_paper_portfolio_summary_ok_and_stale_fail_closed(tmp_path: Path) -> None:
    """[WHY] Xác nhận tính đúng NAV/1D/lũy kế của danh mục theo dõi và fail-closed nếu thiếu phiên d0."""
    db_path = tmp_path / "screener_paper.db"
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        # Thiếu bảng -> fail-closed MISSING
        missing_tbl = build_paper_portfolio_summary(conn, "2026-11-13", "2026-11-12")
        assert missing_tbl["status"] == "MISSING"
        assert missing_tbl["total_equity"] is None

        conn.execute("CREATE TABLE paper_portfolio_state (portfolio_id TEXT PRIMARY KEY, initial_capital REAL)")
        conn.execute(
            """
            CREATE TABLE paper_equity_curve (
                portfolio_id TEXT, date TEXT, settled_cash REAL, market_value REAL, total_equity REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE paper_lots (
                portfolio_id TEXT, symbol TEXT, open_date TEXT, quantity INTEGER, is_closed INTEGER
            )
            """
        )
        conn.execute("INSERT INTO paper_portfolio_state VALUES ('SEL_PAPER_V1', 1000000000.0)")
        conn.execute(
            "INSERT INTO paper_equity_curve VALUES ('SEL_PAPER_V1', '2026-11-12', 10000000.0, 885000000.0, 895000000.0)"
        )
        conn.execute(
            "INSERT INTO paper_equity_curve VALUES ('SEL_PAPER_V1', '2026-11-13', 8500000.0, 888390000.0, 896890000.0)"
        )
        conn.execute("INSERT INTO paper_lots VALUES ('SEL_PAPER_V1', 'VCB', '2026-07-06', 3200, 0)")
        conn.execute("INSERT INTO paper_lots VALUES ('SEL_PAPER_V1', 'FPT', '2026-07-06', 2970, 0)")
        conn.execute("INSERT INTO paper_lots VALUES ('SEL_PAPER_V1', 'OLD', '2026-06-20', 1000, 1)")
        conn.commit()

        ok = build_paper_portfolio_summary(conn, "2026-11-13", "2026-11-12")
        assert ok["status"] == "OK"
        assert ok["total_equity"] == 896_890_000.0
        assert ok["settled_cash"] == 8_500_000.0
        assert ok["ret_1d_pct"] == 0.21
        assert ok["cum_ret_pct"] == -10.31
        assert ok["open_symbols"] == ["FPT", "VCB"]

        # Nếu phiên mới nhất chưa cập nhật vào paper_equity_curve -> phải trả MISSING, cấm mượn NAV cũ
        stale = build_paper_portfolio_summary(conn, "2026-11-14", "2026-11-13")
        assert stale["status"] == "MISSING"
        assert stale["total_equity"] is None

        # Nếu có gap phiên d1 (2026-11-12 != vni_d1='2026-11-11') -> ret_1d_pct phải None
        gapped = build_paper_portfolio_summary(conn, "2026-11-13", "2026-11-11")
        assert gapped["status"] == "OK"
        assert gapped["ret_1d_pct"] is None


def test_sector_flow_summary_ranks_and_filters_small_sectors(tmp_path: Path) -> None:
    """[WHY] Đảm bảo ICB flow_map lọc ngành < 3 mã, loại chỉ số/stale và Fail-Closed khi vni_d1 = None."""
    db_path = tmp_path / "screener_flow.db"
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        # Thiếu bảng symbol_industry -> MISSING
        assert build_sector_flow_summary(conn, "2026-11-13", "2026-11-12")["status"] == "MISSING"

        conn.execute(
            """
            CREATE TABLE daily_ohlcv (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, is_stale INTEGER
            )
            """
        )
        conn.execute("CREATE TABLE symbol_industry (symbol TEXT PRIMARY KEY, icb_name2 TEXT)")

        sectors_pct = [
            ("Ngân hàng", 2.0),
            ("Công nghệ Thông tin", 1.5),
            ("Bán lẻ", 1.0),
            ("Bất động sản", 0.0),
            ("Thép", -1.0),
            ("Dầu khí", -2.0),
        ]
        idx = 1
        for sec_name, pct in sectors_pct:
            for _ in range(3):
                sym = f"M{idx:02d}"
                idx += 1
                c0 = 10_000.0 * (1.0 + pct / 100.0)
                conn.execute("INSERT INTO symbol_industry VALUES (?, ?)", (sym, sec_name))
                conn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-12', 10000, 10000, 10000, 10000, 100000, 0)", (sym,))
                conn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-13', ?, ?, ?, ?, 100000, 0)", (sym, c0, c0, c0, c0))

        # Ngành siêu nhỏ 2 mã (+50%) phải bị lọc vì HAVING COUNT(*) >= 3
        for sym in ("TIN1", "TIN2"):
            conn.execute("INSERT INTO symbol_industry VALUES (?, 'Niche2')", (sym,))
            conn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-12', 10000, 10000, 10000, 10000, 100000, 0)", (sym,))
            conn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-13', 15000, 15000, 15000, 15000, 100000, 0)", (sym,))

        # Chỉ số & dòng stale phải bị loại kể cả khi nằm trong symbol_industry
        conn.execute("INSERT INTO symbol_industry VALUES ('VNINDEX', 'Dầu khí')")
        conn.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX', '2026-11-12', 1000, 1000, 1000, 1000, 1000, 0)")
        conn.execute("INSERT INTO daily_ohlcv VALUES ('VNINDEX', '2026-11-13', 2000, 2000, 2000, 2000, 1000, 0)")
        conn.commit()

        fm = build_sector_flow_summary(conn, "2026-11-13", "2026-11-12")
        assert fm["status"] == "OK"
        assert fm["sector_count"] == 6
        assert [s["sector"] for s in fm["top_sectors"]] == ["Ngân hàng", "Công nghệ Thông tin", "Bán lẻ"]
        assert [s["sector"] for s in fm["bottom_sectors"]] == ["Dầu khí", "Thép", "Bất động sản"]
        assert "Niche2" not in [s["sector"] for s in fm["top_sectors"]]

        # Fail-closed khi gap phiên (vni_d1=None)
        assert build_sector_flow_summary(conn, "2026-11-13", None)["status"] == "MISSING"


def test_valuation_leaders_filters_healthy_pit_and_sorts_by_composite_z(tmp_path: Path) -> None:
    """[WHY] Lọc đúng archetype khỏe, PIT period <= 2026Q2, clean metric và chặn look-ahead 2026Q3."""
    from types import SimpleNamespace

    class _FakeHealthEngine:
        def __init__(self, arch_map: dict[str, str | None]) -> None:
            self._arch_map = arch_map

        def analyze(self, sym: str, _as_of_date: str):
            arch = self._arch_map.get(sym)
            return None if arch is None else SimpleNamespace(archetype=arch)

    screener_db = tmp_path / "screener_val.db"
    fin_db = tmp_path / "fin_val.db"
    with contextlib.closing(sqlite3.connect(screener_db)) as sconn:
        sconn.execute(
            """
            CREATE TABLE daily_ohlcv (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, is_stale INTEGER
            )
            """
        )
        for sym, c1, c0 in (
            ("FPT", 49500.0, 50100.0),
            ("VCB", 57000.0, 57500.0),
            ("BAD1", 20000.0, 21000.0),
            ("DIRT", 25000.0, 25500.0),
        ):
            sconn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-12', ?, ?, ?, ?, 500000, 0)", (sym, c1, c1, c1, c1))
            sconn.execute("INSERT INTO daily_ohlcv VALUES (?, '2026-11-13', ?, ?, ?, ?, 600000, 0)", (sym, c0, c0, c0, c0))
        sconn.commit()

        with contextlib.closing(sqlite3.connect(fin_db)) as fconn:
            fconn.execute(
                """
                CREATE TABLE valuation_scores (
                    symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL,
                    z_score REAL, zone TEXT, is_clean_metric INTEGER
                )
                """
            )
            fconn.execute(
                "CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)"
            )
            # FPT: STEADY_EARNER, 2026Q2 hợp lệ (-1.40) + 2026Q3 future (phải bị chặn bởi PIT)
            fconn.execute("INSERT INTO valuation_scores VALUES ('FPT', '2026Q2', 'PE', 13.24, -1.60, 'CHEAP', 1)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('FPT', '2026Q2', 'PB', 3.32, -1.20, 'CHEAP', 1)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('FPT', '2026Q3', 'PE', 5.0, -3.00, 'CHEAP', 1)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('FPT', '2026Q3', 'PB', 1.0, -3.00, 'CHEAP', 1)")
            for q in ("2026Q2", "2026Q1", "2025Q4", "2025Q3"):
                fconn.execute("INSERT INTO health_ratios VALUES ('FPT', ?, 'ROE', 0.06)", (q,))
            # VCB: HIGH_QUALITY_COMPOUNDER, chỉ 3 quý ROE -> roe_ttm_pct phải None (không đoán/annualize)
            fconn.execute("INSERT INTO valuation_scores VALUES ('VCB', '2026Q2', 'PE', 12.48, -1.00, 'FAIR', 1)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('VCB', '2026Q2', 'PB', 2.09, -1.00, 'CHEAP', 1)")
            for q in ("2026Q2", "2026Q1", "2025Q4"):
                fconn.execute("INSERT INTO health_ratios VALUES ('VCB', ?, 'ROE', 0.05)", (q,))
            # BAD1: CYCLICAL -> bị loại bởi archetype
            fconn.execute("INSERT INTO valuation_scores VALUES ('BAD1', '2026Q2', 'PE', 5.0, -3.0, 'CHEAP', 1)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('BAD1', '2026Q2', 'PB', 0.8, -3.0, 'CHEAP', 1)")
            # DIRT: is_clean_metric = 0 -> bị loại bởi clean metric guard
            fconn.execute("INSERT INTO valuation_scores VALUES ('DIRT', '2026Q2', 'PE', 6.0, -2.5, 'CHEAP', 0)")
            fconn.execute("INSERT INTO valuation_scores VALUES ('DIRT', '2026Q2', 'PB', 0.9, -2.5, 'CHEAP', 0)")
            fconn.commit()

        engine = _FakeHealthEngine(
            {"FPT": "STEADY_EARNER", "VCB": "HIGH_QUALITY_COMPOUNDER", "BAD1": "CYCLICAL", "DIRT": "STEADY_EARNER"}
        )
        res = build_valuation_leaders(
            sconn,
            fin_db,
            engine,  # type: ignore[arg-type]
            "2026-11-13",
            "2026-11-12",
            candidate_symbols=("FPT", "VCB", "BAD1", "DIRT"),
        )
        assert res["status"] == "OK"
        assert res["pit_quarter"] == "2026Q2"
        assert [x["symbol"] for x in res["leaders"]] == ["FPT", "VCB"]
        assert res["leaders"][0]["period"] == "2026Q2"
        assert res["leaders"][0]["composite_z"] == -1.4
        assert res["leaders"][0]["roe_ttm_pct"] == 24.0
        assert res["leaders"][1]["roe_ttm_pct"] is None


def test_cli_daily_brief_smoke_on_live_db(tmp_path: Path) -> None:
    """[WHY] Chạy `ptck.py daily-brief` thật trên DB hiện tại (nếu có) kiểm chứng hợp đồng đầu cuối."""
    live_screener = REPO_ROOT / "backend" / "data" / "screener_cache.db"
    if not live_screener.exists():
        return
    out_file = tmp_path / "live_snap.json"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "ptck.py"), "daily-brief", "--out", str(out_file)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    snap = json.loads(out_file.read_text(encoding="utf-8"))
    assert snap["schema_version"] == 1
    assert len(snap["governor_progress"]) == 4
    assert snap["paper_portfolio"]["status"] == "OK"
    assert snap["flow_map"]["status"] == "OK"
    assert snap["valuation_leaders"]["status"] == "OK"
    assert len(snap["valuation_leaders"]["leaders"]) == 5
    # [WHY]: Chốt chặn chống penny quay lại radar động lượng sạch
    for row in snap["momentum_clean"]:
        assert row["close"] >= 10_000.0
        assert row["adv20_shares"] >= 500_000.0
        assert row["adv20_vnd"] >= 10_000_000_000.0
        assert row["rs"] >= 85.0
        assert row["health_archetype"] != "DISTRESSED"
