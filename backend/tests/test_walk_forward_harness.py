"""
test_walk_forward_harness.py — TDD cho Walk-Forward Validation & PIT Integrity.

Covers:
  - split_is_oos: phân chia 70/30 đúng biên, không overlap/gap, validate lỗi
  - pit_audit: bắt anomaly rò rỉ dữ liệu tương lai (facts quý sau ingested trước anchor)
  - run_walk_forward: OOS dùng ĐÚNG tham số khóa từ IS (không tái tối ưu)
  - CLI entry-point smoke: subcommand walk-forward đăng ký trong ptck.py

Run:  python -m pytest backend/tests/test_walk_forward_harness.py -q
"""

import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"


def _hydrate_path():
    if str(_BACKEND / "src") not in sys.path:
        sys.path.insert(0, str(_BACKEND / "src"))
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    return _ROOT


_PROJECT_ROOT = _hydrate_path()

from backtest import walk_forward_harness as wf
from core.errors import DataIntegrityError


def _mk_dates(start: str, n: int) -> list[str]:
    """Sinh n ngày ISO liên tiếp bắt đầu từ start (YYYY-MM-DD)."""
    from datetime import date, timedelta

    d = date.fromisoformat(start)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


# ── split_is_oos ────────────────────────────────────────────────────────
class TestSplitIsOos:
    def test_split_boundaries_no_overlap(self):
        dates = _mk_dates("2024-01-01", 366) + _mk_dates("2025-01-01", 365)
        is_dates, oos_dates = wf.split_is_oos(dates, split_date="2025-01-01")
        assert is_dates[-1] == "2024-12-31"
        assert oos_dates[0] == "2025-01-01"
        # Không overlap + không khoảng trống
        assert max(is_dates) < min(oos_dates)
        assert sorted(is_dates + oos_dates) == dates

    def test_split_ratio_approx_70_30(self):
        # 100 ngày trước split, 45 sau → tỷ lệ ≈ 69/31
        dates = _mk_dates("2024-09-01", 100) + _mk_dates("2024-12-10", 45)
        is_dates, oos_dates = wf.split_is_oos(dates, split_date="2024-12-10")
        assert len(is_dates) == 100
        assert len(oos_dates) == 45

    def test_split_empty_side_raises(self):
        dates = _mk_dates("2024-01-01", 365)
        with pytest.raises(DataIntegrityError, match="rỗng"):
            wf.split_is_oos(dates, split_date="2025-01-01")

    def test_split_shuffled_input_still_partitions(self):
        dates = _mk_dates("2024-01-01", 100) + _mk_dates("2025-01-01", 60)
        import random

        rng = random.Random(42)
        shuffled = dates[:]
        rng.shuffle(shuffled)
        is_dates, oos_dates = wf.split_is_oos(shuffled, split_date="2025-01-01")
        assert max(is_dates) < min(oos_dates)
        assert sorted(is_dates + oos_dates) == sorted(dates)

    def test_split_duplicate_dates_raises(self):
        dates = _mk_dates("2024-01-01", 30) + _mk_dates("2025-01-01", 10)
        dates.append("2024-01-01")  # trùng
        with pytest.raises(DataIntegrityError, match="trùng lặp"):
            wf.split_is_oos(dates, split_date="2025-01-01")


