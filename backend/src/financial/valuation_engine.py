"""valuation_engine.py — GIAI ĐOẠN 3: Valuation Discount Engine

Tầng 3 của Kiến trúc 4 Lớp Bất biến.
Tính Z-score và Percentile định giá (P/E, P/B, EV/EBITDA, P/S)
từ dữ liệu giá thực tế (screener_cache.db) + dữ liệu tài chính (financial_facts.db).
"""

# WHY: Module này trả lời "đắt hay rẻ so với lịch sử của CHÍNH cổ phiếu đó" — không dùng
# ngưỡng tuyệt đối (vd P/E<10 là rẻ) vì mỗi cổ phiếu có biên lợi nhuận và vòng đời khác
# nhau. Dùng z-score (lệch bao nhiêu sigma so với mean lịch sử) để đo độ hiếm của mức giá,
# và percentile (thứ hạng) làm thước đo bổ sung chống outlier. Cả 2 bám vào chuỗi thời
# gian nội tại của symbol nên so sánh được giữa các ngành không đồng nhất.

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
sys.path.insert(0, str(BACKEND_DIR))

from src.financial.financial_facts import FinancialFactsDB

SCREENER_DB_PATH = DATA_DIR / "screener_cache.db"

QUARTER_END_MAP = {
    1: (3, 31),  # Q1 ends March 31
    2: (6, 30),  # Q2 ends June 30
    3: (9, 30),  # Q3 ends Sep 30
    4: (12, 31),  # Q4 ends Dec 31
}
# WHY: Map kỳ tài chính (2026Q1) → ngày cuối quý để lấy giá đóng cửa "sát" thời điểm báo
# cáo tài chính: valuation ratio phải dùng giá tại ngày số liệu công bố, không phải giá
# hiện tại, nếu không z-score sẽ bị méo do chênh lệch thời gian giữa báo cáo và giá.

# WHY: Metadata từng ratio gồm ngưỡng z-score (ultra_cheap/cheap/expensive/ultra_expensive).
# Ngưỡng ±2σ cho "quá rẻ/quá đắt" (cơ hội/risk lớn), ±1σ cho mức lệch đáng chú ý — chọn
# dựa trên quy tắc 68-95-99 của phân phối chuẩn để vùng FAIR (±1σ) bao ~68% quan sát lịch
# sử, tránh gắn nhãn cực đoan cho biến động thông thường.
VALUATION_RATIOS = {
    "PE": {
        "name": "P/E",
        "formula": "Price / TTM EPS",
        "description": "Giá trên lợi nhuận mỗi cổ phần",
        "ultra_cheap": 2.0,  # z < -2
        "cheap": 1.0,
        "expensive": 1.0,  # z > 1
        "ultra_expensive": 2.0,
    },
    "PB": {
        "name": "P/B",
        "formula": "Price / Book Value Per Share",
        "description": "Giá trên giá trị sổ sách",
        "ultra_cheap": 2.0,
        "cheap": 1.0,
        "expensive": 1.0,
        "ultra_expensive": 2.0,
    },
    "EV_EBITDA": {
        "name": "EV/EBITDA",
        "formula": "(Market Cap + Total Debt - Cash) / EBITDA",
        "description": "Giá trị doanh nghiệp trên EBITDA",
        "ultra_cheap": 2.0,
        "cheap": 1.0,
        "expensive": 1.0,
        "ultra_expensive": 2.0,
    },
    "PS": {
        "name": "P/S",
        "formula": "Price / Revenue Per Share",
        "description": "Giá trên doanh thu mỗi cổ phần",
        "ultra_cheap": 2.0,
        "cheap": 1.0,
        "expensive": 1.0,
        "ultra_expensive": 2.0,
    },
}


