"""forensic_engine.py — Forensic Screening (Beneish M-Score, Sloan, ARI).

Rule-based forensic scan over the canonical `financial_facts` store.
Fully vectorized (Pandas/NumPy): one bulk query, pivot to wide, then pure
column-wise math. No per-symbol Python loops.

Time-series safety (Period-Adjacency Validation):
  A ratio at period t is only computed against period t-1 when the fiscal
  ordinals (fiscal_year * 4 + fiscal_quarter) differ by exactly 1. Rows
  that lose adjacency (missing quarter, fiscal-year change, split/merged
  reporting period) get a NEUTRAL value — 1.0 for indices (DSRI, SGI, AQI,
  GMI, DEPI, SGAI, LVGI), NaN for accruals (Sloan) — so a broken time
  series can never manufacture a false red flag.

Entity-type note: BANK symbols lack industrial metrics (COGS, GROSS_PROFIT,
RECEIVABLES, ADMIN_EXPENSES, DEPRECIATION). Missing metrics degrade to the
neutral 1.0 index (M-Score stays finite, never falsely flagged). ARI falls
back to RECEIVABLES/TOTAL_EQUITY because "Phải thu khác" (other receivables)
is not persisted as a separate metric in the fact store.
"""

# WHY: Báo cáo tài chính thiếu quý / đổi niên độ / gộp-tách kỳ là hiện tượng
# thường gặp ở thị trường VN. Dùng shift(1) thuần tuý sẽ so sánh t với kỳ
# KHÔNG liền kề -> bóp méo DSRI/SGI/AQI -> cờ đỏ giả. Ordinal + adjacency
# check biến mọi gián đoạn thành "không có tín hiệu" (neutral) thay vì
# "tín hiệu giả", đúng nguyên tắc kiểm toán: không đủ bằng chứng thì không
# kết luận vi phạm.

import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from src.financial.financial_facts import FINANCIAL_DB_PATH

# Beneish M-Score coefficients (canonical 8-index model)
BENEISH_COEFF = {
    "DSRI": 0.920,
    "GMI": 0.528,
    "AQI": 0.404,
    "SGI": 0.892,
    "DEPI": 0.115,
    "SGAI": -0.172,
    "TATA": 4.679,
    "LVGI": -0.327,
}
BENEISH_INTERCEPT = -4.84
BENEISH_THRESHOLD = -1.78

OUTPUT_COLUMNS = [
    "symbol",
    "period",
    "fiscal_year",
    "fiscal_quarter",
    "adjacent",
    "DSRI",
    "GMI",
    "AQI",
    "SGI",
    "DEPI",
    "SGAI",
    "LVGI",
    "TATA",
    "M_Score",
    "M_Score_Flag",
    "Sloan_Ratio",
    "ARI",
    "RPT_Flag",
    "Hard_Violation",
    "Risk_Score",
]