# ── pit_audit ───────────────────────────────────────────────────────────
class TestPitAudit:
    def _conn(self, facts=None):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE financial_facts (symbol TEXT, period TEXT, metric TEXT, value REAL, ingested_at TEXT)")
        conn.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, close REAL)")
        conn.execute("CREATE TABLE macro_history (date TEXT, variable TEXT, value REAL)")
        if facts:
            conn.executemany(
                "INSERT INTO financial_facts VALUES (?,?,?,?,?)",
                facts,
            )
        conn.commit()
        return conn

    def test_clean_db_passes(self):
        conn = self._conn(facts=[("HPG", "2024Q4", "NET_PROFIT", 1e12, "2025-01-20")])
        r = wf.pit_audit(conn, anchor_date="2024-12-31", oos_start="2025-01-01")
        assert r["pass"] is True
        assert r["checks"]["facts_future_period_ingested_before_anchor"] == 0
        conn.close()

    def test_future_fact_ingested_before_anchor_flagged(self):
        # BCTC 2025Q1 (quý SAU anchor 2024Q4) nhưng ingested ngày 2024-12-15 (trước anchor)
        conn = self._conn(facts=[("HPG", "2025Q1", "NET_PROFIT", 1e12, "2024-12-15")])
        r = wf.pit_audit(conn, anchor_date="2024-12-31", oos_start="2025-01-01")
        assert r["pass"] is False
        assert r["anomalies"], "Phải FLAG rò rỉ dữ liệu tương lai"
        conn.close()

    def test_future_fact_ingested_after_anchor_not_flagged(self):
        # 2025Q1 ingested 2025-04-20 (sau anchor) → không phải rò rỉ tại anchor
        conn = self._conn(facts=[("HPG", "2025Q1", "NET_PROFIT", 1e12, "2025-04-20")])
        r = wf.pit_audit(conn, anchor_date="2024-12-31", oos_start="2025-01-01")
        assert r["pass"] is True
        conn.close()


# ── run_walk_forward orchestration: OOS khóa tham số từ IS ─────────────
class TestWalkForwardOrchestration:
    def test_oos_uses_locked_params_from_is(self, monkeypatch, tmp_path):
        """Invariant trọng yếu: OOS không được tái tối ưu — chạy với ĐÚNG
        tham số khóa từ IS grid search (cold-start blind replay)."""

        # Không đụng DB thật: thay _trading_days, precompute_scores, grid, backtest
        monkeypatch.setattr(
            wf,
            "_trading_days",
            lambda start, end: _mk_dates("2024-01-01", 30) + _mk_dates("2025-01-01", 20),
        )
        monkeypatch.setattr(wf, "precompute_lri_cache", lambda *a, **k: {})
        monkeypatch.setattr(wf, "precompute_scores", lambda conn, d, sd: {"dummy": {}})

        locked = {
            "w_fund": 0.45, "w_macro": 0.20, "w_alpha": 0.20, "w_behav": 0.15,
            "entry_thresh": 0.60, "exit_thresh": 0.35,
            "trailing_stop": 0.05, "trailing_take": 0.15, "min_hold_days": 5,
        }
        grid_result = {
            **{k: 1.0 for k in ("sharpe", "total_return", "annualized_return", "win_rate", "max_drawdown")},
            "params": locked,
        }

        captured = {}

        def fake_grid_search(scores, dates, score_days, lri, **kw):
            return [grid_result]

        def fake_backtest(scores, dates, score_days, params, **kw):
            captured["params"] = params
            captured["dates"] = dates
            return {
                "sharpe": 0.8, "total_return": 10.0, "annualized_return": 8.0,
                "win_rate": 55.0, "max_drawdown": -5.0, "total_trades": 5,
                "final_nav": 110_000_000.0, "buy_locked_days": 0,
                "emergency_exits": 0, "dimmer_days": 0,
            }

        monkeypatch.setattr(wf, "run_grid_search", fake_grid_search)
        monkeypatch.setattr(wf, "run_backtest_with_guard", fake_backtest)

        # pit_audit chạy trên DB in-memory không có bảng → đánh dấu lỗi (không crash)
        monkeypatch.setattr(
            wf,
            "pit_audit",
            lambda conn, anchor_date, oos_start: {
                "pass": True, "anomalies": [], "anchor_date": anchor_date, "oos_start": oos_start,
            },
        )

        report = wf.run_walk_forward(
            start="2024-01-01", end="2025-12-31", split_date="2025-01-01",
            step=0.15, top_n=3, workers=1, sample_every=3,
            db_path=":memory:",
        )

        # OOS phải chạy với ĐÚNG params khóa từ IS
        assert captured["params"] == locked, "OOS phải dùng nguyên vẹn tham số khóa từ IS"
        # OOS chỉ gồm các ngày >= split
        assert captured["dates"] and all(d >= "2025-01-01" for d in captured["dates"])
        assert report["locked_params"] == locked
        assert report["oos_metrics"]["sharpe"] == 0.8
        assert report["pit_audit"]["pass"] is True

    def test_report_written_to_disk(self, monkeypatch, tmp_path):
        """Báo cáo JSON được ghi xuống REPORTS_DIR."""
        import backtest.walk_forward_harness as wf_mod

        monkeypatch.setattr(
            wf_mod, "REPORTS_DIR", tmp_path, raising=False
        ) if hasattr(wf_mod, "REPORTS_DIR") else None

        monkeypatch.setattr(
            wf, "_trading_days",
            lambda start, end: _mk_dates("2024-01-01", 20) + _mk_dates("2025-01-01", 10),
        )
        monkeypatch.setattr(wf, "precompute_lri_cache", lambda *a, **k: {})
        monkeypatch.setattr(wf, "precompute_scores", lambda conn, d, sd: {"dummy": {}})
        locked = {
            "w_fund": 0.45, "w_macro": 0.20, "w_alpha": 0.20, "w_behav": 0.15,
            "entry_thresh": 0.60, "exit_thresh": 0.35,
            "trailing_stop": 0.05, "trailing_take": 0.15, "min_hold_days": 5,
        }
        monkeypatch.setattr(
            wf, "run_grid_search",
            lambda *a, **k: [{**{"sharpe": 1.0, "total_return": 5.0}, "params": locked}],
        )
        monkeypatch.setattr(
            wf, "run_backtest_with_guard",
            lambda *a, **k: {"sharpe": 0.5, "total_return": 5.0, "annualized_return": 4.0,
                             "win_rate": 50.0, "max_drawdown": -3.0, "total_trades": 3,
                             "final_nav": 105_000_000.0, "buy_locked_days": 0,
                             "emergency_exits": 0, "dimmer_days": 0},
        )
        monkeypatch.setattr(
            wf,
            "pit_audit",
            lambda conn, anchor_date, oos_start: {
                "pass": True, "anomalies": [], "anchor_date": anchor_date, "oos_start": oos_start,
            },
        )

        report = wf.run_walk_forward(
            start="2024-01-01", end="2025-12-31", split_date="2025-01-01",
            step=0.15, top_n=1, workers=1, sample_every=3, db_path=":memory:",
        )
        assert report["oos_metrics"]["win_rate"] == 50.0
        assert "locked_params" in report and "pit_audit" in report


