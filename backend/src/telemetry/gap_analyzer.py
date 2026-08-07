import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import sqlite3


def _get_db_path():
    return str(backend_dir / "data" / "screener_cache.db")


class GapAnalyzer:
    def __init__(self, conn, lookback_months=3):
        self.conn = conn
        self.lookback_months = lookback_months
        self._cutoff = None
        self._trading_dates = None
        self._expected_symbols = None

    @classmethod
    def create(cls, lookback_months=3):
        conn = sqlite3.connect(_get_db_path(), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return cls(conn, lookback_months)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _get_cutoff(self):
        if self._cutoff is not None:
            return self._cutoff
        max_date = self.conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
        if max_date is None:
            return datetime.now().strftime("%Y-%m-%d")
        max_dt = datetime.strptime(max_date, "%Y-%m-%d")
        cutoff_dt = max_dt - timedelta(days=self.lookback_months * 30)
        self._cutoff = cutoff_dt.strftime("%Y-%m-%d")
        return self._cutoff

    def get_trading_dates(self):
        if self._trading_dates is not None:
            return self._trading_dates
        cutoff = self._get_cutoff()
        rows = self.conn.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' AND date >= ? ORDER BY date", (cutoff,)
        ).fetchall()
        self._trading_dates = [r[0] for r in rows]
        return self._trading_dates

    def get_expected_symbols(self):
        if self._expected_symbols is not None:
            return self._expected_symbols
        cutoff = self._get_cutoff()
        from_db = set()
        rows = self.conn.execute(
            "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30') AND date >= ?", (cutoff,)
        ).fetchall()
        for r in rows:
            from_db.add(r[0])
        from_industry = set()
        try:
            rows = self.conn.execute("SELECT DISTINCT symbol FROM symbol_industry").fetchall()
            for r in rows:
                from_industry.add(r[0])
        except Exception:
            pass
        merged = from_db | from_industry
        self._expected_symbols = sorted(merged)
        return self._expected_symbols

    def get_actual_symbols(self, target_date):
        rows = self.conn.execute(
            "SELECT DISTINCT symbol FROM daily_ohlcv WHERE date=? AND symbol NOT IN ('VNINDEX', 'VN30')", (target_date,)
        ).fetchall()
        return set(r[0] for r in rows)

    def analyze(self):
        trading_dates = self.get_trading_dates()
        expected_set = set(self.get_expected_symbols())
        expected_count = len(expected_set)

        gaps = {}
        complete_gaps = []

        for dt in trading_dates:
            actual = self.get_actual_symbols(dt)
            actual_count = len(actual)

            if actual_count == 0:
                complete_gaps.append(dt)
                gaps[dt] = sorted(expected_set)
                continue

            deficit_pct = (expected_count - actual_count) / expected_count * 100
            if deficit_pct >= 10:
                missing = sorted(expected_set - actual)
                gaps[dt] = missing

        result = {
            "generated_at": datetime.now().isoformat(),
            "config": {
                "lookback_months": self.lookback_months,
                "cutoff_date": self._get_cutoff(),
            },
            "trading_dates_range": [trading_dates[0], trading_dates[-1]] if trading_dates else [],
            "total_trading_dates": len(trading_dates),
            "expected_symbols_count": expected_count,
            "gap_summary": {
                "dates_with_gaps": len(gaps),
                "complete_gap_dates": len(complete_gaps),
                "total_missing_slots": sum(len(v) for v in gaps.values()),
            },
            "missing": gaps,
        }

        max_density = 0
        max_density_date = None
        for dt in trading_dates:
            actual = self.get_actual_symbols(dt)
            cnt = len(actual)
            if cnt > max_density:
                max_density = cnt
                max_density_date = dt

        result["max_density"] = {"date": max_density_date, "symbols": max_density}
        if expected_count > 0:
            result["max_density_pct"] = round(max_density / expected_count * 100, 1)
        else:
            result["max_density_pct"] = 0.0

        return result


def run_gap_analyzer(output_path=None, lookback_months=3):
    if output_path is None:
        output_path = PROJECT_ROOT / "backend" / "data" / "missing_manifest.json"

    analyzer = GapAnalyzer.create(lookback_months)
    try:
        result = analyzer.analyze()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return result
    finally:
        analyzer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PTCK Gap Analyzer")
    parser.add_argument("--output", type=str, default=None, help="Output path for manifest JSON")
    parser.add_argument("--lookback-months", type=int, default=3, help="Number of months to analyze (default: 3)")
    args = parser.parse_args()

    result = run_gap_analyzer(args.output, args.lookback_months)
    summary = result["gap_summary"]
    config = result["config"]
    density = result.get("max_density")
    print("=" * 60)
    print("  GAP ANALYZER — KIEM TOAN DU LIEU")
    print("=" * 60)
    print(f"  Thoi gian: {config['cutoff_date']} -> {result['trading_dates_range'][1]}")
    print(f"  So ngay giao dich: {result['total_trading_dates']}")
    print(f"  So ky vong: {result['expected_symbols_count']} symbols")
    print(f"  Mat do cao nhat: {density['symbols']} ({result['max_density_pct']}%) vao {density['date']}")
    print(f"  Ngay thieu du lieu: {summary['dates_with_gaps']}")
    print(f"  Ngay mat hoan toan: {summary['complete_gap_dates']}")
    print(f"  Tong o thieu: {summary['total_missing_slots']}")
    print(f"  Xuat manifest: {args.output or PROJECT_ROOT / 'backend' / 'data' / 'missing_manifest.json'}")
    print("=" * 60)