class ValuationEngine:
    """Tính định giá, Z-score, percentile từ dữ liệu giá + tài chính."""

    def __init__(self, facts_db: FinancialFactsDB = None):
        self.facts_db = facts_db or FinancialFactsDB()
        self.db_path = self.facts_db.db_path

    def connect(self):
        return sqlite3.connect(str(self.db_path))

    def screener_connect(self):
        return sqlite3.connect(str(SCREENER_DB_PATH))

    def init_schema(self):
        conn = self.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS valuation_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_quarter INTEGER,
                entity_type TEXT NOT NULL,
                ratio_name TEXT NOT NULL,
                ratio_value REAL,
                z_score REAL,
                percentile REAL,
                mean REAL,
                std REAL,
                count INTEGER,
                zone TEXT,
                price REAL,
                UNIQUE(symbol, period, ratio_name)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_valuation_symbol
            ON valuation_scores(symbol, period)
        """)
        for col_sql in [
            "ALTER TABLE valuation_scores ADD COLUMN z_score_peer REAL",
            "ALTER TABLE valuation_scores ADD COLUMN zone_peer TEXT",
            "ALTER TABLE valuation_scores ADD COLUMN peer_group TEXT",
            "ALTER TABLE valuation_scores ADD COLUMN peer_count INTEGER",
            "ALTER TABLE valuation_scores ADD COLUMN z_score_ts REAL",
            "ALTER TABLE valuation_scores ADD COLUMN zone_ts TEXT",
            "ALTER TABLE valuation_scores ADD COLUMN mean_5y REAL",
            "ALTER TABLE valuation_scores ADD COLUMN std_5y REAL",
            "ALTER TABLE valuation_scores ADD COLUMN count_5y INTEGER",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
                pass
        conn.commit()
        conn.close()
        print("  Schema OK: valuation_scores table (incl. peer-group columns)")

    def _quarter_end_date(self, year: int, quarter: int) -> str:
        m, d = QUARTER_END_MAP[quarter]
        return f"{year:04d}-{m:02d}-{d:02d}"

    def _get_price_at_date(self, symbol: str, target_date: str) -> float | None:
        """Lấy close price gần nhất với target_date (tìm backward)."""
        conn = self.screener_connect()
        cur = conn.cursor()
        target = datetime.strptime(target_date, "%Y-%m-%d")
        # Search backward up to 30 days
        # WHY: Tìm ngược tối đa 30 ngày vì ngày cuối quý có thể rơi vào cuối tuần/lễ (TT chứng
        # khoán VN nghỉ, không có dữ liệu); lấy phiên giao dịch gần nhất về trước để giá phản
        # ánh đúng thời điểm báo cáo. Fallback tới giá mới nhất khi chuỗi quá ngắn.
        for days_back in range(31):
            d = (target - timedelta(days=days_back)).strftime("%Y-%m-%d")
            cur.execute(
                "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?",
                (symbol.upper(), d),
            )
            row = cur.fetchone()
            if row:
                conn.close()
                return row[0]
        # Fallback: latest available
        cur.execute(
            "SELECT date, close FROM daily_ohlcv WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (symbol.upper(),),
        )
        row = cur.fetchone()
        conn.close()
        return row[1] if row else None

    def _get_price_history(self, symbol: str) -> dict[str, float]:
        """Get all historical prices: {date: close}."""
        conn = self.screener_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT date, close FROM daily_ohlcv WHERE symbol=? ORDER BY date",
            (symbol.upper(),),
        )
        result = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return result

    def _get_market_cap(self, price: float, shares_out: float) -> float | None:
        if price and shares_out:
            return price * shares_out
        return None

    def compute_ratios_for_period(self, symbol: str, period: str, period_metrics: dict, entity_type: str) -> dict:
        """Tính các valuation ratios cho 1 kỳ."""
        fy = period_metrics.get("_fiscal_year", int(period[:4]))
        fq = period_metrics.get("_fiscal_quarter", int(period[5:6]))
        qend = self._quarter_end_date(fy, fq)
        price = self._get_price_at_date(symbol, qend)
        if not price:
            return {}

        eps = period_metrics.get("EPS")
        shares = period_metrics.get("SHARES_OUT")
        bvps = period_metrics.get("BOOK_VALUE_PS")
        # Fallback BVPS = Equity / Shares
        if not bvps and shares:
            eq = period_metrics.get("TOTAL_EQUITY")
            if eq:
                bvps = eq / shares
        revenue = period_metrics.get("REVENUE") or period_metrics.get("NII")
        ebitda = period_metrics.get("EBITDA")
        cash = period_metrics.get("CASH_EQUIV") or period_metrics.get("CASH_AND_BALANCES")
        debt = period_metrics.get("TOTAL_DEBT")

        mc = self._get_market_cap(price, shares)
        result = {"_price": price, "_mc": mc}
        quarters = 4

        # PE
        # WHY: dữ liệu là theo quý nên nhân 4 để annualize thành TTM EPS — so sánh P/E cùng
        # thang "1 năm" giữa các kỳ; chỉ tính khi EPS>0 vì EPS âm làm P/E vô nghĩa (giá trị
        # âm không thể hiện "rẻ" hay "đắt").
        if eps and eps > 0:
            ttm_eps = eps * (quarters / 1)  # quarterly → annualized
            result["PE"] = price / ttm_eps

        # PB
        if bvps and bvps > 0:
            result["PB"] = price / bvps

        # ROE (annualized for quarterly period)
        # WHY: FairMultipleEngine (Gordon Growth Model) cần chỉ số ROE để tính Fair P/B
        # và Margin of Safety (MoS). Lưu trữ ROE vào valuation_scores cho mọi doanh nghiệp.
        ni = period_metrics.get("NET_INCOME") or period_metrics.get("NET_PROFIT")
        eq = period_metrics.get("TOTAL_EQUITY")
        if ni and eq and eq > 0:
            result["ROE"] = (ni * 4) / eq

        # PS (use revenue for standard, NII for bank as proxy)
        if revenue and shares:
            rev_ps = revenue / shares
            if rev_ps > 0:
                result["PS"] = price / rev_ps

        # EV/EBITDA
        # WHY: EV = Market Cap + Tổng nợ − Tiền: giá trị "mua lại toàn bộ doanh nghiệp đã
        # trừ tiền mặt" — chuẩn hoá P/E bị bóp méo bởi cấu trúc vốn (nợ cao làm EPS thấp
        # → P/E cao giả tạo), nên EV/EBITDA so sánh được giữa các công ty vay nợ khác nhau.
        if mc and ebitda and ebitda > 0:
            ev = mc
            if debt:
                ev += debt
            if cash:
                ev -= cash
            if ev > 0:
                result["EV_EBITDA"] = ev / ebitda

        return result

    def _compute_stats(self, values: list[float]) -> tuple[float, float, int]:
        n = len(values)
        if n < 2:
            return 0.0, 0.0, n
        # WHY: dùng phương sai mẫu (chia n−1) thay vì tổng thể: chuỗi ratios chỉ là MẪU của
        # toàn bộ phân phối giá trị của cổ phiếu, n−1 cho ước lượng std không thiên lệch —
        # z-score tính từ std này đáng tin hơn khi số kỳ còn ít (4–12 quý).
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = variance**0.5
        return mean, std, n

    def _percentile(self, values: list[float], current: float) -> float:
        if not values:
            return 50.0
        # WHY: percentile dạng "đếm ≤ hiện tại / tổng" (inclusive rank): đơn giản, median-robust,
        # không cần giả định phân phối chuẩn — bổ sung cho z-score vì ratio định giá thường
        # lệch phải (outlier P/E rất lớn) làm mean/std bị kéo lệch.
        sorted_vals = sorted(values)
        count_below = sum(1 for v in sorted_vals if v <= current)
        return (count_below / len(sorted_vals)) * 100.0

    def _classify_zone(self, ratio_name: str, z_score: float) -> str:
        meta = VALUATION_RATIOS.get(ratio_name, {})
        if z_score <= -meta.get("ultra_cheap", 2.0):
            return "ULTRA_CHEAP"
        elif z_score <= -meta.get("cheap", 1.0):
            return "CHEAP"
        elif z_score >= meta.get("ultra_expensive", 2.0):
            return "ULTRA_EXPENSIVE"
        elif z_score >= meta.get("expensive", 1.0):
            return "EXPENSIVE"
        else:
            return "FAIR"

    def compute_valuation(self, symbol: str) -> dict:
        """Main entry: fetch facts + prices, compute all valuation ratios + z-scores."""
        entity_type = self.facts_db.get_entity_type(symbol)
        facts = self.facts_db.get_facts(symbol)
        if not facts:
            return {"status": "NO_DATA", "symbol": symbol}

        # For each period, compute valuation ratios
        all_period_ratios = {}  # {period: {ratio_name: value}}
        for period in sorted(facts.keys()):
            pm = facts[period]
            ratios = self.compute_ratios_for_period(symbol, period, pm, entity_type)
            if ratios:
                all_period_ratios[period] = ratios

        if not all_period_ratios:
            return {"status": "NO_PRICE", "symbol": symbol}

        # For each ratio type, compute stats + z-scores + percentiles
        ratio_names = ["PE", "PB", "PS", "EV_EBITDA", "ROE"]
        conn = self.connect()
        results = []
        latest_price = None

        for rname in ratio_names:
            # Collect all values across periods
            # WHY: z-score tính theo CHUỖI LỊCH SỬ nội tại của chính symbol (mean/std của mọi
            # kỳ) thay vì ngưỡng tĩnh — mục tiêu là phát hiện "symbol đang rẻ hơn bình thường
            # của chính nó", phù hợp cả với cổ phiếu tăng trưởng cao vốn có P/E luôn lớn.
            period_values = []
            for period in sorted(all_period_ratios.keys()):
                v = all_period_ratios[period].get(rname)
                if v is not None and v > 0:
                    period_values.append((period, v))

            if len(period_values) < 1:
                continue

            vals = [v for _, v in period_values]
            mean, std, n = self._compute_stats(vals)

            for period, val in period_values:
                z = (val - mean) / std if std > 0 else 0.0
                pct = self._percentile(vals, val)
                zone = self._classify_zone(rname, z)
                price = all_period_ratios[period].get("_price")
                if price:
                    latest_price = price

                fy = int(period[:4])
                fq = int(period[5:6])
                conn.execute(
                    """
                    INSERT OR REPLACE INTO valuation_scores
                        (symbol, period, fiscal_year, fiscal_quarter,
                         entity_type, ratio_name, ratio_value, z_score,
                         percentile, mean, std, count, zone, price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        symbol.upper(),
                        period,
                        fy,
                        fq,
                        entity_type.upper(),
                        rname,
                        val,
                        round(z, 4),
                        round(pct, 2),
                        round(mean, 4),
                        round(std, 4),
                        n,
                        zone,
                        price,
                    ),
                )
                results.append(
                    {
                        "period": period,
                        "ratio": rname,
                        "value": val,
                        "z_score": round(z, 2),
                        "percentile": round(pct, 1),
                        "zone": zone,
                    }
                )

        conn.commit()
        conn.close()

        if not results:
            return {"status": "NO_VALUATION_RESULTS", "symbol": symbol.upper()}

        # Summary: latest period zones
        latest_period = sorted(results, key=lambda r: r["period"])[-1]["period"]
        latest_ratios = [r for r in results if r["period"] == latest_period]
        zones = {r["ratio"]: r["zone"] for r in latest_ratios}
        has_ultra_cheap = any(z == "ULTRA_CHEAP" for z in zones.values())
        has_cheap = any(z in ("ULTRA_CHEAP", "CHEAP") for z in zones.values())

        return {
            "status": "DONE",
            "symbol": symbol.upper(),
            "entity_type": entity_type,
            "latest_price": latest_price,
            "latest_period": latest_period,
            "periods_computed": len(all_period_ratios),
            "total_entries": len(results),
            "zones": zones,
            "has_ultra_cheap": has_ultra_cheap,
            "has_cheap": has_cheap,
            "details": results,
        }

    def get_valuation(self, symbol: str) -> dict | None:
        """Lấy valuation scores từ DB."""
        conn = self.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT period, ratio_name, ratio_value, z_score,
                   percentile, zone, price
            FROM valuation_scores
            WHERE symbol = ?
            ORDER BY period DESC, ratio_name
        """,
            (symbol.upper(),),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None

        result = {"symbol": symbol.upper(), "ratios": []}
        for r in rows:
            result["ratios"].append(
                {
                    "period": r[0],
                    "ratio": r[1],
                    "value": r[2],
                    "z_score": r[3],
                    "percentile": r[4],
                    "zone": r[5],
                    "price": r[6],
                }
            )
        return result

    def get_latest_valuation(self, symbol: str) -> dict | None:
        """Latest period valuation summary."""
        conn = self.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT period, ratio_name, ratio_value, z_score,
                   percentile, zone, price
            FROM valuation_scores
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM valuation_scores WHERE symbol = ?
            )
            ORDER BY ratio_name
        """,
            (symbol.upper(), symbol.upper()),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        period = rows[0][0]
        price = rows[0][6]
        return {
            "symbol": symbol.upper(),
            "period": period,
            "price": price,
            "ratios": [{"ratio": r[1], "value": r[2], "z_score": r[3], "percentile": r[4], "zone": r[5]} for r in rows],
        }

    def compare_valuations(self, symbols: list[str]) -> dict:
        return {sym: self.get_latest_valuation(sym) for sym in symbols}