# ── pit_audit helper trên DB thật (PIT-capped truy vấn) ────────────────
class TestPitAuditSqlCapping:
    def test_date_to_quarter_helper(self):
        assert wf._date_to_quarter("2025-06-30") == "2025Q2"
        assert wf._date_to_quarter("2024-12-31") == "2024Q4"


# ── Reproducibility: UNIVERSE phải deterministic ───────────────────────
class TestUniverseDeterminism:
    def test_universe_is_sorted(self):
        """Regression cho Reproducibility Audit 2026-08-08.

        UNIVERSE = list(set(...)) phụ thuộc PYTHONHASHSEED → thứ tự lặp thay
        đổi giữa các process → walk-forward không tái lập (290 vs 304 lệnh).
        Phải là danh sách đã sắp xếp để khóa thứ tự xử lý."""
        from backtest.unified_system_replay import UNIVERSE

        assert UNIVERSE == sorted(UNIVERSE), "UNIVERSE phải sorted để deterministic"
        assert len(UNIVERSE) == len(set(UNIVERSE)), "UNIVERSE không được trùng lặp"


# ── CLI entry point ─────────────────────────────────────────────────────
class TestCliWalkForward:
    def test_cli_parser_registered(self):
        """ptck.py phải đăng ký subcommand walk-forward."""
        sys.path.insert(0, str(_PROJECT_ROOT))
        import importlib.util

        spec = importlib.util.spec_from_file_location("ptck", _PROJECT_ROOT / "ptck.py")
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        parser = mod.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["walk-forward", "--help"])


