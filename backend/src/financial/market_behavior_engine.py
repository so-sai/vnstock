"""market_behavior_engine.py — GIAI ĐOẠN 4: Market Behavior Engine

Volume Profile + Active Demand detection.
Xác định vùng giá tích lũy (HVN), Point of Control,
và lực cầu chủ động tại vùng hỗ trợ.
"""

# WHY: Module này dựng "bối cảnh giao dịch" theo khối lượng thay vì theo giá đơn thuần.
# Volume Profile (POC/VAH/VAL/HVN) được chọn thay cho hỗ trợ/kháng cự giá thường thấy vì
# nó phản ánh nơi giao dịch thực sự (dòng tiền lớn để lại dấu vết khối lượng), ít bị
# nhiễu bởi spike giá riêng lẻ. Active Demand scan dùng volume surge + vị trí đóng nến để
# nhận diện lực cầu chủ động sớm tại vùng tích lũy — tín hiệu trước khi giá breakout.

import json
import math
import sqlite3
import sys
from collections import defaultdict
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

SCREENER_DB = DATA_DIR / "screener_cache.db"
FINANCIAL_DB = DATA_DIR / "financial_facts.db"


class MarketBehaviorEngine:
    """Volume Profile + Active Demand Scanner."""

    def __init__(self):
        self.db_path = SCREENER_DB

    def screener_conn(self):
        return sqlite3.connect(str(self.db_path))

    def fin_conn(self):
        return sqlite3.connect(str(FINANCIAL_DB))

    def init_schema(self):
        conn = self.fin_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS volume_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                price_current REAL,
                poc REAL,
                vah REAL,
                val REAL,
                poc_volume REAL,
                total_volume REAL,
                value_area_volume REAL,
                bin_size REAL,
                hvns TEXT,
                price_ma20 REAL,
                price_ma50 REAL,
                price_ma200 REAL,
                volume_ma20 REAL,
                volume_ratio REAL,
                range_pct REAL,
                UNIQUE(symbol, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS active_demand (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                price REAL,
                signal_type TEXT,
                support_level REAL,
                volume_ratio REAL,
                close_position REAL,
                price_change REAL,
                strength REAL,
                metadata TEXT,
                UNIQUE(symbol, date, signal_type)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vp_symbol ON volume_profile(symbol, date)
        """)
        conn.commit()
        conn.close()
        print("  Schema OK: volume_profile + active_demand tables")

    def get_ohlcv(self, symbol: str, days: int = 250) -> list[dict]:
        conn = self.screener_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT date, open, high, low, close, adj_close, volume
            FROM daily_ohlcv
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT ?
        """,
            (symbol.upper(), days),
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in reversed(rows):
            result.append(
                {
                    "date": r[0],
                    "open": r[1],
                    "high": r[2],
                    "low": r[3],
                    "close": r[4],
                    "adj_close": r[5] or r[4],
                    "volume": r[6] or 0,
                }
            )
        return result

    def compute_atr(self, data: list[dict], period: int = 14) -> float:
        if len(data) < period + 1:
            return 0
        trs = []
        for i in range(1, period + 1):
            hl = data[-i]["high"] - data[-i]["low"]
            hc = abs(data[-i]["high"] - data[-i - 1]["close"])
            lc = abs(data[-i]["low"] - data[-i - 1]["close"])
            trs.append(max(hl, hc, lc))
        return sum(trs) / len(trs)

    def compute_volume_profile(self, symbol: str, window: int = 60) -> dict | None:
        data = self.get_ohlcv(symbol, days=window + 200)
        if len(data) < window:
            return None

        profile_data = data[-window:]
        atr = self.compute_atr(profile_data)
        # WHY: bin_size lấy theo ATR rồi làm tròn về hàng trăm, floor 100: bin tự co/giãn theo
        # mức độ biến động của từng cổ phiếu (cổ phiếu dao động mạnh cần bin rộng hơn để
        # tránh phân mảnh vùng giá thành quá nhiều bin rỗng), đồng thời chặn dưới 100 để
        # cổ phiếu giá thấp không tạo bin quá mịn gây nhiễu.
        bin_size = max(round(atr / 10, -2), 100) if atr > 0 else 500

        volume_bins = defaultdict(float)
        total_vol = 0
        for d in profile_data:
            vol = d["volume"]
            if vol <= 0:
                continue
            lo = d["low"]
            hi = d["high"]
            span = hi - lo
            if span <= 0:
                bin_key = round(lo / bin_size) * bin_size
                volume_bins[bin_key] += vol
            else:
                # WHY: Phân bổ khối lượng theo tỉ lệ (vol_per_unit × overlap) thay vì đổ toàn
                # bộ vào bin giá đóng cửa: một nến trải dài qua nhiều bin thì lượng giao dịch
                # ước lượng phân bổ đều theo độ dài giá quét qua — phản ánh chính xác hơn
                # "giá nào thực sự được khớp nhiều" so với gán cứng theo 1 mức giá.
                vol_per_unit = vol / span
                b_lo = math.floor(lo / bin_size) * bin_size
                b_hi = math.ceil(hi / bin_size) * bin_size
                for level in range(int(b_lo), int(b_hi) + int(bin_size), int(bin_size)):
                    if level >= lo - bin_size and level <= hi + bin_size:
                        overlap = min(hi, level + bin_size) - max(lo, level)
                        if overlap > 0:
                            volume_bins[level] += vol_per_unit * overlap
            total_vol += vol

        if not volume_bins:
            return None

        # POC
        poc = max(volume_bins, key=volume_bins.get)
        poc_vol = volume_bins[poc]

        # Sort bins by price
        sorted_bins = sorted(volume_bins.items())
        total_vp_vol = sum(v for _, v in sorted_bins)

        # Value Area: 70% of total volume centered on POC
        # WHY: 70% là convention chuẩn của Market Profile (chuẩn giá trị vùng giao dịch cân
        # bằng): xấp xỉ 68% của phân phối chuẩn. Mở rộng lần lượt về 2 phía từ POC theo bin
        # nào có khối lượng lớn hơn để VA bám sát đám đông giao dịch thực, không phình theo
        # chiều dài giá.
        target_va = total_vp_vol * 0.70
        va_vol = volume_bins[poc]
        vah = poc
        val = poc

        # Expand outward from POC
        sorted_prices = [b[0] for b in sorted_bins]
        poc_idx = sorted_prices.index(poc)
        l_idx = poc_idx - 1
        r_idx = poc_idx + 1

        while va_vol < target_va and (l_idx >= 0 or r_idx < len(sorted_bins)):
            left_vol = volume_bins[sorted_prices[l_idx]] if l_idx >= 0 else 0
            right_vol = volume_bins[sorted_prices[r_idx]] if r_idx < len(sorted_bins) else 0

            if left_vol >= right_vol and l_idx >= 0:
                va_vol += left_vol
                val = sorted_prices[l_idx]
                l_idx -= 1
            elif r_idx < len(sorted_bins):
                va_vol += right_vol
                vah = sorted_prices[r_idx]
                r_idx += 1
            else:
                break

        # High Volume Nodes (HVN): bins with volume > 2x average
        # WHY: Ngưỡng 2× khối lượng trung bình/bin để lọc ra các mức giá "nghẽn" thật sự
        # (được giao dịch vượt trội), loại bỏ nhiễu bin ngẫu nhiên — 2x là điểm cắt đủ cao
        # để chỉ giữ node có ý nghĩa nhưng vẫn bắt được vùng tích lũy rõ rệt.
        avg_bin_vol = total_vp_vol / len(volume_bins)
        hvns = [{"price": p, "volume": v} for p, v in sorted_bins if v > avg_bin_vol * 2]

        latest = data[-1]
        close_prices = [d["close"] for d in data]
        volumes = [d["volume"] for d in profile_data]
        vol_ma20 = sum(volumes[-20:]) / min(20, len(volumes)) if len(volumes) >= 20 else sum(volumes) / len(volumes)
        current_vol = latest["volume"]
        # WHY: volume_ratio = khối lượng hôm nay / MA20 khối lượng để chuẩn hoá "sôi động bất
        # thường" theo baseline riêng từng cổ phiếu (cổ phiếu thanh khoản cao có volume tuyệt
        # đối lớn hơn nhưng ratio mới so sánh được chéo giữa các mã).
        vol_ratio = current_vol / vol_ma20 if vol_ma20 > 0 else 0

        def ma(prices, n):
            if len(prices) < n:
                return None
            return sum(prices[-n:]) / n

        price = latest["close"]
        price_ma20 = ma(close_prices, 20)
        price_ma50 = ma(close_prices, 50)
        price_ma200 = ma(close_prices, 200)

        return {
            "symbol": symbol.upper(),
            "date": latest["date"],
            "price_current": price,
            "poc": poc,
            "vah": vah,
            "val": val,
            "poc_volume": poc_vol,
            "total_volume": total_vp_vol,
            "value_area_volume": va_vol,
            "bin_size": bin_size,
            "hvns": hvns[:5],  # Top 5 HVNs
            "price_ma20": price_ma20,
            "price_ma50": price_ma50,
            "price_ma200": price_ma200,
            "volume_ma20": vol_ma20,
            "volume_ratio": vol_ratio,
            "range_pct": (vah - val) / val * 100 if val > 0 else 0,
        }

    def scan_active_demand(self, symbol: str, lookback: int = 20) -> list[dict]:
        data = self.get_ohlcv(symbol, days=lookback + 60)
        if len(data) < 60:
            return []

        vp = self.compute_volume_profile(symbol)
        if not vp:
            return []

        val = vp["val"]
        vp["vah"]
        poc = vp["poc"]
        vol_ma20 = vp["volume_ma20"]

        signals = []
        # WHY: dùng set + sorted để loại trùng mức giá (VAL có thể trùng HVN/POC) và luôn có
        # danh sách hỗ trợ tăng dần; so sánh khoảng cách tương đối (dist/price) chứ không
        # tuyệt đối để mức hỗ trợ 3% nghĩa tương đương nhau giữa cổ phiếu giá 10k và 200k.
        support_levels = sorted(set([val, poc] + [h["price"] for h in vp.get("hvns", [])]))

        window_data = data[-lookback:]
        for d in window_data:
            price = d["close"]
            low = d["low"]
            vol = d["volume"]
            vol_ratio = vol / vol_ma20 if vol_ma20 > 0 else 0
            day_range = d["high"] - d["low"]
            close_position = (price - low) / day_range if day_range > 0 else 0.5

            # Find nearest support
            nearest_support = None
            min_dist = float("inf")
            for sup in support_levels:
                dist = abs(price - sup) / sup if sup > 0 else 0
                if dist < min_dist:
                    min_dist = dist
                    nearest_support = sup

            # Active demand conditions:
            # 1. Price near support (within 3%)
            # 2. Volume > 1.5x MA20
            # 3. Close in upper 60% of range
            # 4. Positive price change
            # WHY: 4 điều kiện phối hợp để loại bỏ false-positive: giá sát hỗ trợ (3%) đảm bảo
            # đúng vùng nghẽn; volume ≥1.5x MA20 chứng minh dòng tiền đổ vào (không phải thin
            # volume); đóng nến ≥60% biên độ = người mua áp đảo trong phiên; close > open xác
            # nhận áp lực mua ròng thay vì bẫy hồi giá.
            if min_dist <= 0.03 and vol_ratio >= 1.5 and close_position >= 0.6 and price > d.get("open", price):
                # WHY: strength = tổng có trọng số các yếu tố độc lập (volume 0.4, close
                # position 0.3, độ sát hỗ trợ 0.3) — volume là tín hiệu mạnh nhất nên nặng
                # nhất; cộng dồn để có thang so sánh giữa các tín hiệu khác ngày/mã.
                strength = vol_ratio * 0.4 + close_position * 0.3 + (1 - min_dist) * 0.3
                signals.append(
                    {
                        "symbol": symbol.upper(),
                        "date": d["date"],
                        "price": price,
                        "signal_type": "ACTIVE_DEMAND",
                        "support_level": nearest_support,
                        "volume_ratio": round(vol_ratio, 2),
                        "close_position": round(close_position, 2),
                        "price_change": round((price - d["open"]) / d["open"] * 100, 2) if d["open"] > 0 else 0,
                        "strength": round(strength, 2),
                    }
                )

        return signals

    def scan_symbol(self, symbol: str) -> dict:
        vp = self.compute_volume_profile(symbol)
        if not vp:
            return {"status": "NO_DATA", "symbol": symbol}

        signals = self.scan_active_demand(symbol)
        conn = self.fin_conn()

        conn.execute(
            """
            INSERT OR REPLACE INTO volume_profile
                (symbol, date, price_current, poc, vah, val,
                 poc_volume, total_volume, value_area_volume,
                 bin_size, hvns, price_ma20, price_ma50, price_ma200,
                 volume_ma20, volume_ratio, range_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                symbol.upper(),
                vp["date"],
                vp["price_current"],
                vp["poc"],
                vp["vah"],
                vp["val"],
                vp["poc_volume"],
                vp["total_volume"],
                vp["value_area_volume"],
                vp["bin_size"],
                json.dumps(vp["hvns"]),
                vp["price_ma20"],
                vp["price_ma50"],
                vp["price_ma200"],
                vp["volume_ma20"],
                vp["volume_ratio"],
                vp["range_pct"],
            ),
        )

        for sig in signals:
            conn.execute(
                """
                INSERT OR REPLACE INTO active_demand
                    (symbol, date, price, signal_type, support_level,
                     volume_ratio, close_position, price_change, strength, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    sig["symbol"],
                    sig["date"],
                    sig["price"],
                    sig["signal_type"],
                    sig["support_level"],
                    sig["volume_ratio"],
                    sig["close_position"],
                    sig["price_change"],
                    sig["strength"],
                    "{}",
                ),
            )

        conn.commit()
        conn.close()

        return {
            "status": "DONE",
            "symbol": symbol.upper(),
            "date": vp["date"],
            "price": vp["price_current"],
            "poc": vp["poc"],
            "vah": vp["vah"],
            "val": vp["val"],
            "value_area_pct": f"{vp['range_pct']:.1f}%",
            "volume_ratio": round(vp["volume_ratio"], 2),
            "signals": len(signals),
            "hvns": len(vp["hvns"]),
            "position": (
                "ABOVE_VA" if vp["price_current"] > vp["vah"] else "BELOW_VA" if vp["price_current"] < vp["val"] else "IN_VA"
            ),
        }

    def scan_multi(self, symbols: list[str]) -> dict:
        results = {}
        for sym in symbols:
            print(f"  [{sym}]...")
            r = self.scan_symbol(sym)
            results[sym] = r
            if r["status"] == "DONE":
                sig_str = f"{r['signals']} signals" if r["signals"] else "no signal"
                print(
                    f"    POC={r['poc']:,.0f} VA=[{r['val']:,.0f}–{r['vah']:,.0f}] "
                    f"Price={r['price']:,.0f} ({r['position']}) | {sig_str}"
                )
            else:
                print(f"    {r['status']}")
        return results


# =========================================================================
# CLI
# =========================================================================


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Market Behavior Engine — PTCK_VN Phase 4")
    parser.add_argument("action", choices=["init", "scan", "signals", "profile"], help="Hành động")
    parser.add_argument("--symbols", nargs="+", default=["FPT", "VCB", "STB", "CSB"], help="Danh sách symbol")
    parser.add_argument("--days", type=int, default=20, help="Số ngày lookback cho active demand")
    args = parser.parse_args()

    engine = MarketBehaviorEngine()

    if args.action == "init":
        print("=== Khởi tạo schema ===")
        engine.init_schema()

    elif args.action == "scan":
        print(f"=== Scan Market Behavior: {', '.join(args.symbols)} ===")
        engine.init_schema()
        results = engine.scan_multi(args.symbols)

        print(f"\n  {'=' * 60}")
        print("  VOLUME PROFILE SUMMARY")
        print(f"  {'=' * 60}")
        print(f"  {'Symbol':<8} {'Price':>10} {'POC':>10} {'VAL':>10} {'VAH':>10} {'Zone':<12} {'Sig'}")
        print(f"  {'-' * 60}")
        for sym in args.symbols:
            r = results.get(sym, {})
            if r.get("status") == "DONE":
                print(
                    f"  {sym:<8} {r['price']:>10,.0f} {r['poc']:>10,.0f} "
                    f"{r['val']:>10,.0f} {r['vah']:>10,.0f} "
                    f"{r['position']:<12} {r['signals']}"
                )

    elif args.action == "signals":
        print("=== Active Demand Signals ===")
        conn = engine.fin_conn()
        for sym in args.symbols:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT date, price, volume_ratio, close_position,
                       strength, support_level
                FROM active_demand
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT 10
            """,
                (sym.upper(),),
            )
            rows = cur.fetchall()
            if rows:
                print(f"\n  {sym}:")
                for r in rows:
                    print(
                        f"    {r[0]} price={r[1]:,.0f} "
                        f"vol={r[2]:.1f}x close_pos={r[3]:.0%} "
                        f"strength={r[4]:.2f} support={r[5]:,.0f}"
                    )
            else:
                print(f"\n  {sym}: No active demand signals")
        conn.close()

    elif args.action == "profile":
        for sym in args.symbols:
            r = engine.scan_symbol(sym)
            if r["status"] == "DONE":
                print(f"\n  {'=' * 50}")
                print(f"  {sym} — {r['date']}")
                print(f"  {'=' * 50}")
                print(f"  Price:     {r['price']:>12,.0f}")
                print(f"  POC:       {r['poc']:>12,.0f}")
                print(f"  VAL:       {r['val']:>12,.0f}")
                print(f"  VAH:       {r['vah']:>12,.0f}")
                print(f"  VA Range:  {r['value_area_pct']:>10}")
                print(f"  Position:  {r['position']}")
                print(f"  Vol Ratio: {r['volume_ratio']:>10.2f}x")
                print(f"  Signals:   {r['signals']}")
            else:
                print(f"\n  {sym}: {r['status']}")


if __name__ == "__main__":
    main()