# =========================================================================
# CLI
# =========================================================================


def print_valuation_table(data: dict, symbols: list[str]):
    print(f"\n  {'=' * 80}")
    print("  ĐỊNH GIÁ SO SÁNH — Q2/2026 (latest period)")
    print(f"  {'=' * 80}")

    # Collect all ratio names
    ratio_names = ["PE", "PB", "PS", "EV_EBITDA"]
    metric_headers = ["P/E", "P/B", "P/S", "EV/EBITDA"]

    print(f"  {'Metric':<15}", end="")
    for sym in symbols:
        print(f" {sym:>14s}", end="")
    print()

    col_width = 15
    total_width = col_width + 15 * len(symbols) + len(symbols)
    print(f"  {'-' * total_width}")

    for i, ratio in enumerate(ratio_names):
        print(f"  {metric_headers[i]:<15}", end="")
        for sym in symbols:
            d = data.get(sym)
            if d and d.get("ratios"):
                found = [r for r in d["ratios"] if r["ratio"] == ratio]
                if found:
                    r = found[0]
                    val = r["value"]
                    z = r["z_score"]
                    zone = r["zone"]
                    icons = {
                        "ULTRA_CHEAP": "⬇⬇",
                        "CHEAP": "⬇",
                        "FAIR": "●",
                        "EXPENSIVE": "⬆",
                        "ULTRA_EXPENSIVE": "⬆⬆",
                    }
                    ico = icons.get(zone, "?")
                    if val < 10:
                        vs = f"{val:.2f}"
                    elif val < 100:
                        vs = f"{val:.1f}"
                    else:
                        vs = f"{val:.0f}"
                    print(f" {ico} {vs:>7s} (z={z:+.1f})", end=" ")
                else:
                    print(f"  {'---':>12s}", end=" ")
            else:
                print(f"  {'---':>12s}", end=" ")
        print()

    # Zone summary
    print(f"\n  {'ZONE LEGEND':<15}", end="")
    print("  ⬇⬇ Ultra Cheap (z<-2)  ⬇ Cheap (z<-1)  ● Fair  ⬆ Expensive  ⬆⬆ Ultra Expensive")

    # Ultra cheap detection
    print(f"\n  {'⬇⬇ ULTRA CHEAP SIGNAL':<15}", end="")
    found = False
    for sym in symbols:
        d = data.get(sym)
        if d and d.get("ratios"):
            ultra = [r for r in d["ratios"] if r.get("zone") == "ULTRA_CHEAP"]
            cheap = [r for r in d["ratios"] if r.get("zone") == "CHEAP"]
            if ultra or cheap:
                print(f"\n    {sym}: ", end="")
                for r in ultra + cheap:
                    print(f"{r['ratio']}({r['zone']}) ", end="")
                found = True
    if not found:
        print(" None")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Valuation Engine — PTCK_VN Phase 3")
    parser.add_argument("action", choices=["init", "compute", "show", "compare"], help="Hành động")
    parser.add_argument("--symbols", nargs="+", default=["FPT", "ACB", "HDB", "MBB", "VCB"], help="Danh sách symbol")
    args = parser.parse_args()

    engine = ValuationEngine()

    if args.action == "init":
        print("=== Khởi tạo valuation_scores table ===")
        engine.init_schema()

    elif args.action == "compute":
        print(f"=== Compute Valuation: {', '.join(args.symbols)} ===")
        engine.init_schema()
        results = {}
        for sym in args.symbols:
            print(f"\n  [{sym}]...")
            r = engine.compute_valuation(sym)
            results[sym] = r
            if r["status"] == "DONE":
                zones_str = " ".join(f"{k}={v}" for k, v in r.get("zones", {}).items())
                print(
                    f"  [{r['status']}] {sym}: price={r.get('latest_price')} "
                    f"| {r.get('latest_period')} | {r.get('periods_computed')} periods "
                    f"| {r.get('total_entries')} entries | {zones_str}"
                )
            else:
                print(f"  [{r['status']}] {sym}")

        # Summary table
        data = {sym: engine.get_latest_valuation(sym) for sym in args.symbols}
        print_valuation_table(data, args.symbols)

    elif args.action == "show":
        for sym in args.symbols:
            v = engine.get_latest_valuation(sym)
            if not v:
                print(f"\n  {sym}: NO DATA")
                continue
            print(f"\n  {'=' * 50}")
            print(f"  {sym} @ {v.get('price', 'N/A'):,} VND ({v['period']})")
            print(f"  {'=' * 50}")
            icons = {"ULTRA_CHEAP": "⬇⬇", "CHEAP": "⬇", "FAIR": "●", "EXPENSIVE": "⬆", "ULTRA_EXPENSIVE": "⬆⬆"}
            for r in v["ratios"]:
                ico = icons.get(r["zone"], "?")
                zs = f"z={r['z_score']:+.2f}"
                pct = f"p{r['percentile']:.0f}%"
                print(f"    {ico} {r['ratio']:12s} = {r['value']:<10.2f}  {zs:>8s}  {pct:>6s}  ({r['zone']})")

    elif args.action == "compare":
        data = {sym: engine.get_latest_valuation(sym) for sym in args.symbols}
        print_valuation_table(data, args.symbols)


if __name__ == "__main__":
    main()
