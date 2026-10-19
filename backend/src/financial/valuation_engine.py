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

import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
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

logger = logging.getLogger(__name__)

from src.database.data_validity import VALID_ROW_SQL
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

# BASIS của chuỗi giá dùng để tính ratio (BƯỚC P, 2026-10-16 — xem
# backend/src/research/financial_facts_ratio_audit.md §7/§8):
#   - `daily_ohlcv.close` là chuỗi ĐÃ back-adjust về số cổ phiếu HIỆN TẠI (bằng chứng: HPG
#     2018Q1 = 10.220 ≈ giá thô ~43.000 ÷ 4,22 lần tăng vốn) và `adj_close == close` trên
#     toàn bộ 1.578.932 dòng ⇒ hệ thống KHÔNG lưu cờ điều chỉnh, nó phụ thuộc nhà cung cấp.
#   - Hệ quả bắt buộc: mọi phép chia per-share (BVPS/EPS) PHẢI cùng basis ⇒ dùng số cổ phiếu
#     HIỆN TẠI. Trộn với shares TẠI KỲ (BVPS cũ) ⇒ cờ `BASIS_MISMATCH`, loại khỏi xếp hạng.
#   - Đổi nguồn giá ⇒ PHẢI chạy lại canary HPG 2018Q1 (tests/test_valuation_engine_pit.py).
PRICE_BASIS = "ADJUSTED_TODAY"  # ∈ {"ADJUSTED_TODAY", "RAW"}