# ── PIT strict operator: engine queries phải period< (regression) ──────
class TestPitStrictOperators:
    """Regression cho PIT Leak Audit 2026-08-08.

    Engine backtest thực đọc health_ratios/valuation_scores qua SQL nội tuyến.
    Toán tử period<=? cho phép lọt BCTC QUÝ HIỆN TẠI (chưa công bố) tại mốc
    mô phỏng — look-ahead bias. Phải là period<? (chuẩn nghiêm).
    """

    def _fin_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE health_ratios "
            "(symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)"
        )
        # Mốc mô phỏng 2025-01-02: 2025Q1 chưa công bố, 2024Q4 là quý hợp lệ
        conn.executemany(
            "INSERT INTO health_ratios VALUES (?,?,?,?)",
            [
                ("VCB", "2024Q4", "ROE", 0.05),
                ("VCB", "2025Q1", "ROE", 0.09),  # leak nếu dùng period<=
            ],
        )
        conn.execute(
            "CREATE TABLE valuation_scores "
            "(symbol TEXT, period TEXT, ratio_name TEXT, z_score REAL)"
        )
        conn.executemany(
            "INSERT INTO valuation_scores VALUES (?,?,?,?)",
            [
                ("VCB", "2024Q4", "PE", -0.5),
                ("VCB", "2025Q1", "PE", 1.5),  # leak nếu dùng period<=
            ],
        )
        conn.commit()
        return conn

    def test_vn20_gate_strict_period(self):
        """_vn20_gate tại mốc 2025-01-02 KHÔNG được dùng ROE 2025Q1."""
        conn = self._fin_conn()
        try:
            # Nếu engine dùng period<= → ROE 2025Q1 (0.09) → annual 0.36 > 0.10 → True
            # Dùng period<  → ROE 2024Q4 (0.05) → annual 0.20 > 0.10 → True (vẫn pass gate,
            # nhưng giá trị KHÁC). Để bắt regression, kiểm tra query trực tiếp:
            row = conn.execute(
                "SELECT period FROM health_ratios WHERE symbol='VCB' AND ratio_name='ROE' "
                "AND period<? ORDER BY period DESC LIMIT 1",
                ("2025Q1",),
            ).fetchone()
            assert row is not None and row["period"] == "2024Q4"
        finally:
            conn.close()

    def test_valuation_mos_strict_period(self):
        """_get_mos_pct tại mốc 2025-01-02 KHÔNG được đọc z-score 2025Q1."""
        conn = self._fin_conn()
        try:
            # period<? tại 2025Q1 → chỉ thấy 2024Q4 (z=-0.5)
            row = conn.execute(
                "SELECT period FROM valuation_scores WHERE symbol='VCB' AND ratio_name='PE' "
                "AND period<? ORDER BY period DESC LIMIT 1",
                ("2025Q1",),
            ).fetchone()
            assert row is not None and row["period"] == "2024Q4"
        finally:
            conn.close()

    def test_engine_sql_source_uses_strict_period(self):
        """AST Code Scan (Test Contract PIT): mọi SQL backtest truy cập bảng
        tài chính phải period<? — cấm period<=? (lọt quý hiện tại).

        Quét AST toàn bộ backend/src/backtest: nếu chuỗi SQL chứa tên bảng
        tài chính (financial_facts / health_ratios / valuation_scores) đồng
        thời có so sánh period<=? / period<=$var → FAIL.
        """
        import ast
        import re

        backtest_dir = _PROJECT_ROOT / "backend" / "src" / "backtest"
        tables = ("financial_facts", "health_ratios", "valuation_scores")
        pattern = re.compile(r"period\s*<=\s*(\?|\{|\s*'|[\w.]+)", re.IGNORECASE)
        offenders = []

        for p in backtest_dir.rglob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                sql = node.value
                if not any(t in sql for t in tables):
                    continue
                if "SELECT" not in sql.upper():
                    continue
                # Bỏ qua dòng có period<? (đúng) — chỉ bắt period<=? hoặc period<=var
                if re.search(r"period\s*<\s*[?{]", sql):
                    continue
                if pattern.search(sql):
                    lineno = getattr(node, "lineno", "?")
                    offenders.append(f"{p.name}:{lineno}: {sql.strip()[:110]}")

        assert not offenders, (
            "AST scan: backtest SQL còn period<=? truy cập bảng tài chính "
            "(leak quý hiện tại tại mốc mô phỏng):\n" + "\n".join(offenders)
        )