class ForensicEngine:
    """Rule-based forensic screener over financial_facts.db."""

    # Canonical metric -> accepted source metric names (alias resolution).
    metric_aliases: dict[str, list[str]] = {
        "REVENUE": ["REVENUE", "NET_REVENUE"],
        "NET_INCOME": ["NET_INCOME", "NET_PROFIT"],
        "CFO": ["CFO"],
        "TOTAL_ASSETS": ["TOTAL_ASSETS"],
        "CURRENT_ASSETS": ["CURRENT_ASSETS"],
        "TOTAL_EQUITY": ["TOTAL_EQUITY"],
        "TOTAL_LIABILITIES": ["TOTAL_LIABILITIES"],
        "RECEIVABLES": ["RECEIVABLES", "OTHER_RECEIVABLES"],
        "GROSS_PROFIT": ["GROSS_PROFIT"],
        "COGS": ["COGS"],
        "DEPRECIATION": ["DEPRECIATION"],
        "ADMIN_EXPENSES": ["ADMIN_EXPENSES", "OPERATING_EXPENSE"],
    }

    _numeric_metrics: list[str] = list(metric_aliases.keys())

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(FINANCIAL_DB_PATH)

    # ── Data load (single bulk query + pivot) ───────────────────────────
    def load_facts(self, conn: sqlite3.Connection) -> pd.DataFrame:
        """Load the forensic metric subset from the EAV fact store, pivot to wide.

        One query for every symbol — never one query per symbol. Columns:
        symbol, period, fiscal_year, fiscal_quarter + one wide column per metric.
        """
        aliases = sorted({a for names in self.metric_aliases.values() for a in names})
        placeholders = ",".join(["?"] * len(aliases))
        query = f"""
            SELECT symbol, period, fiscal_year, fiscal_quarter, metric, value
            FROM financial_facts
            WHERE metric IN ({placeholders})
        """
        raw = pd.read_sql_query(query, conn, params=aliases)
        if raw.empty:
            return raw
        pivot = raw.pivot_table(
            index=["symbol", "period", "fiscal_year", "fiscal_quarter"],
            columns="metric",
            values="value",
            aggfunc="first",
        ).reset_index()
        return pivot

    # ── Vectorized forensic math ────────────────────────────────────────
    def compute_forensics(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute M-Score, Sloan, ARI + flags on a wide-format fact frame.

        Accepts a frame with symbol/period/fiscal_year/fiscal_quarter plus any
        subset of the metric columns; missing metrics degrade to neutral.
        """
        if df is None or len(df) == 0:
            return df

        df = df.copy()

        for canonical, aliases in self.metric_aliases.items():
            if canonical in df.columns:
                continue
            for alias in aliases:
                if alias in df.columns:
                    df[canonical] = df[alias]
                    break
        for col in self._numeric_metrics:
            if col not in df.columns:
                df[col] = np.nan
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values(["symbol", "fiscal_year", "fiscal_quarter"]).reset_index(drop=True)

        # Period-Adjacency Validation
        df["ordinal"] = df["fiscal_year"] * 4 + df["fiscal_quarter"]
        prev_ord = df.groupby("symbol")["ordinal"].shift(1)
        df["adjacent"] = (df["ordinal"] - prev_ord) == 1
        adj = df["adjacent"].to_numpy(dtype=bool)

        shift_cols = [
            "REVENUE",
            "RECEIVABLES",
            "TOTAL_ASSETS",
            "CURRENT_ASSETS",
            "TOTAL_EQUITY",
            "TOTAL_LIABILITIES",
            "NET_INCOME",
            "CFO",
            "GROSS_PROFIT",
            "COGS",
            "DEPRECIATION",
            "ADMIN_EXPENSES",
        ]
        group = df.groupby("symbol")
        for col in shift_cols:
            df[f"prev_{col}"] = group[col].shift(1)

        rev = df["REVENUE"].to_numpy()
        rec = df["RECEIVABLES"].to_numpy()
        ta = df["TOTAL_ASSETS"].to_numpy()
        ca = df["CURRENT_ASSETS"].to_numpy()
        liab = df["TOTAL_LIABILITIES"].to_numpy()
        ni = df["NET_INCOME"].to_numpy()
        cfo = df["CFO"].to_numpy()
        eq = df["TOTAL_EQUITY"].to_numpy()
        prev_rev = df["prev_REVENUE"].to_numpy()
        prev_rec = df["prev_RECEIVABLES"].to_numpy()
        prev_ta = df["prev_TOTAL_ASSETS"].to_numpy()
        prev_ca = df["prev_CURRENT_ASSETS"].to_numpy()
        prev_liab = df["prev_TOTAL_LIABILITIES"].to_numpy()

        def _neutral(ratio: np.ndarray) -> np.ndarray:
            return np.where(np.isnan(ratio), 1.0, ratio)

        # DSRI = (Rec/Rev)_t / (Rec/Rev)_{t-1}
        r_t = np.where(rev > 0, rec / rev, np.nan)
        r_t1 = np.where(prev_rev > 0, prev_rec / prev_rev, np.nan)
        df["DSRI"] = _neutral(np.where(adj & (r_t1 > 0), r_t / r_t1, 1.0))

        # GMI = GM_{t-1} / GM_t,  GM = GrossProfit / Revenue
        gp = df["GROSS_PROFIT"].to_numpy()
        gp_t1 = df["prev_GROSS_PROFIT"].to_numpy()
        gm_t = np.where(rev > 0, gp / rev, np.nan)
        gm_t1 = np.where(prev_rev > 0, gp_t1 / prev_rev, np.nan)
        df["GMI"] = _neutral(np.where(adj & (gm_t > 0), gm_t1 / gm_t, 1.0))

        # AQI = (1 - CA/TA)_t / (1 - CA/TA)_{t-1}  (non-current assets proxy)
        aq_t = np.where(ta > 0, (ta - ca) / ta, np.nan)
        aq_t1 = np.where(prev_ta > 0, (prev_ta - prev_ca) / prev_ta, np.nan)
        df["AQI"] = _neutral(np.where(adj & (aq_t1 > 0), aq_t / aq_t1, 1.0))

        # SGI = Rev_t / Rev_{t-1}
        df["SGI"] = _neutral(np.where(adj & (prev_rev > 0), rev / prev_rev, 1.0))

        # DEPI = (Dep/TA)_{t-1} / (Dep/TA)_t
        dep = df["DEPRECIATION"].to_numpy()
        dep_t1 = df["prev_DEPRECIATION"].to_numpy()
        dr_t = np.where(ta > 0, dep / ta, np.nan)
        dr_t1 = np.where(prev_ta > 0, dep_t1 / prev_ta, np.nan)
        df["DEPI"] = _neutral(np.where(adj & (dr_t > 0), dr_t1 / dr_t, 1.0))

        # SGAI = (SGA/Rev)_t / (SGA/Rev)_{t-1}
        sga = df["ADMIN_EXPENSES"].to_numpy()
        sga_t1 = df["prev_ADMIN_EXPENSES"].to_numpy()
        sg_t = np.where(rev > 0, sga / rev, np.nan)
        sg_t1 = np.where(prev_rev > 0, sga_t1 / prev_rev, np.nan)
        df["SGAI"] = _neutral(np.where(adj & (sg_t1 > 0), sg_t / sg_t1, 1.0))

        # LVGI = (Liab/TA)_t / (Liab/TA)_{t-1}
        lv_t = np.where(ta > 0, liab / ta, np.nan)
        lv_t1 = np.where(prev_ta > 0, prev_liab / prev_ta, np.nan)
        df["LVGI"] = _neutral(np.where(adj & (lv_t1 > 0), lv_t / lv_t1, 1.0))

        # TATA = (NI - CFO) / TA  (level accrual; no t-1 required)
        df["TATA"] = np.where(ta > 0, (ni - cfo) / ta, np.nan)

        # Sloan Accruals Ratio with 2-period average total assets
        prev_ta_shift = df["prev_TOTAL_ASSETS"].to_numpy()
        avg_assets = (ta + prev_ta_shift) / 2.0
        avg_assets = np.where(avg_assets <= 0, ta, avg_assets)
        df["Sloan_Ratio"] = np.where(adj & (avg_assets > 0), (ni - cfo) / avg_assets, np.nan)

        # ARI = Receivables / Equity (point-in-time, no adjacency required)
        df["ARI"] = np.where(eq > 0, rec / eq, 0.0)

        # Beneish M-Score
        df["M_Score"] = (
            BENEISH_INTERCEPT
            + BENEISH_COEFF["DSRI"] * df["DSRI"]
            + BENEISH_COEFF["GMI"] * df["GMI"]
            + BENEISH_COEFF["AQI"] * df["AQI"]
            + BENEISH_COEFF["SGI"] * df["SGI"]
            + BENEISH_COEFF["DEPI"] * df["DEPI"]
            + BENEISH_COEFF["SGAI"] * df["SGAI"]
            + BENEISH_COEFF["TATA"] * df["TATA"]
            + BENEISH_COEFF["LVGI"] * df["LVGI"]
        )
        # Flag only rows backed by an adjacent t-1 (never a broken series).
        df["M_Score_Flag"] = (df["M_Score"] > BENEISH_THRESHOLD) & adj

        df["Hard_Violation"] = (df["Sloan_Ratio"].abs() > 0.25) & (df["ARI"] > 0.50)

        sloan_abs = df["Sloan_Ratio"].abs().fillna(0.0)
        ari_cap = df["ARI"].clip(0.0, 1.0).fillna(0.0)
        m_flag = df["M_Score_Flag"].astype(int)
        df["Risk_Score"] = np.clip((sloan_abs * 2.0 + ari_cap + m_flag * 0.5) / 3.5, 0.0, 1.0)
        df.loc[df["Hard_Violation"], "Risk_Score"] = 1.0

        # Notes text is not persisted in financial_facts -> RPT always False.
        df["RPT_Flag"] = False

        return df[OUTPUT_COLUMNS]

    # ── Entry point ─────────────────────────────────────────────────────
    def run(self, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
        """Screen every symbol in the store; return the forensic frame.

        Pass `conn` to reuse an existing connection (dependency injection —
        required for in-memory/`:memory:` databases and E2E tests).
        """
        if conn is not None:
            df = self.load_facts(conn)
            return self.compute_forensics(df)
        with sqlite3.connect(self.db_path) as local_conn:
            df = self.load_facts(local_conn)
        return self.compute_forensics(df)


class ForensicScoreCache:
    """O(1) materialized cache of forensic scores for the Provider/Governor layer.

    The full-screen forensic scan is expensive (queries + vectorized math for
    every symbol), so it must never run inside a hot request path. A background
    job / post-backfill hook calls :meth:`refresh` to persist the LATEST period
    per symbol into the `forensic_scores` table; the ProviderManager reads that
    table via a PRIMARY KEY lookup — O(1), no recompute at request time.
    """

    TABLE = "forensic_scores"

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS forensic_scores (
            symbol          TEXT PRIMARY KEY,
            period          TEXT NOT NULL,
            m_score         REAL,
            m_score_flag    INTEGER NOT NULL DEFAULT 0,
            sloan_ratio     REAL,
            ari             REAL,
            hard_violation  INTEGER NOT NULL DEFAULT 0,
            risk_score      REAL NOT NULL DEFAULT 0.0,
            computed_at     TEXT DEFAULT (datetime('now'))
        );
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(FINANCIAL_DB_PATH)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(self._SCHEMA)
            conn.commit()

    def refresh(self, frame: pd.DataFrame, conn: sqlite3.Connection | None = None) -> int:
        """Upsert the latest period per symbol from a forensic frame.

        Keeps the newest row (by fiscal ordinal) per symbol so the O(1) lookup
        always returns the most recent screening. Returns rows written.
        """
        if frame is None or frame.empty:
            return 0
        latest = frame.sort_values(["symbol", "fiscal_year", "fiscal_quarter"]).groupby("symbol", as_index=False).tail(1)

        def _nz(v: Any) -> Any:
            return None if pd.isna(v) else float(v)

        rows = [
            (
                r.symbol,
                r.period,
                _nz(r.M_Score),
                int(bool(r.M_Score_Flag)),
                _nz(r.Sloan_Ratio),
                _nz(r.ARI),
                int(bool(r.Hard_Violation)),
                float(r.Risk_Score),
            )
            for r in latest.itertuples(index=False)
        ]
        owns_conn = conn is None
        conn = conn if conn is not None else self._connect()
        try:
            conn.execute(self._SCHEMA)
            conn.executemany(
                f"INSERT OR REPLACE INTO {self.TABLE} "
                "(symbol, period, m_score, m_score_flag, sloan_ratio, ari, "
                " hard_violation, risk_score) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
        finally:
            if owns_conn:
                conn.close()
        return len(rows)

    def get(self, symbol: str) -> dict[str, Any] | None:
        """O(1) PRIMARY KEY lookup — returns the latest score for one symbol."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT * FROM {self.TABLE} WHERE symbol = ?",
                    (symbol.upper(),),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {
            "symbol": row["symbol"],
            "period": row["period"],
            "m_score": row["m_score"],
            "m_score_flag": bool(row["m_score_flag"]),
            "sloan_ratio": row["sloan_ratio"],
            "ari": row["ari"],
            "hard_violation": bool(row["hard_violation"]),
            "risk_score": row["risk_score"],
            "computed_at": row["computed_at"],
        }
