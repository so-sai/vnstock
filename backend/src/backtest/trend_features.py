"""Trend Engine — Feature dựng theo Point-In-Time (PIT).

Module này là NƠI LOOK-AHEAD BIAS CHUI VÀO nếu viết sai, nên nó được thiết kế sao cho
tính PIT là hệ quả của CẤU TRÚC, không phải của sự cẩn thận khi gọi hàm.

Ý tưởng: dựng pivot (index = ngày, cột = mã) MỘT LẦN cho cả cửa sổ, rồi mọi feature
được tính bằng phép toán LÙI (`.shift`, `.rolling`, `.rank(axis=1)`). Vì mọi phép
toán chỉ đọc hàng quá khứ của chính nó, giá trị tại ngày T KHÔNG THỂ phụ thuộc dữ
liệu sau T — kể cả khi bảng pivot có chứa tương lai. Đây là điều kiện kiểm được bằng
test bất biến (xem `test_trend_features_no_lookahead.py`): "thêm dữ liệu tương lai
vào bảng thì giá trị tại T phải KHÔNG ĐỔI".

Nguồn định nghĩa:
  · RS   — mô phỏng ĐÚNG công thức production (`src/engine/rs_ranker.py`):
               raw = 0.4·r63 + 0.2·r126 + 0.2·r189 + 0.2·r252
               rating = raw.rank(pct=True) × 99
           với r_n = close[T]/close[T−n] − 1.
  · ADV20/high_20/MA20 — spec §4, cửa sổ "nền 20 phiên" TRƯỚC phiên T.

CÁC LỆCH CÓ CHỦ Ý so với production (nêu rõ để không ai tưởng là sai sót):
  1. Thiếu lịch sử ⇒ trả None (vô hiệu mã), KHÔNG trả 0. Production `get_return()`
     trả 0 khi thiếu phiên. Số 0 bịa đó làm mã mới niêm yết xếp hạng "tệ nhất", và
     tệ hơn: nó làm MỌI mã khác được percentile CAO LÊN một cách giả tạo. Ở đây
     `missing > fake`: không đo được thì loại khỏi bảng xếp hạng.
  2. Cả 4 feature dùng cửa sổ TRƯỚC phiên T (`.shift(1)`), không gồm phiên T.
     Production tính ADV20 bằng `tail(20)` — GỒM phiên mới nhất. Với "Volume ≥ 1.3×
     ADV20" thì gồm phiên T làm mẫu số tự nuốt chính cú spike đang xét.

═══ ⛔ CHƯA ĐƯỢC WIRE VÀO VÒNG LẶP — 2 CHẶN ĐANG MỞ ═══

Probe trên DB thật (2026-10-08) cho thấy RS ở đây KHÔNG tái lập được RS production,
nên cổng "RS ≥ 90" của spec §4 hiện KHÔNG kiểm được đúng nghĩa:

  (A) KHÁC MẪU SỐ XẾP HẠNG. Production xếp hạng trên mọi mã có ≥20 phiên (thiếu lịch
      sử ⇒ 0), tôi xếp hạng trên mã đủ 252 phiên. Top-10 hai bên gần như không giao
      nhau; trên 156 mã chung, điểm RS lệch trung vị 7 điểm (max 15.7).
      ⇒ RS ≥ 90 đang có nghĩa khác nhau ở hai nơi.

  (B) DỮ LIỆU GIÁ ĐANG NHIỄM. 34.157 dòng `close = 0` (toàn bộ nguồn kbs, toàn bộ
      năm 2026, đúng 45 phiên × 1.008 mã) đang mang `is_stale = 0`. Chuỗi giá của các
      mã đó xen 0,00 với 300–500, khiến momentum vô nghĩa: HKB r252 ≈ (300−0,4)/0,4
      ≈ 749 ⇒ rs_raw 336 ⇒ **RS 99, đứng đầu thị trường**. Toàn bộ 6 mã có
      |rs_raw| > 5 trong market_rs.json đều thuộc nhóm này.
      Đây là lỗi dữ liệu SỐNG, không chỉ lỗi backtest: radar RS production cũng đọc
      chính chuỗi đó.

Vì (A) và (B), module này DỪNG ở tầng feature: đúng về PIT (13 test bất biến xanh),
nhưng định nghĩa RS và tính hợp lệ của dữ liệu phải được chủ hệ thống chốt trước khi
nối vào `run_backtest()`.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# ── Định nghĩa RS — khớp production (rs_ranker.calculate_rs_score) ──
# Khoá = số phiên lùi, giá trị = trọng số. Pin bằng test: sửa trọng số là sửa
# định nghĩa RS, tức là sửa ngưỡng "RS ≥ 90" của spec theo một cách không ai thấy.
RS_WEIGHTS: dict[int, float] = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
# Chủ hệ thống chốt "rolling window 250 phiên"; công thức production cần đúng 252
# phiên cho thành phần r1y ⇒ hiện thực hoá là 252. 252 ≥ 250 nên không trái ý định.
RS_LOOKBACK_SESSIONS = 252
RS_RANK_SCALE = 99.0  # khớp production: rank(pct=True) × 99

BASE_WINDOW = 20  # "nền 20 phiên" (spec §4)
EXCLUDE_CURRENT_SESSION = True  # .shift(1) cho cả 4 feature — xem docstring mục 2

FEATURE_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class FeatureRow:
    """Feature của một mã tại một phiên. `ok=False` nghĩa là KHÔNG dùng được, kèm lý do."""

    symbol: str
    date: str
    close: float
    open_: float
    high_20: float
    ma20: float
    volume: float
    adv20: float
    rs: float
    ok: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def volume_ratio(self) -> float:
        return self.volume / self.adv20 if self.adv20 > 0 else 0.0


def _connect(db: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    return con


def load_pivot(field_name: str, start: str, end: str, *, db: Path = DB_PATH, clean_only: bool = True) -> pd.DataFrame:
    """Pivot một cột của daily_ohlcv: index = date (str), cột = symbol.

    `clean_only`: chỉ lấy is_stale = 0 — dòng nhiễm không được tham gia tính toán.
    """
    if field_name not in FEATURE_COLUMNS:
        raise ValueError(f"cột không hợp lệ: {field_name!r}; cho phép {FEATURE_COLUMNS}")
    where = "AND is_stale = 0" if clean_only else ""
    q = (
        f"SELECT date, symbol, {field_name} AS v FROM daily_ohlcv "
        f"WHERE date BETWEEN ? AND ? {where}"
    )
    with _connect(db) as con:
        df = pd.read_sql_query(q, con, params=[start, end])
    if df.empty:
        raise ValueError(f"không có dữ liệu {field_name} trong [{start}, {end}]")
    pivot = df.pivot_table(index="date", columns="symbol", values="v", aggfunc="last")
    return pivot.sort_index()


def _sessions_before(date: str, n: int, *, db: Path = DB_PATH) -> str:
    """Ngày của phiên cách `date` đúng n-sessions (dùng chính dữ liệu thật, không lịch giả)."""
    with _connect(db) as con:
        rows = con.execute(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE date < ? ORDER BY date DESC LIMIT ?",
            (date, n),
        ).fetchall()
    if len(rows) < n:
        raise ValueError(f"không đủ {n} phiên trước {date} trong DB")
    return str(rows[-1]["date"])


class FeatureStore:
    """Kho feature PIT. Mọi giá trị trả về tại ngày T chỉ phụ thuộc dữ liệu ≤ T.

    Dựng pivot bao gồm cả WARM-UP (mặc định 252 phiên trước `start`) để ngày đầu
    của cửa sổ cũng có RS — nếu không, RS sẽ rỗng đúng những phiên đầu và ai đó sẽ
    "sửa" bằng cách bỏ điều kiện thay vì kéo warm-up.
    """

    def __init__(self, start: str, end: str, *, warmup: int = RS_LOOKBACK_SESSIONS, db: Path = DB_PATH):
        self.start, self.end, self.warmup, self.db = start, end, warmup, db
        self.warmup_start = _sessions_before(start, warmup, db=db)
        self._panels: dict[str, pd.DataFrame] = {}
        self._cache: dict[str, pd.DataFrame] = {}

    # ── panel + feature (tính lười, mỗi thứ một lần) ──
    def panel(self, field_name: str) -> pd.DataFrame:
        if field_name not in self._panels:
            self._panels[field_name] = load_pivot(field_name, self.warmup_start, self.end, db=self.db)
        return self._panels[field_name]

    def _shifted(self, field_name: str) -> pd.DataFrame:
        """Panel đã .shift(1) — cửa sổ TRƯỚC phiên T (không gồm T)."""
        if field_name not in self._cache:
            p = self.panel(field_name)
            self._cache[field_name] = p.shift(1) if EXCLUDE_CURRENT_SESSION else p
        return self._cache[field_name]

    def _rs_frame(self) -> pd.DataFrame:
        if "rs" not in self._cache:
            close = self.panel("close")
            # Khởi tạo bằng 0/NaN để NaN lan truyền: mã thiếu lịch sử ⇒ RS = NaN ⇒
            # bị loại khỏi bảng xếp hạng, KHÔNG bị gán 0 (xem docstring đầu file).
            raw: pd.DataFrame = close * 0.0
            for n, w in RS_WEIGHTS.items():
                ret = close / close.shift(n) - 1.0  # CHỈ đọc quá khứ
                raw = raw + ret * w
            # rank(axis=1) = xếp hạng THEO HÀNG (theo phiên), không đọc hàng tương lai.
            self._cache["rs"] = raw.rank(axis=1, pct=True) * RS_RANK_SCALE
        return self._cache["rs"]

    def _mean20(self, field_name: str) -> pd.DataFrame:
        key = f"mean20_{field_name}"
        if key not in self._cache:
            self._cache[key] = self._shifted(field_name).rolling(BASE_WINDOW).mean()
        return self._cache[key]

    def _max20(self, field_name: str) -> pd.DataFrame:
        key = f"max20_{field_name}"
        if key not in self._cache:
            self._cache[key] = self._shifted(field_name).rolling(BASE_WINDOW).max()
        return self._cache[key]

    # ── tra cứu một ô ──
    @staticmethod
    def _at(frame: pd.DataFrame, symbol: str, date: str) -> float | None:
        if symbol not in frame.columns or date not in frame.index:
            return None
        v = frame.at[date, symbol]
        if v is None or pd.isna(v):
            return None
        return float(v)

    def rs(self, symbol: str, date: str) -> float | None:
        return self._at(self._rs_frame(), symbol, date)

    def adv20(self, symbol: str, date: str) -> float | None:
        return self._at(self._mean20("volume"), symbol, date)

    def high_20(self, symbol: str, date: str) -> float | None:
        return self._at(self._max20("high"), symbol, date)

    def ma20(self, symbol: str, date: str) -> float | None:
        return self._at(self._mean20("close"), symbol, date)

    def close(self, symbol: str, date: str) -> float | None:
        return self._at(self.panel("close"), symbol, date)

    def open_(self, symbol: str, date: str) -> float | None:
        return self._at(self.panel("open"), symbol, date)

    def volume(self, symbol: str, date: str) -> float | None:
        return self._at(self.panel("volume"), symbol, date)

    # ── gói tất cả ──
    def features(self, symbol: str, date: str) -> FeatureRow:
        """FeatureRow đầy đủ. Thiếu bất kỳ thành phần nào ⇒ ok=False kèm lý do cụ thể.

        KHÔNG bao giờ thay giá trị thiếu bằng 0: 0 là một con số có nghĩa (giá/khối
        lượng bằng 0 không tồn tại), dùng nó sẽ khiến mã thiếu dữ liệu bị chấm như mã
        có dữ liệu xấu — sai cả về nguyên nhân lẫn về hướng.
        """
        need = {
            "close": self.close(symbol, date),
            "open": self.open_(symbol, date),
            "high_20": self.high_20(symbol, date),
            "ma20": self.ma20(symbol, date),
            "volume": self.volume(symbol, date),
            "adv20": self.adv20(symbol, date),
            "rs": self.rs(symbol, date),
        }
        missing = [k for k, v in need.items() if v is None]
        if missing:
            return FeatureRow(
                symbol=symbol,
                date=date,
                close=need["close"] or 0.0,
                open_=need["open"] or 0.0,
                high_20=need["high_20"] or 0.0,
                ma20=need["ma20"] or 0.0,
                volume=need["volume"] or 0.0,
                adv20=need["adv20"] or 0.0,
                rs=need["rs"] or 0.0,
                ok=False,
                reasons=[f"thiếu feature: {', '.join(missing)} (missing > fake)"],
            )
        return FeatureRow(
            symbol=symbol,
            date=date,
            close=need["close"],  # type: ignore[arg-type]
            open_=need["open"],  # type: ignore[arg-type]
            high_20=need["high_20"],  # type: ignore[arg-type]
            ma20=need["ma20"],  # type: ignore[arg-type]
            volume=need["volume"],  # type: ignore[arg-type]
            adv20=need["adv20"],  # type: ignore[arg-type]
            rs=need["rs"],  # type: ignore[arg-type]
        )