def _median(xs: list[float]) -> float:
    """Trung vị không cần import (dùng cho hiệu chuẩn chu kỳ EPS)."""
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


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
            except (sqlite3.Error, TypeError, ValueError) as _e:
                logger.debug("ALTER TABLE valuation_scores cột đã tồn tại hoặc lỗi (bỏ qua): %s", _e)
        conn.commit()
        conn.close()
        print("  Schema OK: valuation_scores table (incl. peer-group columns)")

    def _quarter_end_date(self, year: int, quarter: int) -> str:
        m, d = QUARTER_END_MAP[quarter]
        return f"{year:04d}-{m:02d}-{d:02d}"

    def _get_price_at_date(self, symbol: str, target_date: str) -> float | None:
        """Close GẦN NHẤT về trước trong cửa sổ 30 ngày quanh `period_end` (PIT).

        ⚠️ SỬA 2026-10-16 (BƯỚC P, `financial_facts_ratio_audit.md` §8): bản cũ khi không tìm
        thấy bar nào trong 30 ngày đã fallback `ORDER BY date DESC LIMIT 1` — tức lấy **giá MỚI
        NHẤT trong kho (giá TƯƠNG LAI)** rồi tính PE/PB cho kỳ quá khứ. Đo được: **636/1.789
        cặp (35,6%)** bị nhiễm, đỉnh điểm **ACB 2018Q1→2019Q4** đều gán giá 21.100 của **2025**.
        Nay: KHÔNG có bar trong cửa sổ ⇒ trả `None` (Missing > Fake). Dùng
        `_get_price_with_flag()` nếu cần biết VÌ SAO thiếu.
        """
        price, _flag = self._get_price_with_flag(symbol, target_date)
        return price

    def _get_price_with_flag(self, symbol: str, target_date: str) -> tuple[float | None, str | None]:
        """(price, flag) — flag ∈ {None, PRICE_FALLBACK_FUTURE, PRICE_STALE_ONLY, PRICE_MISSING}.

        - `None`                  : có bar trong cửa sổ 30 ngày ⇒ PIT hợp lệ.
        - `PRICE_FALLBACK_FUTURE` : không có bar trong cửa sổ NHƯNG có bar **sau** `period_end`
                                    ⇒ bản cũ sẽ lấy giá tương lai (đúng 636 cặp đã nhiễm).
        - `PRICE_STALE_ONLY`      : chỉ có bar **trước** cửa sổ ⇒ giá cũ, lệch mức (không phải
                                    look-ahead nhưng vẫn không phải giá tại kỳ).
        - `PRICE_MISSING`         : mã hoàn toàn không có bar.

        [INVARIANT — LUẬT B (`AGENTS.md`)] Giá chỉ lấy từ bar `is_stale = 0` VÀ qua `VALID_ROW_SQL`
        (chế độ Live/PIT). ĐO ĐƯỢC khi thiếu vị từ: `DDG 2026Q2` lấy đúng dòng đã cách ly 800.000đ ⇒
        sinh `P/B = 226,18`; `BII 2024Q2` lấy 900.000đ thay vì bar sống 1.000đ ⇒ `P/S = 25.583,871`
        (Bước 3, commit `eebf89b`). Bỏ vị từ ⇒ mọi cờ cách ly trở thành vô nghĩa với consumer này.

        """
        conn = self.screener_connect()
        try:
            cur = conn.cursor()
            target = datetime.strptime(target_date, "%Y-%m-%d")
            for days_back in range(31):
                d = (target - timedelta(days=days_back)).strftime("%Y-%m-%d")
                cur.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?"
                    " AND COALESCE(is_stale, 0) = 0 AND " + VALID_ROW_SQL,
                    (symbol.upper(), d),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return row[0], None
            cur.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol=? AND date>?"
                " AND COALESCE(is_stale, 0) = 0 AND " + VALID_ROW_SQL,
                (symbol.upper(), target_date),
            )
            later = cur.fetchone()[0] or 0
            cur.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol=? AND COALESCE(is_stale, 0) = 0 AND " + VALID_ROW_SQL,
                (symbol.upper(),),
            )
            total = cur.fetchone()[0] or 0
            if later > 0:
                return None, "PRICE_FALLBACK_FUTURE"
            if total > 0:
                return None, "PRICE_STALE_ONLY"
            return None, "PRICE_MISSING"
        finally:
            conn.close()

    def _get_price_history(self, symbol: str) -> dict[str, float]:
        """Get all historical prices: {date: close}."""
        conn = self.screener_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT date, close FROM daily_ohlcv WHERE symbol=?"
            " AND COALESCE(is_stale, 0) = 0 AND " + VALID_ROW_SQL + " ORDER BY date",
            (symbol.upper(),),
        )
        result = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return result

    def _get_market_cap(self, price: float, shares_out: float) -> float | None:
        if price and shares_out:
            return price * shares_out
        return None

    def _get_latest_shares(self, facts: dict) -> float | None:
        """Lấy số cổ phiếu hiện tại (latest CHARTER_CAPITAL/10000).

        WHY (BASIS-FIX 2026-08-19): price từ screener_cache là split-adjusted
        (quy về số cổ phiếu hiện tại). BVPS fallback phải dùng shares HIỆN TẠI,
        không phải shares thời điểm đó, nếu không P/B lịch sử bị lệch basis
        (HPG 2018: PB=0.45 sai vs đúng 1.90 do phát hành thêm cổ phiếu).
        """
        latest_cc = None
        for period in sorted(facts):
            cc = facts[period].get("CHARTER_CAPITAL")
            if cc and cc > 0:
                latest_cc = cc
        return latest_cc / 10000.0 if latest_cc else None

    # ── TỰ HIỆU CHUẨN CHU KỲ EPS + BASIS SỐ CỔ PHIẾU (phán quyết 2026-10-16) ──────────
    # Quy ước EPS KHÁC NHAU theo nguồn (đo 2026-10-16, `financial_facts_ratio_audit.md` §9):
    #   vci : EPS == NET_INCOME_kỳ/shares (khớp ×1,00 mọi kỳ) ⇒ RIÊNG KỲ ⇒ annualize ×4
    #   tcbs: EPS == TTM4/shares          (khớp ×1,00 ở 6/6 kỳ) ⇒ TTM       ⇒ annualize ×1
    # ⇒ CẤM dùng một hằng số chung. Hiệu chuẩn từng mã bằng chính kho (dung sai 2%).
    CALIB_TOL = 0.02
    LEVEL_TOL_PCT = 2.0  # lệch MỨC > 2% ⇒ cờ THÔNG TIN (không chặn) — Bước 5, 2026-10-16
    SHRES_DIV_TOL_PCT = 25.0  # pha loãng ĐÃ BIẾT của SHB 2026 = +16,3% ⇒ >25% là khác bản chất
    MAX_PERIODS = 200  # `get_facts` mặc định chỉ 8 kỳ ⇒ phải xin rõ số kỳ cho chuỗi PIT
    PIT_TODAY = date.today().isoformat()  # kỳ có effective_date > mốc này = CHƯA được phép đọc
    ALL_RATIOS = ("PE", "PB", "PS", "EV_EBITDA", "ROE")
    # PHẠM VI TỪNG CỜ (sửa 2026-10-16 sau khi DoD B cho thấy 11.375 dòng bị loại OAN):
    # cờ chỉ được dán lên những ratio mà nó thực sự nói về ratio đó — nếu không, một hàng PE sẽ
    # mất tư cách "sạch" chỉ vì EV/EBITDA của cùng kỳ dùng EBITDA proxy. Cờ không có trong map
    # (giá, basis số cp) áp cho MỌI ratio vì chúng ảnh hưởng cả 5.
    FLAG_SCOPE = {
        "EXTREME_LOW_PE": {"PE"},
        "PERIODICITY_MISMATCH_EPS": {"PE"},
        "NEGATIVE_EARNINGS": {"PE"},
        "OUTLIER_WINSORED": {"PE"},
        "UNTRACEABLE_DENOMINATOR": {"EV_EBITDA"},
        "NEAR_ZERO_DENOMINATOR": {"PB", "PS", "EV_EBITDA"},
        "DISTRESSED_EQUITY": {"PB", "ROE"},
        "BASIS_MISMATCH": {"PB", "PE", "PS"},
        "EPS_PERIODICITY_UNKNOWN": {"PE", "ROE"},
        "EPS_PERIODICITY_AMBIGUOUS": {"PE", "ROE"},
        "EPS_LEVEL_MISMATCH": {"PE", "ROE"},
    }

    @classmethod
    def flags_for_ratio(cls, flags: list[str], ratio_name: str) -> list[str]:
        """Lọc cờ theo ratio: cờ không khai báo phạm vi ⇒ áp cho mọi ratio."""
        return sorted({f for f in flags if ratio_name in cls.FLAG_SCOPE.get(f, cls.ALL_RATIOS)})

    # Cờ THÔNG TIN: ghi lại để cảnh báo nhưng KHÔNG tước tư cách xếp hạng.
    # WHY (sửa 2026-10-16 sau DoD B): `SHARES_DILUTION_FLAGGED` báo "số cp đã pha loãng 1–25%
    # so với vốn điều lệ" — đây là thông tin CHO NGƯỜI ĐỌC (canary pha loãng theo phán quyết 2),
    # không phải bằng chứng sai số. Nếu cho nó chặn thì SHB/MBB/TCB… đều bị loại khỏi bảng xếp
    # hạng dù số liệu đúng. Ngược lại `SHARES_BASIS_UNEXPLAINED` (>25%, không giải thích được
    # bằng phát hành thêm) LÀ cờ chặn.
    INFO_FLAGS = {"SHARES_DILUTION_FLAGGED", "EPS_LEVEL_MISMATCH"}

    @classmethod
    def is_blocking(cls, flags: list[str]) -> bool:
        """True nếu còn cờ CHẶN (loại khỏi xếp hạng) sau khi bỏ cờ thông tin."""
        return any(f not in cls.INFO_FLAGS for f in flags)

    def _af_for(self, flow_kind: str, quarter: int) -> float:
        """Hệ số annualize theo `flow_kind`: TTM ×1 · QUARTER ×4 · YTD ×4/q · UNKNOWN ×1."""
        if flow_kind == "TTM":
            return 1.0
        if flow_kind == "QUARTER":
            return 4.0
        if flow_kind == "YTD":
            return self._annualize_factor(quarter)
        return 1.0

    def calibrate_eps_periodicity(self, facts: dict) -> tuple[str, str, float | None, float | None]:
        """→ (flow_kind, basis, cv_thắng, lệch_mức_%).

        PHƯƠNG PHÁP 2 BƯỚC (phán quyết 2026-10-16, Bước 5) — **dung sai vẫn 2%, KHÔNG nới**:
          1. **CHU KỲ (shape):** với mỗi quy ước, lập tỷ lệ `r_t = EPS_t / ứng_viên_t` rồi đo độ tán
             xạ **VỮNG** `1,4826 × MAD(r) / median(r)`. Quy ước ĐÚNG ⇒ `r` gần như hằng số ở mọi
             kỳ ⇒ tán xạ ≈ 0, *bất kể mức*. Nhận khi ≤ `CALIB_TOL`. (Bản 5a dùng `CV = σ/μ` và đã
             bị bác bỏ bằng số đo: vài kỳ dị biệt do trộn vintage veto cả mã ⇒ dòng sạch GIẢM.)
          2. **MỨC (level):** `lệch_mức = |median(r) − 1|`. > `LEVEL_TOL_PCT` ⇒ cờ **THÔNG TIN**
             `EPS_LEVEL_MISMATCH` — đó là khác biệt ĐỊNH NGHĨA/mẫu số (lợi nhuận mẹ vs toàn phần,
             cổ phiếu quỹ, mẫu số của nhà cung cấp), KHÔNG phải sai chu kỳ.
        Vì sao phải tách: đo 2026-10-16 — VNM có tỷ lệ **0,891 nhưng CV chỉ 1,2%** (hai chuỗi đồng
        pha tuyệt đối); điều kiện cũ `|median − 1| ≤ 2%` biến một sai số *danh mục kế toán* thành
        "không xác định được chu kỳ" và chặn oan hàng nghìn dòng.
        Chống chọn nhầm (fail-closed): **≥2 quy ước cùng đạt CV ≤ tol ⇒ `AMBIGUOUS`** (đúng ca NI
        gần phẳng — chọn sai ở đó làm PE lệch tới 4×).
        """
        periods = sorted(facts)
        raw: dict[str, list[float]] = {"QUARTER": [], "TTM": [], "YTD": []}

        def ni_at(idx: int) -> float | None:
            """NET_INCOME (hoặc NET_PROFIT) của kỳ thứ `idx` — **None nếu thiếu**.

            ⚠️ SỬA 2026-10-16 (DoD B phơi ra: 48% dòng `UNKNOWN`): bản đầu trả `0.0` cho quý
            thiếu ⇒ ứng viên TTM/YTD bị tính thiếu ⇒ lệch > dung sai ⇒ mã bị gắn `UNKNOWN` oan.
            Vi phạm luật "Không biết ≠ 0". Nay: thiếu ⇒ None ⇒ **bỏ ứng viên đó**, không đoán.
            """
            if idx < 0:
                return None
            pm = facts[periods[idx]]
            v = pm.get("NET_INCOME")
            if v is None:
                v = pm.get("NET_PROFIT")
            return float(v) if v is not None else None

        def window(idx: int, n: int) -> list[float] | None:
            """n kỳ gần nhất tính đến `idx`; None nếu BẤT KỲ kỳ nào thiếu."""
            vals = [ni_at(idx - k) for k in range(n)]
            if any(v is None for v in vals):
                return None
            return [float(v) for v in vals if v is not None]

        for idx, p in enumerate(periods):
            pm = facts[p]
            # Bỏ 4 kỳ đầu: trong năm đầu tiên, cửa sổ TTM (4 quý) TRÙNG cửa sổ YTD ⇒ hai ứng
            # viên không phân biệt được (dev bằng nhau ⇒ chọn theo thứ tự dict = tuỳ tiện).
            # Chỉ so từ kỳ thứ 5 trở đi thì YTD và TTM mới khác nhau thật.
            if idx < 4:
                continue
            eps = pm.get("EPS")
            ni = ni_at(idx)
            sh = pm.get("SHARES_OUT") or ((pm.get("CHARTER_CAPITAL") or 0) / 10000.0) or None
            if not eps or not sh:
                continue
            cands: dict[str, float] = {}
            if ni:
                cands["QUARTER"] = ni / sh
            w4 = window(idx, 4)
            if w4 and sum(w4) > 0:
                cands["TTM"] = sum(w4) / sh
            wy = window(idx, int(p[5]))
            if wy and sum(wy) > 0:
                cands["YTD"] = sum(wy) / sh
            for kind, cand in cands.items():
                if cand > 0:
                    raw[kind].append(eps / cand)

        stats: dict[str, tuple[float, float]] = {}  # kind → (độ_tán_xạ_vững, lệch_mức_%)
        for kind, rs in raw.items():
            if len(rs) < 4:
                continue
            med = _median(rs)
            if med <= 0:
                continue
            # ĐỘ TÁN XẠ VỮNG (Bước 5b, 2026-10-16): dùng MAD thay σ.
            # WHY: đo trên store sau Bước 5a — `CV = σ/μ` bị CHI PHỐI BỞI VÀI KỲ DỊ BIỆT (nguồn
            # trộn vintage cho 2–3 kỳ), nên nó VETO cả mã dù 90% kỳ khớp tuyệt đối: dòng sạch
            # 34.707 → 29.313, PE sạch 3.929 → 1.359 (GIẢM, ngược mục tiêu). Bản cũ dùng trung vị
            # nên không bị; đổi sang MAD giữ được tính vững đó mà vẫn đo ĐỘ TÁN XẠ (bất biến mức).
            # Ngưỡng VẪN 2% — không nới.
            mad = _median([abs(r - med) for r in rs])
            disp = 1.4826 * mad / abs(med)
            stats[kind] = (disp, abs(med - 1.0) * 100.0)

        if not stats:
            # Không đủ dữ liệu để hiệu chuẩn ⇒ mặc định theo NGUỒN (đã đo), nhãn SOURCE_DEFAULT.
            # `SHARES_OUT` chỉ tồn tại ở các dòng nguồn tcbs (đo 2026-10-16) ⇒ dấu hiệu nguồn.
            has_shares_out = any(facts[p].get("SHARES_OUT") for p in periods)
            return ("TTM" if has_shares_out else "QUARTER"), "SOURCE_DEFAULT", None, None

        ranked = sorted(stats.items(), key=lambda kv: kv[1][0])
        winners = [k for k, (cv, _l) in ranked if cv <= self.CALIB_TOL]
        if len(winners) > 1:
            cv, level = ranked[0][1]
            return "AMBIGUOUS", "AMBIGUOUS", cv, level
        if len(winners) == 1:
            cv, level = stats[winners[0]]
            return winners[0], "CALIBRATED", cv, level
        cv, level = ranked[0][1]
        return "UNKNOWN", "CALIBRATION_FAILED", cv, level

    def select_shares_now(self, facts: dict) -> tuple[float | None, str, list[str]]:
        """→ (shares_now, basis, flags) — số cổ phiếu cho BASIS A (giá adjusted + shares NOW).

        PHÁN QUYẾT 2 (2026-10-16): ưu tiên số cổ phiếu **đã pha loãng thực tế** (`SHARES_OUT`
        nguồn tcbs) vì giá `daily_ohlcv` đã back-adjust về số cp HIỆN TẠI; bảo thủ ⇒ dùng mẫu
        số đã phát hành (SHB 2026Q2: 5.343,7M ⇒ BVPS 14.093 ⇒ P/B 0,756 thay vì 0,650).

        GUARD: nếu |lệch| giữa `SHARES_OUT` và `CHARTER_CAPITAL/10.000` (cùng kỳ mới nhất) vượt
        `SHRES_DIV_TOL_PCT` thì **không dùng** — biên độ đó KHÔNG giải thích được bằng phát hành
        thêm (SHB 2010Q1–Q3 lệch **+1.234%**; 2015Q4 +181%) ⇒ fallback `CHARTER_PIT` + cờ
        `SHARES_BASIS_UNEXPLAINED`. Lệch trong 1–25% ⇒ dùng `SHARES_OUT` + cờ
        `SHARES_DILUTION_FLAGGED` (canary pha loãng, đúng phán quyết 2).
        """
        periods = sorted(facts)
        charter = None
        tcbs = None
        for p in periods:
            cc = facts[p].get("CHARTER_CAPITAL")
            if cc and cc > 0:
                charter = cc / 10000.0
            so = facts[p].get("SHARES_OUT")
            if so and so > 0:
                tcbs = float(so)
        if tcbs and charter:
            div = (tcbs - charter) / charter * 100.0
            if abs(div) <= self.SHRES_DIV_TOL_PCT:
                flags = ["SHARES_DILUTION_FLAGGED"] if abs(div) > 1.0 else []
                return tcbs, "TCBS_REPORTED", flags
            return charter, "CHARTER_PIT", ["SHARES_BASIS_UNEXPLAINED", "SHARES_DILUTION_FLAGGED"]
        if tcbs:
            return tcbs, "TCBS_REPORTED", ["SHARES_BASIS_UNVERIFIED"]
        if charter:
            return charter, "CHARTER_PIT", []
        return None, "NONE", []

    @staticmethod
    def pit_allowed(period_effective: dict[str, str], today: str) -> tuple[list[str], list[str]]:
        """→ (kỳ được đọc, kỳ BỊ CHẶN vì `effective_date` chưa tới).

        WHY (phát hiện 2026-10-16): B lần đầu ghi **19 dòng `2026Q3`** vào `valuation_scores` dù
        `effective_date = 2026-10-30` còn ở TƯƠNG LAI — tức tự tạo một lỗ look-ahead ở tầng điểm
        trong khi Hạng mục A đã cô lập đúng 90 dòng facts của kỳ đó. Nay engine **không sinh** ratio
        cho kỳ chưa tới ngày hiệu lực (fail-closed), và kỳ thiếu `effective_date` cũng bị chặn
        (không có provenance ⇒ đứng ngoài).
        """
        ok, blocked = [], []
        for per, eff in period_effective.items():
            if eff and eff <= today:
                ok.append(per)
            else:
                blocked.append(per)
        return sorted(ok), sorted(blocked)

    def _period_effective_dates(self, symbol: str) -> dict[str, str]:
        """Bản đồ {period → effective_date} đọc trực tiếp từ kho facts.

        ⚠️ Mở connection RIÊNG (read-only) — KHÔNG dùng/không đóng connection của `facts_db`:
        `FinancialFactsDB.connect()` cache một `self.conn` dùng chung; đóng nó làm chết cả engine
        (đã xảy ra 2026-10-16: `ProgrammingError: Cannot operate on a closed database` ở mã đầu tiên).
        Cũng không sửa `financial_facts.py` — file đó thuộc luồng ingestion.
        """
        path = getattr(self.facts_db, "db_path", None)
        if not path:
            return {}
        conn = sqlite3.connect(f"file:{Path(str(path)).as_posix()}?mode=ro", uri=True, timeout=30.0)
        try:
            rows = conn.execute(
                "SELECT period, MIN(effective_date) FROM financial_facts WHERE symbol=? AND period IS NOT NULL "
                "GROUP BY period",
                (symbol.upper(),),
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
        return {str(p): (str(e) if e else "") for p, e in rows}

    def compute_ratios_for_period(
        self,
        symbol: str,
        period: str,
        period_metrics: dict,
        entity_type: str,
        shares_now: float | None = None,
    ) -> dict:
        """Tính các valuation ratios cho 1 kỳ."""
        fy = period_metrics.get("_fiscal_year", int(period[:4]))
        fq = period_metrics.get("_fiscal_quarter", int(period[5:6]))
        qend = self._quarter_end_date(fy, fq)
        price, price_flag = self._get_price_with_flag(symbol, qend)
        flags: list[str] = []
        if price_flag:
            flags.append(price_flag)
        if not price:
            # Missing > Fake: KHÔNG bịa giá. Trả bản ghi CHỈ có cờ để tầng kiểm toán nhìn thấy
            # (bản cũ `return {}` làm mất dấu vết vì sao kỳ đó không có ratio).
            return {"_flags": flags, "_is_clean": False, "_price": None, "_no_price": True}

        # ── ANNUALIZE THEO CHU KỲ (sửa lỗi ×4 mù quáng — 2026-10-16) ──────────────
        # ĐO ĐƯỢC (financial_facts_ratio_audit.md §2): 39/40 ca PE<2 có PE_lưu = (giá/EPS)/4
        # CHÍNH XÁC, và kỳ của chúng tập trung ở Q2+Q4 = 65% — đúng chỗ EPS **luỹ kế YTD**.
        # Bản cũ nhân 4 cho MỌI kỳ ⇒ PE bị chia ~4 ở các kỳ luỹ kế. Nay: hệ số theo quý
        # (YTD: Q1×4, Q2×2, Q3×4/3, Q4×1); nếu dữ liệu là EPS RIÊNG QUÝ thì ×4.
        flow_kind = str(period_metrics.get("_flow_kind", "UNKNOWN")).upper()
        af = self._af_for(flow_kind, fq)
        if flow_kind == "UNKNOWN":
            flags.append("EPS_PERIODICITY_UNKNOWN")
        for _bf in period_metrics.get("_base_flags") or []:
            if _bf not in flags:
                flags.append(_bf)

        eps = period_metrics.get("EPS")
        shares = period_metrics.get("SHARES_OUT")
        bvps = period_metrics.get("BOOK_VALUE_PS")
        bvps_source = "PROVIDED" if bvps else None
        # SHARES_OUT proxy từ vốn điều lệ (BS): mệnh giá chuẩn TTCK VN = 10.000đ/cp.
        # WHY: vnstock VCI/KBS không trả item "số cổ phiếu lưu hành" trực tiếp trong BCTC
        # (probe 2026-08-14), nhưng BS có charter_capital/paid_in_capital. Chia cho mệnh
        # giá 10.000đ → số cổ phiếu. Chỉ dùng khi metric gốc vắng; nếu cả 2 đều thiếu →
        # shares=None → PB/PS không tính được (NO_DATA, không bịa).
        if not shares:
            cc = period_metrics.get("CHARTER_CAPITAL")
            if cc and cc > 0:
                shares = cc / 10000.0
        # Fallback BVPS = Equity / Shares
        # BASIS-FIX: price là split-adjusted → dùng shares hiện tại (shares_now)
        # khi có; nếu không có, rơi về shares thời điểm đó (cũ, bảo toàn hành vi).
        if not bvps:
            eq = period_metrics.get("TOTAL_EQUITY")
            if eq and shares_now:
                bvps = eq / shares_now
                bvps_source = "SHARES_NOW"
            elif eq and shares:
                bvps = eq / shares
                bvps_source = "SHARES_PIT"  # ⚠ trộn basis với giá ADJUSTED_TODAY → BASIS_MISMATCH
        revenue = period_metrics.get("REVENUE") or period_metrics.get("NII")
        # EBITDA proxy = OPERATING_PROFIT + DEPRECIATION_AMORTIZATION (CF).
        # WHY: VCI/KBS IS không có item ebitda (probe 2026-08-14) — mapping đã tồn tại
        # nhưng payload không trả. EBIT + D&A là công thức chuẩn; D&A lấy từ cashflow.
        ebitda = period_metrics.get("EBITDA")
        ebitda_is_proxy = False
        if not ebitda:
            op = period_metrics.get("OPERATING_PROFIT")
            da = period_metrics.get("DEPRECIATION_AMORTIZATION")
            if op and da:
                ebitda = op + da
                ebitda_is_proxy = True
        cash = period_metrics.get("CASH_EQUIV") or period_metrics.get("CASH_AND_BALANCES")
        debt = period_metrics.get("TOTAL_DEBT")

        mc = self._get_market_cap(price, shares)
        result = {"_price": price, "_mc": mc}

        # PE — annualize theo chu kỳ, không nhân 4 mù quáng; EPS ≤ 0 ⇒ KHÔNG bỏ lặng mà gắn cờ.
        if eps and eps > 0:
            result["PE"] = price / (eps * af)
        elif eps is not None and eps <= 0:
            flags.append("NEGATIVE_EARNINGS")

        # PB
        if bvps and bvps > 0:
            result["PB"] = price / bvps

        # ROE (annualized theo cùng hệ số chu kỳ — cùng lớp lỗi với PE)
        # WHY: FairMultipleEngine (Gordon Growth Model) cần chỉ số ROE để tính Fair P/B
        # và Margin of Safety (MoS). Lưu trữ ROE vào valuation_scores cho mọi doanh nghiệp.
        ni = period_metrics.get("NET_INCOME") or period_metrics.get("NET_PROFIT")
        eq = period_metrics.get("TOTAL_EQUITY")
        if ni and eq and eq > 0:
            result["ROE"] = (ni * af) / eq

        # PS (annualize doanh thu cùng hệ số; nếu không, PS sẽ nhảy theo quý trong cùng năm)
        if revenue and shares:
            rev_ps = (revenue * af) / shares
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

        # ── BOUNDING SPEC + CỜ (Hạng mục E) — additive, KHÔNG xoá/không sửa số ──────
        if eq and eq <= 0:
            flags.append("DISTRESSED_EQUITY")
        pe = result.get("PE")
        if pe is not None:
            if pe < 2.0:
                flags.append("EXTREME_LOW_PE")
                if fq in (2, 4):
                    # Kỳ luỹ kế (bán niên/cả năm) là nơi lỗi chu kỳ EPS hay lộ nhất ⇒ đánh dấu
                    # để tầng kiểm toán soi lại (kèm `eps_flow_kind` đã ghi ở cùng hàng).
                    flags.append("PERIODICITY_MISMATCH_EPS")
            elif pe > 100.0:
                flags.append("OUTLIER_WINSORED")
        if result.get("PB") is not None and result["PB"] > 20.0:
            flags.append("NEAR_ZERO_DENOMINATOR")
        if result.get("PS") is not None and result["PS"] > 100.0:
            flags.append("NEAR_ZERO_DENOMINATOR")
        if result.get("EV_EBITDA") is not None and result["EV_EBITDA"] > 200.0:
            flags.append("NEAR_ZERO_DENOMINATOR")
        if ebitda_is_proxy and result.get("EV_EBITDA") is not None:
            # EBITDA không tồn tại như metric trong kho ⇒ mẫu số là proxy EBIT+D&A.
            flags.append("UNTRACEABLE_DENOMINATOR")
        if PRICE_BASIS == "ADJUSTED_TODAY" and bvps_source == "SHARES_PIT":
            # Giá ở basis ADJUSTED_TODAY nhưng BVPS chia theo shares TẠI KỲ = râu ông nọ cắm
            # cằm bà kia (HPG 2018Q1 sẽ ra 0,450 thay vì 1,899) ⇒ loại khỏi xếp hạng.
            flags.append("BASIS_MISMATCH")

        result["_flags"] = sorted(set(flags))
        result["_is_clean"] = not self.is_blocking(result["_flags"])
        return result

    @staticmethod
    def _annualize_factor(quarter: int) -> float:
        """Hệ số annualize cho số **luỹ kế YTD** (chuẩn công bố VN: Q1=3M, Q2=H1, Q3=9M, Q4=FY).

        Q1 → ×4 · Q2 → ×2 · Q3 → ×4/3 · Q4 → ×1. (Bản cũ dùng ×4 cho mọi quý — xem §2 note.)
        """
        return {1: 4.0, 2: 2.0, 3: 4.0 / 3.0, 4: 1.0}.get(int(quarter), 4.0)

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

    @staticmethod
    def _clear_scores(conn: sqlite3.Connection, symbol: str) -> int:
        """Xoá MỌI dòng `valuation_scores` của một mã (gọi trong transaction của lần ghi).

        [WHY] Xem khối `[ATOMIC REPLACE]` tại chỗ gọi: `INSERT OR REPLACE` đơn thuần để lại GHOST ROW.
        Tách thành hàm để test được trực tiếp trên DB tạm mà không cần dựng cả engine.
        """
        return conn.execute("DELETE FROM valuation_scores WHERE symbol = ?", (symbol,)).rowcount

    def compute_valuation(self, symbol: str) -> dict:
        """Main entry: fetch facts + prices, compute all valuation ratios + z-scores."""
        entity_type = self.facts_db.get_entity_type(symbol)
        # WHY `periods`: `get_facts` mặc định chỉ trả **8 kỳ** — với chuỗi PIT (SHB có 71 kỳ từ
        # TCBS) thì 8 kỳ làm z-score/percentile lệch hẳn; phải xin toàn bộ chuỗi.
        facts = self.facts_db.get_facts(symbol, periods=self.MAX_PERIODS)
        if not facts:
            return {"status": "NO_DATA", "symbol": symbol}

        # GATE PIT (2026-10-16): CHỈ đọc kỳ có `effective_date <= hôm nay`. Kỳ chưa tới ngày hiệu
        # lực KHÔNG được sinh ratio — B lần đầu đã ghi 19 dòng `2026Q3` (effective 2026-10-30) dù
        # kỳ đó còn ở tương lai, tức tự tạo lỗ look-ahead ở tầng điểm. Cũng chặn luôn kỳ KHÔNG có
        # `effective_date` (không provenance ⇒ đứng ngoài — luật bất biến #1).
        _eff = self._period_effective_dates(symbol)
        if _eff:
            _allowed, _blocked = self.pit_allowed(_eff, self.PIT_TODAY)
            if _blocked:
                logger.warning("[PIT] %s: chặn %d kỳ chưa tới ngày hiệu lực %s", symbol, len(_blocked), _blocked[:5])
            facts = {p: facts[p] for p in _allowed if p in facts}
            if not facts:
                return {"status": "NO_PIT_PERIODS", "symbol": symbol.upper(), "blocked": _blocked}

        # PHÁN QUYẾT 1+2 (2026-10-16): hiệu chuẩn chu kỳ EPS THEO TỪNG MÃ và chọn basis số cổ
        # phiếu CÓ GUARD — không dùng hằng số chung, không trộn basis (xem docstring 2 hàm dưới).
        # BƯỚC 5: tách CHU KỲ (CV ≤ 2%) khỏi MỨC (lệch mức chỉ là cờ thông tin).
        flow_kind, flow_basis, flow_cv, flow_level = self.calibrate_eps_periodicity(facts)
        shares_now, shares_basis, shares_flags = self.select_shares_now(facts)
        base_flags = list(shares_flags)
        if flow_kind == "UNKNOWN":
            base_flags.append("EPS_PERIODICITY_UNKNOWN")
        elif flow_kind == "AMBIGUOUS":
            # Hai quy ước cùng ổn định (NI gần phẳng) ⇒ chọn sai làm PE lệch tới 4× ⇒ chặn.
            base_flags.append("EPS_PERIODICITY_AMBIGUOUS")
        if flow_level is not None and flow_level > self.LEVEL_TOL_PCT:
            # Lệch MỨC hệ thống (lợi nhuận mẹ vs toàn phần / mẫu số của nguồn) — THÔNG TIN, không
            # chặn: chu kỳ vẫn đúng, và PE dùng EPS như nhà cung cấp công bố nên không lệch theo.
            base_flags.append("EPS_LEVEL_MISMATCH")

        # For each period, compute valuation ratios
        all_period_ratios = {}  # {period: {ratio_name: value}}
        for period in sorted(facts.keys()):
            pm = dict(facts[period])
            pm["_flow_kind"] = flow_kind
            pm["_base_flags"] = base_flags
            ratios = self.compute_ratios_for_period(symbol, period, pm, entity_type, shares_now=shares_now)
            if ratios:
                all_period_ratios[period] = ratios

        if not all_period_ratios:
            # [WHY] Mã không còn kỳ nào tính được ⇒ VẪN phải dọn ghost của các lần chạy cũ, nếu
            # không nó tồn tại vĩnh viễn (đúng lớp lỗi `DDG 2026Q2` / `BII 2020Q1` đã đo).
            _c = self.connect()
            try:
                self._clear_scores(_c, symbol.upper())
                _c.commit()
            finally:
                _c.close()
            return {"status": "NO_PRICE", "symbol": symbol}

        # For each ratio type, compute stats + z-scores + percentiles
        ratio_names = ["PE", "PB", "PS", "EV_EBITDA", "ROE"]
        conn = self.connect()
        # Migration ADDITIVE tại chỗ dùng (idempotent) — 5 cột cờ kiểm toán Hạng mục E.
        # WHY: cờ phải nằm cùng hàng điểm để mọi consumer thấy được mà không cần join bảng phụ;
        # CHỈ thêm cột, KHÔNG sửa/xoá dữ liệu cũ (đúng luật "gắn cờ, không xoá cứng").
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(valuation_scores)")}
        for _c, _t in (
            ("anomaly_flags", "TEXT"),
            ("is_clean_metric", "INTEGER"),
            ("price_basis", "TEXT"),
            ("shares_basis", "TEXT"),
            ("eps_flow_kind", "TEXT"),
        ):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE valuation_scores ADD COLUMN {_c} {_t}")
        results = []

        # [ATOMIC REPLACE — 2026-10-16] Xoá TOÀN BỘ điểm cũ của mã, CÙNG transaction với phần ghi
        # bên dưới. [WHY] ĐO ĐƯỢC: `INSERT OR REPLACE` không có DELETE để lại GHOST ROW — kỳ nào
        # engine mới TỪ CHỐI sinh (vd `DDG 2026Q2` không còn bar sạch ⇒ `_no_price`) thì hàng ratio
        # cũ (P/B = 226,18 với price 800.000đ đã cách ly) vẫn nằm nguyên. Sau khi vá vị từ giá, đo
        # được đúng 4 ghost: `DDG 2026Q2`; `BII 2020Q1` (PB + EV_EBITDA); `BII 2023Q4` — tất cả là
        # dòng mà engine hiện tại KHÔNG THỂ sinh lại (`_get_price_with_flag` trả None).
        # [INVARIANT] Recompute = THAY THẾ, không phải upsert: sau lần chạy, tập dòng của mã phải
        # ĐÚNG BẰNG tập dòng engine vừa sinh. An toàn vì `compute_valuation(symbol)` luôn tính toàn
        # bộ kỳ của mã (không có tham số lọc kỳ). Script gọi chịu trách nhiệm backup trước khi chạy.
        self._clear_scores(conn, symbol.upper())

        for rname in ratio_names:
            # Collect all values across periods (sorted ascending)
            # WHY: z-score tính theo CHUỖI LỊCH SỬ nội tại của chính symbol (mean/std của
            # các kỳ) thay vì ngưỡng tĩnh — mục tiêu là phát hiện "symbol đang rẻ hơn bình
            # thường của chính nó", phù hợp cả với cổ phiếu tăng trưởng cao vốn có P/E luôn
            # lớn. QUAN TRỌNG (PIT): dùng EXPANDING WINDOW — mỗi kỳ chỉ tính mean/std/
            # percentile trên các kỳ <= kỳ hiện tại, KHÔNG dùng tương lai → replay đọc
            # period <= target_date nhận được z-score không nhiễm look-ahead.
            period_values = []
            for period in sorted(all_period_ratios.keys()):
                v = all_period_ratios[period].get(rname)
                if v is not None and v > 0:
                    period_values.append((period, v))

            if len(period_values) < 1:
                continue

            # Time-series z-score (TS) — intrinsic valuation vs symbol's own history.
            # WHY: TS là PRIMARY valuation method theo thiết kế (44a8cf1) nhưng từng bị
            # clear khi backfill chạy clear_scores() + compute_valuation() vì engine không
            # bao giờ điền cột TS. Khôi phục tại nguồn:
            #   z_ts,t = (x_t - mu_{<t}) / sigma_{<t}   (STRICTLY-PAST window)
            # với mu/sigma tính trên các kỳ TRƯỚC period hiện tại (KHÔNG gồm observation
            # hiện tại), yêu cầu >= 12 quý history. Khác với CS expanding inclusive
            # (z_score) → TS đo "rẻ/đắt so với quá khứ của chính nó tại thời điểm đó",
            # tạo dispersion thật thay vì trùng z_score. Nếu chưa đủ history -> NULL
            # (NO_DATA), KHÔNG fallback giả thành FAIR — thiếu dữ liệu phải là tín hiệu
            # khác biệt, không phải trung tính giả.
            TS_MIN_QUARTERS = 12
            ts_stats = {}
            for idx, (_per, _v) in enumerate(period_values):
                past = [v for _, v in period_values[:idx]]
                if len(past) >= TS_MIN_QUARTERS:
                    m, s, nn = self._compute_stats(past)
                    if s > 0:
                        ts_stats[_per] = (round(m, 4), round(s, 4), nn)
                    else:
                        ts_stats[_per] = (None, None, nn)
                else:
                    ts_stats[_per] = (None, None, len(past))

            for i, (period, val) in enumerate(period_values):
                window = [v for _, v in period_values[: i + 1]]
                mean, std, n = self._compute_stats(window)
                z = (val - mean) / std if std > 0 else 0.0
                pct = self._percentile(window, val)
                zone = self._classify_zone(rname, z)
                price = all_period_ratios[period].get("_price")
                # TS z-score tại period này (strictly-past window, PIT-safe)
                ts_mean, ts_std, ts_n = ts_stats.get(period, (None, None, None))
                if ts_mean is not None and ts_std is not None:
                    z_ts = round((val - ts_mean) / ts_std, 4)
                    zone_ts = self._classify_zone(rname, z_ts)
                else:
                    z_ts = None
                    zone_ts = None  # NO_DATA: chưa đủ 12 quý history, không ép FAIR

                fy = int(period[:4])
                fq = int(period[5:6])
                _period_flags = self.flags_for_ratio(all_period_ratios[period].get("_flags") or [], rname)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO valuation_scores
                        (symbol, period, fiscal_year, fiscal_quarter,
                         entity_type, ratio_name, ratio_value, z_score,
                         percentile, mean, std, count, zone, price,
                         z_score_ts, zone_ts, mean_5y, std_5y, count_5y,
                         anomaly_flags, is_clean_metric, price_basis, shares_basis, eps_flow_kind)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        z_ts,
                        zone_ts,
                        ts_mean,
                        ts_std,
                        ts_n,
                        (",".join(_period_flags) or None),
                        (0 if self.is_blocking(_period_flags) else 1),
                        PRICE_BASIS,
                        shares_basis,
                        flow_kind,
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
                        "z_score_ts": z_ts,
                        "zone_ts": zone_ts,
                        "ts_count": ts_n,
                    }
                )

        conn.commit()
        conn.close()

        if not results:
            return {"status": "NO_VALUATION_RESULTS", "symbol": symbol.upper()}

        # Summary: latest period zones
        # WHY: latest_price phải theo latest_period — trước đây gán trong vòng lặp ratio
        # (PE→PB→…→ROE) nên ratio cuối thiếu kỳ mới (VD ROE 2026Q1 khi PB có 2026Q2)
        # overwrite giá kỳ cũ vào summary → latest_period=2026Q2 nhưng price=Q1-end.
        latest_period = sorted(results, key=lambda r: r["period"])[-1]["period"]
        latest_price = all_period_ratios.get(latest_period, {}).get("_price")
        if not latest_price:
            for period in sorted(all_period_ratios.keys(), reverse=True):
                p = all_period_ratios[period].get("_price")
                if p:
                    latest_price = p
                    break
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
