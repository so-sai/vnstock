"""
erl_worker.py — One-shot ERL Scan Worker for Tauri Desktop Sidecar.

Kiến trúc 3 lớp chống ban IP:
  Phase 1: In-Database/File Filter (0 HTTP Request)
  Phase 2: Async Throttled Fetch (nếu cần BCTC)
  Output:  Whitelist Top 30 -> data/erl_whitelist.json

Chế độ hoạt động:
  - CLI:  python ptck.py erl-scan
  - Tauri Sidecar: Gọi one-shot, không background loop.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ──
LIQUIDITY_MIN_BN = 1.0  # Thanh khoản tối thiểu (tỷ VND)
RS_RANK_MIN = 50  # RS Rating percentile tối thiểu
TOP_CANDIDATES = 100  # Số candidate sau Phase 1
WHITELIST_SIZE = 30  # Số mã trong whitelist cuối
CRISIS_HARD_LIQUIDITY_GATE_PCT = 0.005  # % tổng giá trị giao dịch toàn thị trường
CRISIS_HARD_LIQUIDITY_GATE_FLOOR = 50.0  # tỷ VND — ngưỡng tối thiểu
WHITELIST_FILENAME = "erl_whitelist.json"
LAST_SCAN_FILENAME = "erl_last_scan.txt"
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 5


@dataclass
class ERLStock:
    symbol: str
    rs_rating: float
    avg_value_20d: float  # tỷ VND
    avg_vol_20d: float
    price: float
    p_survive: float = 0.5  # P(Survive) from proxy
    volume_penalty: float = 1.0
    volatility_proxy: float = 1.0
    anomaly_streak: int = 0
    composite_score: float = 0.0


@dataclass
class ERLWhitelist:
    generated_at: str = ""
    scan_date: str = ""
    market_regime: str = ""
    s_plus: float = 0.0
    hard_liquidity_gate: float = 0.0  # 0 = tắt, >0 = ngưỡng tỷ VND đang kích hoạt
    total_market_value_bn: float = 0.0  # tổng giá trị giao dịch toàn TT phiên gần nhất (tỷ)
    whitelist: list = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.whitelist)

    def to_dict(self):
        return {
            "generated_at": self.generated_at,
            "scan_date": self.scan_date,
            "market_regime": self.market_regime,
            "s_plus": self.s_plus,
            "hard_liquidity_gate": self.hard_liquidity_gate,
            "total_market_value_bn": self.total_market_value_bn,
            "whitelist": self.whitelist,
            "count": self.count,
        }


def _is_holiday(d: date) -> bool:
    """Kiểm tra ngày lễ từ weekend_holidays.json + cuối tuần."""
    if d.weekday() >= 5:
        return True
    try:
        from src.config import PROJECT_ROOT

        cal_path = PROJECT_ROOT / "backend" / "src" / "config" / "weekend_holidays.json"
        if cal_path.exists():
            import json

            data = json.loads(cal_path.read_text(encoding="utf-8"))
            return d.isoformat() in data.get("holidays", [])
    except Exception:
        pass
    return False


def _prev_trading_day(d: date) -> date:
    """Tìm ngày giao dịch gần nhất trước d."""
    d = d
    while True:
        d = d.__class__.fromordinal(d.toordinal() - 1)
        if not _is_holiday(d):
            return d


def _compute_dynamic_gate(data_dir: Path) -> tuple[float, float]:
    """Tính Dynamic Liquidity Gate = 0.5% tổng giá trị giao dịch toàn thị trường.

    Returns:
        (gate_value_tỷ, total_market_value_tỷ)
    """
    db_path = data_dir / "screener_cache.db"
    if not db_path.exists():
        return (CRISIS_HARD_LIQUIDITY_GATE_FLOOR, 0.0)

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT SUM(close * volume / 1e9) FROM daily_ohlcv WHERE date = (SELECT MAX(date) FROM daily_ohlcv)"
        )
        row = cursor.fetchone()
        conn.close()
        total_value = row[0] if row and row[0] else 0.0
    except Exception:
        return (CRISIS_HARD_LIQUIDITY_GATE_FLOOR, 0.0)

    if total_value <= 0:
        return (CRISIS_HARD_LIQUIDITY_GATE_FLOOR, 0.0)

    gate = total_value * CRISIS_HARD_LIQUIDITY_GATE_PCT
    return (max(gate, CRISIS_HARD_LIQUIDITY_GATE_FLOOR), round(total_value, 0))


def latest_closed_session(now: datetime | None = None) -> date:
    """Xác định phiên đã đóng cửa gần nhất.

    - Trước 15:05  → lấy ngày GD trước đó (hôm nay chưa đóng)
    - Sau 15:05    → lấy hôm nay (đã đóng)
    - Cuối tuần/lễ → lấy ngày GD trước đó
    """
    if now is None:
        now = datetime.now()
    today = now.date()

    # Đã qua 15:05?
    if now.hour > MARKET_CLOSE_HOUR or (now.hour == MARKET_CLOSE_HOUR and now.minute >= MARKET_CLOSE_MINUTE):
        if not _is_holiday(today):
            return today

    # Chưa đóng cửa hoặc hôm nay là lễ → lấy phiên trước
    return _prev_trading_day(today)


def _find_data_dir() -> Path:
    """Locate backend/data directory."""
    from src.config import DATA_DIR

    return Path(DATA_DIR)


def _load_rs_data(data_dir: Path) -> list[dict]:
    """Load market_rs.json — chứa RS Rating + avg_value_20d."""
    rs_path = data_dir / "market_rs.json"
    if not rs_path.exists():
        logger.error("market_rs.json not found. Run rs_ranker first.")
        return []
    with open(rs_path, encoding="utf-8") as f:
        return json.load(f)


def _load_regime_snapshot(data_dir: Path) -> dict:
    """Load regime từ snapshot_index.json để ghi vào whitelist context."""
    idx_path = data_dir / "output" / "snapshot_index.json"
    if idx_path.exists():
        try:
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
            if idx:
                latest = idx[-1]
                return {
                    "regime": latest.get("regime", "UNKNOWN"),
                    "delta_sa": latest.get("delta_sa", 0),
                }
        except json.JSONDecodeError, IndexError:
            pass
    return {"regime": "UNKNOWN", "delta_sa": 0}


def phase1_coarse_filter(data_dir: Path) -> list[ERLStock]:
    """Phase 1: Lọc 100% local — không HTTP request.

    Dùng market_rs.json để lọc:
      - avg_value_20d >= 1 tỷ VND (thanh khoản)
      - rs_rating >= 50 (sức mạnh giá nửa trên)
      - Sort by rs_rating DESC, giới hạn 100 candidate.
    """
    rs_data = _load_rs_data(data_dir)
    if not rs_data:
        return []

    candidates: list[ERLStock] = []
    for row in rs_data:
        val = float(row.get("avg_value_20d", 0) or 0)
        rs = float(row.get("rs_rating", 0) or 0)
        if val >= LIQUIDITY_MIN_BN and rs >= RS_RANK_MIN:
            candidates.append(
                ERLStock(
                    symbol=row["symbol"],
                    rs_rating=rs,
                    avg_value_20d=val,
                    avg_vol_20d=float(row.get("avg_vol_20d", 0) or 0),
                    price=float(row.get("price", 0) or 0),
                )
            )

    candidates.sort(key=lambda x: x.rs_rating, reverse=True)
    top = candidates[:TOP_CANDIDATES]
    logger.info(
        "[Phase 1] Đã lọc %d/%d mã từ local RS data (≥%.0f tỷ, RS≥%.0f).",
        len(top),
        len(rs_data),
        LIQUIDITY_MIN_BN,
        RS_RANK_MIN,
    )
    return top


def phase2_proxy_scan(
    candidates: list[ERLStock],
    target_date: str,
    data_dir: Path,
) -> list[ERLStock]:
    """Phase 2: Tính P(Survive) proxy cho từng candidate.

    Dùng EntityResilienceLayer để tính volume anomaly + volatility proxy
    từ local DB — vẫn giữ nguyên 0 HTTP request ở giai đoạn này.
    """
    from src.engine.entity_resilience_layer import EntityResilienceLayer

    db_path = str(data_dir / "screener_cache.db")
    erl = EntityResilienceLayer(db_path=db_path)

    # Scan batch qua DB
    try:
        results = erl.scan_all(target_date=target_date, db_path=db_path)
    except Exception as e:
        logger.warning("ERL proxy scan failed: %s", e)
        results = []

    # Map results vào candidates
    result_map = {r["symbol"]: r for r in results}

    for stock in candidates:
        r = result_map.get(stock.symbol)
        if r:
            stock.p_survive = r.get("P", 0.5)
            stock.volume_penalty = r.get("volume_penalty", 1.0)
            stock.volatility_proxy = r.get("volatility_proxy", 1.0)
            stock.anomaly_streak = r.get("anomaly_streak", 0)
        else:
            stock.p_survive = 0.5
            stock.volume_penalty = 1.0
            stock.volatility_proxy = 1.0

    logger.info("[Phase 2] Đã tính P(Survive) cho %d candidate.", len(candidates))
    return candidates


def compute_composite_score(stock: ERLStock) -> float:
    """Composite score: kết hợp RS Rating + P(Survive).

    Công thức:
      composite = 0.40 * RS_pct + 0.35 * P(Survive) + 0.25 * (1 - anomaly_streak/5)
    """
    rs_pct = stock.rs_rating / 100.0
    surv = stock.p_survive
    anomaly_score = 1.0 - min(stock.anomaly_streak, 5) / 5.0

    return round(0.40 * rs_pct + 0.35 * surv + 0.25 * anomaly_score, 4)


def build_whitelist(
    stocks: list[ERLStock],
    scan_date: str,
    data_dir: Path,
) -> ERLWhitelist:
    """Tính composite score, chọn Top 30, ghi file."""
    for s in stocks:
        s.composite_score = compute_composite_score(s)

    stocks.sort(key=lambda x: x.composite_score, reverse=True)

    # ==============================================================================
    # WHY: In TRENDING/RANGING regimes, small-caps with >1B VND liquidity are
    # tradable. During CRISIS_WARNING / CRISIS, bid orders evaporate instantly
    # (Liquidity Lockout), turning a 5% stop-loss into 30% slippage.
    # RULE: Enforce 0.5% HOSE total daily value as liquidity floor when CRISIS.
    # Gate auto-disables when regime returns to TRENDING/RANGING.
    # ==============================================================================
    # Hard Liquidity Gate: phạt Small-cap khi regime CRISIS
    regime_info = _load_regime_snapshot(data_dir)
    regime = regime_info.get("regime", "UNKNOWN")
    gate_active = regime in ("CRISIS_WARNING", "CRISIS")
    gate_value, total_market_value = _compute_dynamic_gate(data_dir)
    if gate_active:
        before = len(stocks)
        stocks = [s for s in stocks if s.avg_value_20d >= gate_value]
        removed = before - len(stocks)
        if removed:
            logger.info(
                "[Hard Gate] CRISIS regime: loại %d mã Small-cap <%.0f tỷ (%.2f%% tổng TT).",
                removed,
                gate_value,
                CRISIS_HARD_LIQUIDITY_GATE_PCT * 100,
            )
    else:
        gate_value = 0.0

    top30 = stocks[:WHITELIST_SIZE]

    whitelist = ERLWhitelist(
        generated_at=datetime.now().isoformat(),
        scan_date=scan_date,
        market_regime=regime,
        s_plus=regime_info.get("delta_sa", 0),
        hard_liquidity_gate=gate_value if gate_active else 0.0,
        total_market_value_bn=total_market_value,
        whitelist=[
            {
                "symbol": s.symbol,
                "rs_rating": s.rs_rating,
                "avg_value_20d": round(s.avg_value_20d, 1),
                "price": round(s.price, 1),
                "p_survive": round(s.p_survive, 4),
                "volume_penalty": round(s.volume_penalty, 4),
                "anomaly_streak": s.anomaly_streak,
                "composite_score": s.composite_score,
            }
            for s in top30
        ],
    )

    # Ghi file
    out_path = data_dir / WHITELIST_FILENAME
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(whitelist.to_dict(), f, ensure_ascii=False, indent=2)

    # Ghi timestamp cho catch-up detection
    ts_path = data_dir / LAST_SCAN_FILENAME
    ts_path.write_text(scan_date, encoding="utf-8")

    logger.info(
        "[Whitelist] Đã ghi %d mã vào %s",
        len(top30),
        out_path,
    )
    return whitelist


def scan(
    data_dir: str | None = None,
    target_date: str | None = None,
    output: str | None = None,
) -> ERLWhitelist:
    """Main entry: one-shot ERL scan pipeline.

    target_date mặc định = latest_closed_session():
      - Nếu hiện tại >= 15:05 → scan hôm nay (phiên vừa đóng)
      - Nếu hiện tại < 15:05  → scan phiên trước (hôm nay chưa đóng)
    """
    if data_dir is None:
        d_dir = _find_data_dir()
    else:
        d_dir = Path(data_dir)

    if target_date is None:
        target_date = latest_closed_session().isoformat()

    # Phase 1
    candidates = phase1_coarse_filter(d_dir)
    if not candidates:
        logger.warning("Phase 1 returned 0 candidates. RS data may be stale.")
        return ERLWhitelist(scan_date=target_date)

    # Phase 2
    candidates = phase2_proxy_scan(candidates, target_date, d_dir)

    # Build whitelist
    whitelist = build_whitelist(candidates, target_date, d_dir)

    # Optional: output ra file chỉ định
    if output:
        out = Path(output)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(whitelist.to_dict(), f, ensure_ascii=False, indent=2)

    return whitelist


def needs_catchup(data_dir: Path | None = None) -> bool:
    """Kiểm tra xem có cần catch-up scan không.

    So sánh last_scan với phiên đã đóng cửa gần nhất:
      - 10:30 sáng → last_scan phải >= hôm qua (phiên đã đóng gần nhất)
      - 16:00 chiều → last_scan phải = hôm nay (phiên hôm nay đã đóng)
    """
    if data_dir is None:
        data_dir = _find_data_dir()
    ts_path = data_dir / LAST_SCAN_FILENAME
    expected = latest_closed_session()
    if not ts_path.exists():
        logger.info("Catch-up needed: chưa có ERL scan nào.")
        return True
    try:
        last_scan = ts_path.read_text(encoding="utf-8").strip()
        if last_scan != expected.isoformat():
            logger.info(
                "Catch-up needed: last_scan=%s, expected=%s (latest closed session).",
                last_scan,
                expected,
            )
            return True
        return False
    except Exception:
        return True


def get_whitelist(data_dir: Path | None = None) -> dict:
    """Đọc erl_whitelist.json hiện tại."""
    if data_dir is None:
        data_dir = _find_data_dir()
    path = data_dir / WHITELIST_FILENAME
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError, FileNotFoundError:
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = scan()
    print(f"ERL Scan hoàn tất: {result.count} mã được chọn.")
    for s in result.whitelist[:5]:
        print(f"  {s['symbol']:6s} score={s['composite_score']:.4f} P(Survive)={s['p_survive']:.2%}")
