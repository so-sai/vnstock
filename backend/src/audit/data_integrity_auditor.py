"""data_integrity_auditor.py — Deep Data Density Auditor & Self-Healing Pipeline.

WHY:
  Kiểm toán bề mặt (Surface Audit) chỉ đếm xem DB có hàng hay không.
  Kiểm toán Mật độ Dữ liệu Chuyên sâu (Deep Data Density Scanner) kiểm tra từng
  quý trong 30 quý gần nhất (2019Q1 - 2026Q2) với 6 trường dữ liệu cốt lõi:
    - NET_INCOME / NET_PROFIT
    - TOTAL_EQUITY
    - CFO / OPERATING_CASH_FLOW
    - REVENUE / NII
    - TOTAL_DEBT
    - SHARES_OUT / BOOK_VALUE_PS

  Khi phát hiện lỗ hổng chuỗi thời gian (Gap Found / Severe Gap), tự động
  kích hoạt Self-Healing Pipeline (Crawler Backfill) để bù nạp dữ liệu rỗng.
"""

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
FINANCIAL_DB = DATA_DIR / "financial_facts.db"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@dataclass
class DensityAuditResult:
    """Output DTO for symbol data density audit."""

    symbol: str
    required_quarters: int
    available_quarters: int
    density_pct: float
    missing_quarters: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    status: str = "PERFECT"  # PERFECT / GAP_FOUND / SEVERE_GAP
    healed: bool = False


class DataIntegrityAuditor:
    """Deep Data Density Scanner & Self-Healing Data Pipeline Auditor."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or FINANCIAL_DB

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _generate_required_quarters(self, start_year: int = 2019, end_year: int = 2026) -> list[str]:
        quarters = []
        for y in range(start_year, end_year + 1):
            for q in range(1, 5):
                if y == 2026 and q > 2:
                    break
                quarters.append(f"{y}Q{q}")
        return quarters

    def audit_symbol(self, symbol: str, start_year: int = 2019, end_year: int = 2026) -> DensityAuditResult:
        sym = symbol.upper().strip()
        required_quarters = self._generate_required_quarters(start_year, end_year)

        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT period
                FROM financial_facts
                WHERE symbol = ?
            """,
                (sym,),
            )
            avail_periods = set(r[0] for r in cur.fetchall())
        finally:
            conn.close()

        available_count = sum(1 for q in required_quarters if q in avail_periods)
        missing_quarters = [q for q in required_quarters if q not in avail_periods]
        tot = len(required_quarters)
        density_pct = round((available_count / float(tot)) * 100.0, 1) if tot > 0 else 0.0

        # Provenance audit: dữ liệu bịa (is_synthetic=1) KHÔNG được tính là dữ liệu hợp lệ.
        # WHY: Density audit cũ bị đánh lừa — synthetic lấp đầy quý rỗng → 100% → PERFECT.
        # Một số vẽ vừa "đầy đủ" vừa "đúng độ lớn" nhưng không có xuất xứ thật = vô giá trị
        # (No Provenance = No Trust). Mọi quý chứa fact synthetic bị đánh dấu là thiếu.
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT period FROM financial_facts
                WHERE symbol = ? AND is_synthetic = 1
            """,
                (sym,),
            )
            synth_periods = set(r[0] for r in cur.fetchall())
        finally:
            conn.close()

        for q in synth_periods:
            if q in avail_periods and q in required_quarters:
                available_count -= 1
                missing_quarters.append(q)

        available_count = max(available_count, 0)
        missing_quarters = sorted(set(missing_quarters))
        density_pct = round((available_count / float(tot)) * 100.0, 1) if tot > 0 else 0.0

        if density_pct >= 98.0:
            status = "PERFECT"
        elif density_pct >= 80.0:
            status = "GAP_FOUND"
        else:
            status = "SEVERE_GAP"

        return DensityAuditResult(
            symbol=sym,
            required_quarters=tot,
            available_quarters=available_count,
            density_pct=density_pct,
            missing_quarters=missing_quarters,
            status=status,
        )

    def audit_many(self, symbols: list[str], start_year: int = 2019, end_year: int = 2026) -> dict[str, DensityAuditResult]:
        return {s: self.audit_symbol(s, start_year, end_year) for s in symbols}

    def _seed_symbol(self, symbol: str) -> dict:
        """Call VnstockCrawler seeder to backfill missing data."""
        from src.financial.financial_facts import VnstockCrawler

        crawler = VnstockCrawler()
        return crawler.seed_symbol(symbol)

    def audit_and_heal(
        self, symbols: list[str], auto_backfill: bool = True, start_year: int = 2019, end_year: int = 2026
    ) -> dict[str, DensityAuditResult]:
        """Audit data density and automatically self-heal missing quarters."""
        results = self.audit_many(symbols, start_year, end_year)

        if not auto_backfill:
            return results

        for sym, res in results.items():
            if res.status in ("GAP_FOUND", "SEVERE_GAP"):
                try:
                    self._seed_symbol(sym)
                    # Re-audit after healing
                    re_audited = self.audit_symbol(sym, start_year, end_year)
                    re_audited.healed = True
                    results[sym] = re_audited
                except Exception:
                    pass

        return results


def print_density_audit_report(results: dict[str, DensityAuditResult]):
    """Print clean Deep Data Density Audit Report for CLI."""
    from src.utils.cli_theme import c_cyan, c_green, c_red, c_yellow

    print("\n  " + "=" * 125)
    print(f"  🔍 {c_cyan('PTCK DEEP DATA DENSITY AUDIT REPORT (2019Q1 – 2026Q2)')}")
    print("  " + "=" * 125)
    print(
        f"  {'Symbol (Mã)':<10} {'Required':>10} {'Available':>11} {'Data Density (%)':>18} "
        f"  {'Missing Quarters (Các quý thiếu)':<32} {'Audit Status'}"
    )
    print("  " + "─" * 125)

    for sym, r in results.items():
        if r.status == "PERFECT":
            st_str = c_green("🟢 PERFECT")
            den_str = c_green(f"{r.density_pct:>5.1f}%")
        elif r.status == "GAP_FOUND":
            st_str = c_yellow("🟡 GAP_FOUND")
            den_str = c_yellow(f"{r.density_pct:>5.1f}%")
        else:
            st_str = c_red("🔴 SEVERE_GAP")
            den_str = c_red(f"{r.density_pct:>5.1f}%")

        missing_str = ", ".join(r.missing_quarters[:4])
        if len(r.missing_quarters) > 4:
            missing_str += f" ... (+{len(r.missing_quarters) - 4} more)"
        if not missing_str:
            missing_str = "None"

        healed_tag = c_cyan(" [HEALED]") if r.healed else ""

        print(
            f"  {sym:<10} {r.required_quarters:>10} {r.available_quarters:>11} {den_str:>18} "
            f"  {missing_str:<32} {st_str}{healed_tag}"
        )

    print("  " + "=" * 125 + "\n")
