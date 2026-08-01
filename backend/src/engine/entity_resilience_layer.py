"""
entity_resilience_layer.py — Entity Resilience Layer (ERL) Framework

Asynchronous Multi-rate Observation Engine:
  Low-freq: BCTC (Quarterly)  → Prior State
  High-freq: OHLCV + RS (Daily) → Posterior Shift

POMDP Belief State:
  P(S_Entity)_t = P(S_Entity | BCTC_q) × Π(Volume_Anomaly_evidence)_daily
                  × Beneish_Penalty(M)  [khi BCTC ve]


Giai doan 1: Proxy BCTC tu daily_ohlcv (Volume Anomaly, Volatility, RS Alpha)
Giai doan 2: Nap BCTC that (Net Debt, EBITDA, Interest Coverage, Beneish M-Score)
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ──
BENEISH_CUTOFF = -1.78  # M > -1.78 → manipulation suspected
VOLUME_RATIO_THRESHOLD = 2.0
VOLUME_RATIO_ALERT = 3.0
RS_Z_THRESHOLD = -1.5
RS_Z_ALERT = -2.0
ANOMALY_CONSECUTIVE_DAYS = 3
PENALTY_MODERATE = 0.7  # Volume anomaly pre-emptive
PENALTY_SEVERE = 0.5  # Distribution suspected
PENALTY_BENEISH_BASE = 0.5  # M-Score penalty floor
PENALTY_BENEISH_FLOOR = 0.3

# ── Beneish M-Score coefficients (Beneish 1999, corrected) ──
BENEISH_CONST = -4.84
BENEISH_DSRI = 0.920
BENEISH_GMI = 0.528
BENEISH_AQI = 0.404
BENEISH_SGI = 0.892
BENEISH_DEPI = 0.115
BENEISH_SGAI = -0.172
BENEISH_TATA = 4.697
BENEISH_LVGI = -0.327


def compute_beneish_m_score(
    dsri: float, gmi: float, aqi: float, sgi: float,
    depi: float, sgai: float, tata: float, lvgi: float,
) -> float:
    """Beneish M-Score chuan hoa (Beneish 1999, he so TATA=4.697)."""
    return (
        BENEISH_CONST
        + BENEISH_DSRI * dsri
        + BENEISH_GMI * gmi
        + BENEISH_AQI * aqi
        + BENEISH_SGI * sgi
        + BENEISH_DEPI * depi
        + BENEISH_SGAI * sgai
        + BENEISH_TATA * tata
        + BENEISH_LVGI * lvgi
    )


def compute_m_score_penalty(m_score: float) -> float:
    """Tinh he so phat tu M-Score.

    M > -1.78 → ap dung phat tuyen tinh, floor 0.3
    M <= -1.78 → khong phat
    """
    if m_score <= BENEISH_CUTOFF:
        return 1.0
    raw = PENALTY_BENEISH_BASE * (1.0 - (m_score + 2.22) / 5.0)
    return max(PENALTY_BENEISH_FLOOR, raw)


class EntityResilienceLayer:
    """Entity Resilience Layer — mo hinh niem tin khong dong bo.

    Usage:
        erl = EntityResilienceLayer(db_path="...")
        # Giai doan 1: Proxy scan
        results = erl.scan_all(target_date="2026-07-22")
        # Giai doan 2: Khi co BCTC
        erl.update_beneish(symbol="VIC", dsri=..., gmi=..., ...)
        belief = erl.get_belief("VIC")
    """

    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        # Belief state: {symbol: {"P": float, "prior": float, "volume_penalty": float,
        #                          "beneish_penalty": float, "anomaly_streak": int,
        #                          "last_updated": str, "beneish_m": float}}
        self._beliefs: dict[str, dict] = {}

    # ── Public API ──

    def scan_all(self, target_date: str, db_path: str | None = None) -> list[dict]:
        """Scan toan bo 1514+ ma, tra ve danh sach co P(S_Entity) thap nhat."""
        if db_path:
            self.db_path = db_path
        conn = sqlite3.connect(self.db_path)

        symbols = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX','VN30')"
            ).fetchall()
        ]

        results = []
        for sym in symbols:
            belief = self._compute_proxy_belief(sym, target_date, conn)
            results.append(belief)
            self._beliefs[sym] = belief

        conn.close()
        results.sort(key=lambda x: x["P"])
        return results

    def scan_vn30(self, target_date: str, db_path: str | None = None) -> list[dict]:
        """Scan VN30 stocks (mac dinh 30 ma lon nhat)."""
        if db_path:
            self.db_path = db_path
        conn = sqlite3.connect(self.db_path)

        # Lay top 30 ma co volume lon nhat
        top30 = conn.execute(
            f"SELECT symbol, SUM(volume) as total_vol FROM daily_ohlcv "
            f"WHERE symbol NOT IN ('VNINDEX','VN30') AND date BETWEEN "
            f"date('{target_date}', '-90 days') AND '{target_date}' "
            f"GROUP BY symbol ORDER BY total_vol DESC LIMIT 30"
        ).fetchall()

        results = []
        for sym, vol in top30:
            belief = self._compute_proxy_belief(sym, target_date, conn)
            results.append(belief)
            self._beliefs[sym] = belief

        conn.close()
        results.sort(key=lambda x: x["P"])
        return results

    def get_belief(self, symbol: str) -> dict:
        return self._beliefs.get(symbol, {"P": 0.5, "prior": 0.5})

    def update_beneish(self, symbol: str, dsri=1.0, gmi=1.0, aqi=1.0, sgi=1.0,
                        depi=1.0, sgai=1.0, tata=0.0, lvgi=1.0) -> dict:
        """Cap nhat Posterior Belief khi co BCTC moi (Giai doan 2)."""
        m = compute_beneish_m_score(dsri, gmi, aqi, sgi, depi, sgai, tata, lvgi)
        penalty = compute_m_score_penalty(m)

        prev = self._beliefs.get(symbol, {"P": 0.5, "prior": 0.5, "volume_penalty": 1.0})
        prior = prev["prior"]

        # Reconciliation: volume anomaly bao dong sai?
        if prev["volume_penalty"] < 1.0 and m <= BENEISH_CUTOFF:
            logger.info(
                "ERL reconcile: %s — false positive overridden (M=%.2f <= %.2f)",
                symbol, m, BENEISH_CUTOFF,
            )
            new_P = prior
            self._beliefs[symbol] = {
                "P": prior, "prior": prior, "volume_penalty": 1.0,
                "beneish_penalty": 1.0, "anomaly_streak": 0,
                "last_updated": datetime.now().isoformat(),
                "beneish_m": round(m, 4),
                "reconciled": True,
            }
        else:
            new_P = prior * penalty
            self._beliefs[symbol] = {
                "P": new_P, "prior": prior, "volume_penalty": prev["volume_penalty"],
                "beneish_penalty": penalty, "anomaly_streak": prev.get("anomaly_streak", 0),
                "last_updated": datetime.now().isoformat(),
                "beneish_m": round(m, 4),
                "reconciled": False,
            }

        return self._beliefs[symbol]

    # ── Internal: Proxy belief (Giai doan 1) ──

    def _compute_proxy_belief(self, symbol: str, target_date: str, conn) -> dict:
        """Tinh P(S_Entity) tu proxy market data (Volume Anomaly, RS, Volatility)."""
        prior = 0.5  # Prior mac dinh (chua co BCTC)

        # Volume anomaly
        vr, rs_z = self._volume_anomaly(symbol, target_date, conn)
        anomaly_streak = self._count_anomaly_streak(symbol, target_date, conn)

        if anomaly_streak >= ANOMALY_CONSECUTIVE_DAYS:
            if vr >= VOLUME_RATIO_ALERT and rs_z <= RS_Z_ALERT:
                vp = PENALTY_SEVERE
            else:
                vp = PENALTY_MODERATE
        else:
            vp = 1.0

        # Volatility contraction proxy (surrogate cho Interest Coverage)
        vol_proxy = self._volatility_proxy(symbol, target_date, conn)

        P = prior * vp * vol_proxy

        return {
            "symbol": symbol,
            "P": round(max(0.01, min(1.0, P)), 4),
            "prior": prior,
            "volume_penalty": round(vp, 4),
            "beneish_penalty": 1.0,
            "volatility_proxy": round(vol_proxy, 4),
            "anomaly_streak": anomaly_streak,
            "volume_ratio": round(vr, 2),
            "rs_z": round(rs_z, 4),
            "last_updated": target_date,
            "beneish_m": None,
            "reconciled": False,
        }

    def _volume_anomaly(self, symbol: str, target_date: str, conn,
                        window: int = 20) -> tuple[float, float]:
        """Tinh Volume_Ratio va RS_Z cho symbol tai target_date."""
        rows = conn.execute(
            f"SELECT date, volume, close FROM daily_ohlcv WHERE symbol = ? "
            f"AND date <= ? ORDER BY date DESC LIMIT {window + 5}",
            (symbol, target_date),
        ).fetchall()
        if len(rows) < window + 1:
            return 1.0, 0.0

        closes = [r[2] for r in rows[:window] if r[2] is not None]
        volumes = [r[1] for r in rows[:window] if r[1] is not None]
        today_vol = rows[0][1] if rows[0][1] is not None else 0

        if not volumes or not closes:
            return 1.0, 0.0
        med_vol = float(np.median(volumes))
        vr = today_vol / med_vol if med_vol > 0 else 1.0

        # RS Z-score: (close_today - mean_closes) / std_closes
        mean_c = float(np.mean(closes))
        std_c = float(np.std(closes)) or 1.0
        rs_z = (closes[0] - mean_c) / std_c

        return vr, rs_z

    def _count_anomaly_streak(self, symbol: str, target_date: str, conn,
                               window: int = 20) -> int:
        """Dem so phien lien tiep co volume anomaly truoc target_date.

        Tinh VR cho tung ngay bang rolling median 20 phien.
        """
        rows = conn.execute(
            f"SELECT date, volume, close FROM daily_ohlcv WHERE symbol = ? "
            f"AND date <= ? ORDER BY date DESC LIMIT {window + ANOMALY_CONSECUTIVE_DAYS + 5}",
            (symbol, target_date),
        ).fetchall()
        if len(rows) < window + 2:
            return 0

        streak = 0
        for i in range(len(rows) - window):
            chunk = rows[i:i + window]
            vols = [r[1] for r in chunk if r[1] is not None]
            if len(vols) < window // 2:
                break
            med_vol = float(np.median(vols)) if vols else 1.0
            today_vol = rows[i][1] if rows[i][1] is not None else 0
            today_close = rows[i][2] if rows[i][2] is not None else 0
            vr_i = today_vol / med_vol if med_vol > 0 else 1.0

            # RS Z tu window
            closes = [r[2] for r in chunk if r[2] is not None]
            mean_c = float(np.mean(closes))
            std_c = float(np.std(closes)) or 1.0
            rs_z_i = (today_close - mean_c) / std_c

            if vr_i >= VOLUME_RATIO_THRESHOLD and rs_z_i <= RS_Z_THRESHOLD:
                streak += 1
            else:
                break
        return min(streak, 5)

    def _volatility_proxy(self, symbol: str, target_date: str, conn,
                           window: int = 20) -> float:
        """Volatility contraction proxy — surrogate cho Interest Coverage.

        ATR giam = volatility contraction = co the la dinh.
        Tra ve he so [0.5, 1.0], thap hon = nguy co cao hon.
        """
        rows = conn.execute(
            f"SELECT high, low, close FROM daily_ohlcv WHERE symbol = ? "
            f"AND date <= ? ORDER BY date DESC LIMIT {window + 5}",
            (symbol, target_date),
        ).fetchall()
        if len(rows) < window + 1:
            return 1.0

        # Tinh ATR ngan gon
        atrs = []
        for i in range(len(rows) - 1):
            h, l, c = rows[i][0] or 0, rows[i][1] or 0, rows[i + 1][2] or 0
            tr = max(h - l, abs(h - c), abs(l - c))
            atrs.append(tr)

        atr_recent = float(np.mean(atrs[:5])) if len(atrs) >= 5 else float(np.mean(atrs))
        atr_history = float(np.mean(atrs)) if atrs else 1.0

        if atr_history > 0:
            ratio = atr_recent / atr_history
            if ratio < 0.7:
                return 0.6  # Volatility contraction manh
            elif ratio < 0.85:
                return 0.8
        return 1.0
