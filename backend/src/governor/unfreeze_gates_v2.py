"""unfreeze_gates_v2.py — SHADOW v2 cho 3 gate "unfreeze" (chua cat-over).

SHADOW_ONLY = True:
  - Module nay CHI doc du lieu va IN bao cao. nine_gates.py (live) khong bi sua.
  - Cat-over chi xay ra sau: (a) 5 phien shadow chay song song, (b) backtest lich su,
    (c) user xac nhan nguong (dac biet doc nguyen cua G3), (d) commit rieng.

3 gate v2 — NGUONG DA CHOT theo phan quyet cua user (2026-09-30):
  G1  ADV3/ADV20 >= 1.4x  HOAC  phien bung no
      (VNINDEX ret >= +1.5%  VA  volume >= 1.3 x ADV20)
  G2  adv_share (ty le ma TANG THUC tren stable universe 618 ma) >= 50.0%
      giu >= 2 phien lien tiep (loai phien giat 1 ngay).
      BAC BO regime_history.breadth_pct (28.7% — do ty le ma tren MA, do tre 3-4 tuan,
      thua phien o vung day) lam dieu kien. Delta share chi in THAM KHAO.
  G3  CANONICAL = (spread level VGB10Y-US10Y >= -30bps)
      HOAC  bo ba macro: US10Y < 4.50%  VA  USD_VND < 25800  VA  DXY < MA50.
      READING_A (delta 5 phien) da bi BAC BO — chi in tham khao.
  Ladder reset: so gate xuong <=1 -> target allocation ve 0% NGAY; tranche1 da mua ->
      khong mua them, trailing stop -5%..-7% tren gia von, cham stop -> ve 100% cash.
  Band-breach: phien vuot nguong 8% -> flag + dung cot '_clean'; KHONG backfill nen thô
      (quarantine). Chuan hoa Corporate Actions = muc B4 rieng, xu ly sau.

Kiem nhan du lieu (2026-09-29, doc tu screener_cache.db mode=ro):
  - market_wide_breadth_daily.breadth_pct = Δ(ad-dj)/total*100 (KHONG phai ty le tang):
    29/09 = 0.46, ty le tang that = 330/(330+326) = 50.3%.
  - regime_history.breadth_pct = diem so rieng (29/09 = 28.7) — khac hoan toan,
    va regime_history THUA phien (09-18..09-23 thieu) -> khong dung cho streak.
  - daily_ohlcv khong phu hop deu tung phien (09-24/09-25 co 1.516 ma, 09-29 co 872)
    -> turnover tinh tren "stable universe" = ma co du lieu ca 20 phien cuoi (618 ma).

Chay:
  python -m governor.unfreeze_gates_v2            (tu backend/src tren sys.path)
  python backend/src/governor/unfreeze_gates_v2.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SHADOW_ONLY = True
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
DATA_DIR = PROJECT_ROOT / "backend" / "data"

THRESHOLDS = {
    "g1_adv3_adv20": 1.4,
    "g1_breakout_index_ret_pct": 1.5,
    "g1_breakout_vol_mult": 1.3,
    "g1_adv20_sessions": 20,
    "g2_share_pct": 50.0,
    "g2_streak": 2,
    "g2_delta_sessions": 3,
    "g2_delta_pp": 25.0,
    "g3_level_bps": -30.0,
    "g3_delta_bps": -30.0,
    "g3_spread_window": 5,
    "g3_dxy_ma": 50,
    "g3_us10y_ceiling_pct": 4.50,
    "g3_usdvnd_ceiling": 25800.0,
}
TRANCHES = (0, 30, 70, 100)
RESET_RULE = (
    "so gate xuong <=1/3 -> target allocation RESET ve 0% NGAY (khong giu nguyen 30%); "
    "neu tranche1 da mua: khong mua them (cam trung binh gia xuong), bat trailing stop "
    "-5%..-7% tren gia von; cham stop -> thanh ly dut khoat ve 100% cash"
)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def adv_ratio(turnovers: list[float], window: int | None = None) -> dict | None:
    """ADV3/ADV20 tren chuoi turnover (van cuoi = phien moi nhat). Thieu du lieu -> None."""
    window = window or THRESHOLDS["g1_adv20_sessions"]
    if len(turnovers) < window or window < 3:
        return None
    adv20 = mean(turnovers[-window:])
    adv3 = mean(turnovers[-3:])
    if not adv20 or adv20 <= 0:
        return None
    return {"adv3": adv3, "adv20": adv20, "ratio": adv3 / adv20}


def index_breakout(index_rows: list[dict]) -> dict:
    """Phien bung no: VNINDEX ret >= +1.5% VA vol >= 1.3 x ADV20 (vol chi so).

    ADV20 cho phep so = trung binh 20 phien TRUOC phien moi nhat (khong tinh hom nay)
    de khong "tu cham diem" cua chinh no.
    NGUYEN MO HUONG: "Vol" trong directive co the la volume chi so (doc nay) hoac
    turnover thi truong — ca hai deu duoc doc ro, khong chon thay user.
    """
    rows = [r for r in index_rows if r.get("ret_pct") is not None and r.get("volume") is not None]
    n = THRESHOLDS["g1_adv20_sessions"]
    if len(rows) < n + 1:
        return {"available": False, "pass": False, "reason": "index: thieu du lieu ADV20"}
    latest = rows[-1]
    adv20 = mean([r["volume"] for r in rows[-(n + 1) : -1]])
    vol_mult = latest["volume"] / adv20 if adv20 else None
    ret_ok = latest["ret_pct"] >= THRESHOLDS["g1_breakout_index_ret_pct"]
    vol_ok = vol_mult is not None and vol_mult >= THRESHOLDS["g1_breakout_vol_mult"]
    return {
        "available": True,
        "pass": ret_ok and vol_ok,
        "ret_pct": latest["ret_pct"],
        "vol_mult": vol_mult,
        "ret_ok": ret_ok,
        "vol_ok": vol_ok,
    }


def gate1(turnovers: list[float], index_rows: list[dict]) -> dict:
    ratio = adv_ratio(turnovers)
    brk = index_breakout(index_rows)
    ratio_pass = bool(ratio and ratio["ratio"] >= THRESHOLDS["g1_adv3_adv20"])
    pass_ = ratio_pass or bool(brk.get("pass"))
    ratio_txt = f"adv_ratio={ratio['ratio']:.2f}x (nguong {THRESHOLDS['g1_adv3_adv20']}x)" if ratio else "adv_ratio=NO_DATA"
    brk_txt = (
        f"breakout: ret={brk.get('ret_pct')}% vol_mult={None if brk.get('vol_mult') is None else round(brk['vol_mult'], 2)}"
        if brk.get("available")
        else f"breakout=NO_DATA ({brk.get('reason')})"
    )
    return {
        "gate": "G1",
        "pass": pass_,
        "reason": f"{ratio_txt}; {brk_txt}",
        "branches": {"adv_ratio": ratio_pass, "breakout": bool(brk.get("pass"))},
        "readings": {"adv": ratio, "breakout": brk},
    }


def adv_share_series(rows: list[tuple]) -> list[dict]:
    """[(date, advances, declines, ...)] -> [{date, share}] (ty le tang; thieu tong -> bo qua)."""
    out = []
    for row in rows:
        date, advances, declines = row[0], row[1], row[2]
        if advances is None or declines is None:
            continue
        total = advances + declines
        if total <= 0:
            continue
        out.append({"date": date, "share": 100.0 * advances / total, "net_pp": row[3] if len(row) > 3 else None})
    return out


def gate2(share_series: list[dict]) -> dict:
    """adv_share >= 50% giu >= 2 phien lien tiep. Delta chi THAM KHAO (khong quyet dinh gate)."""
    need = THRESHOLDS["g2_streak"]
    delta_sessions = THRESHOLDS["g2_delta_sessions"]
    if len(share_series) < need:
        return {
            "gate": "G2",
            "pass": False,
            "reason": "thieu du lieu breadth",
            "branches": {"streak": False, "delta_ref": False},
            "readings": {},
        }
    tail = share_series[-need:]
    streak_ok = all(s["share"] >= THRESHOLDS["g2_share_pct"] for s in tail)
    streak = 0
    for s in reversed(share_series):
        if s["share"] >= THRESHOLDS["g2_share_pct"]:
            streak += 1
        else:
            break
    latest = share_series[-1]
    delta = None
    delta_ref = False
    if len(share_series) >= delta_sessions + 1:
        base = share_series[-(delta_sessions + 1)]
        delta = latest["share"] - base["share"]
        delta_ref = delta >= THRESHOLDS["g2_delta_pp"]
    reason = f"share={latest['share']:.1f}% (nguong {THRESHOLDS['g2_share_pct']}%), streak={streak}/{need}"
    if delta is not None:
        reason += f" | THAM KHAO delta({delta_sessions}p)={delta:+.1f}pp (khong quyet dinh gate)"
    return {
        "gate": "G2",
        "pass": streak_ok,
        "reason": reason,
        "branches": {"streak": streak_ok, "delta_ref": delta_ref},
        "readings": {
            "latest_share": latest["share"],
            "streak": streak,
            "delta_pp": delta,
            "window": [share_series[-(delta_sessions + 1)]["date"], latest["date"]]
            if delta is not None
            else [latest["date"], latest["date"]],
        },
    }


def spread_series(vgb_rows: list[tuple], us_rows: list[tuple]) -> list[dict]:
    """Ghep VGB10Y/US10Y theo ngay -> [{date, vgb, us10y, bps}] tang dan."""
    us = {d: v for d, v in us_rows if v is not None}
    out = []
    for date, vgb in vgb_rows:
        if vgb is None or date not in us:
            continue
        out.append({"date": date, "vgb": vgb, "us10y": us[date], "bps": (vgb - us[date]) * 100.0})
    out.sort(key=lambda r: r["date"])
    return out


def gate3(spreads: list[dict], dxy_rows: list[tuple], macro: dict | None = None) -> dict:
    """CANONICAL (da chot): level spread >= -30bps  HOAC  bo ba macro dong thap.

    bo ba macro = US10Y < 4.50% VA USD_VND < 25800 VA DXY < MA50.
    READING_A (delta 5 phien) chi in THAM KHAO — da bi bac bo (nham dau: delta van >= -30bps
    trong khi level am sau). Du lieu thieu -> nhanh chua duoc tinh la True; ghi ro `missing`.
    """
    window = THRESHOLDS["g3_spread_window"]
    ma_n = THRESHOLDS["g3_dxy_ma"]
    macro = macro or {}
    readings: dict = {}
    level_ok = dxy_ok = False
    missing: list[str] = []

    if len(spreads) >= window + 1:
        latest = spreads[-1]
        base = spreads[-(window + 1)]
        delta = latest["bps"] - base["bps"]
        level = latest["bps"]
        level_ok = level >= THRESHOLDS["g3_level_bps"]
        readings.update(
            {"spread_bps": level, "delta_bps_ref": delta, "spread_window": [base["date"], latest["date"]], "window_n": window}
        )
    else:
        readings["spread_error"] = f"thieu du lieu spread (co {len(spreads)}, can {window + 1})"
        missing.append("spread")

    dxy_vals = [v for _, v in dxy_rows if v is not None]
    if len(dxy_vals) >= ma_n:
        ma = mean(dxy_vals[-ma_n:])
        latest_dxy = dxy_vals[-1]
        dxy_ok = latest_dxy < ma
        readings.update({"dxy": latest_dxy, "dxy_ma": ma, "dxy_ma_n": ma_n, "dxy_below_ma": dxy_ok})
    else:
        readings["dxy_error"] = f"thieu du lieu DXY (co {len(dxy_vals)}, can {ma_n})"
        missing.append("dxy")

    us10y = macro.get("us10y")
    usdvnd = macro.get("usdvnd")
    us10y_ok = us10y is not None and us10y < THRESHOLDS["g3_us10y_ceiling_pct"]
    usdvnd_ok = usdvnd is not None and usdvnd < THRESHOLDS["g3_usdvnd_ceiling"]
    if us10y is None:
        missing.append("us10y")
    if usdvnd is None:
        missing.append("usdvnd")
    readings.update(
        {
            "us10y": us10y,
            "us10y_ceiling_pct": THRESHOLDS["g3_us10y_ceiling_pct"],
            "usdvnd": usdvnd,
            "usdvnd_ceiling": THRESHOLDS["g3_usdvnd_ceiling"],
        }
    )
    macro_ok = us10y_ok and usdvnd_ok and dxy_ok
    pass_ = level_ok or macro_ok
    return {
        "gate": "G3",
        "pass": pass_,
        "partial_data": bool(missing),
        "missing": missing,
        "reason": (
            f"CANONICAL level>={THRESHOLDS['g3_level_bps']}bps: {level_ok}"
            + (f" ({readings.get('spread_bps'):.1f}bps)" if "spread_bps" in readings else "")
            + f" | bo ba macro (US10Y<{THRESHOLDS['g3_us10y_ceiling_pct']}%: {us10y_ok}, "
            f"USD_VND<{THRESHOLDS['g3_usdvnd_ceiling']}: {usdvnd_ok}, DXY<MA{ma_n}: {dxy_ok}) -> {macro_ok}"
            + (f" | NO_DATA: {','.join(missing)}" if missing else "")
            + (f" | THAM KHAO delta({window}p)={readings['delta_bps_ref']:+.1f}bps" if "delta_bps_ref" in readings else "")
        ),
        "branches": {"spread_level": level_ok, "macro_triple": macro_ok, "dxy_ma": dxy_ok},
        "readings": readings,
    }


def deployment_ladder(n_pass: int, regime: str | None, tranche1_in_profit: bool | None) -> dict:
    """Mechanical Deployment Ladder (nguon: ke hoach cua user — khong phai tuy chon cua code).

      Tranche 1  30%  : 2/3 gate PASS
      Tranche 2  70%  : 3/3 gate PASS  VA tranche1 dang co LAI DEM
      Tranche 3 100%  : 3/3 gate PASS  VA tranche1 dang co LAI  VA regime = UPTREND
      <=1 gate PASS   : 0% (100% CASH DEFENSE)

    tranche1_in_profit = None (NO_DATA) -> khong nang bac (guong an toan) va ghi ro
    vao `blocked_by`; regime khong ro -> khong len 100%.
    """
    conditions = {
        "n_pass": n_pass,
        "regime": regime,
        "regime_uptrend": str(regime).upper() == "UPTREND" if regime else None,
        "tranche1_in_profit": tranche1_in_profit,
    }
    if n_pass <= 1:
        return {
            "tranche_pct": 0,
            "mode": "100% CASH DEFENSE",
            "action": "HOLD_CASH_EARN_INTEREST",
            "reset_rule": RESET_RULE,
            "conditions": conditions,
            "blocked_by": [],
        }
    if n_pass == 2:
        return {
            "tranche_pct": 30,
            "mode": "TRANCHE 1 (PROBE)",
            "action": "DEPLOY_30PCT_MANDATORY_IN_3_SESSIONS",
            "reset_rule": RESET_RULE,
            "conditions": conditions,
            "blocked_by": [],
        }

    in_profit = tranche1_in_profit is True
    uptrend = str(regime).upper() == "UPTREND" if regime else False
    blocked: list[str] = []
    if not in_profit:
        blocked.append(
            "tranche1_in_profit=NO_DATA (chua co state file)"
            if tranche1_in_profit is None
            else "tranche1_in_profit=False (khong co loi dem)"
        )
    if not uptrend:
        blocked.append(f"regime khong phai UPTREND (hien tai: {regime or 'NO_DATA'})")

    if in_profit and uptrend:
        return {
            "tranche_pct": 100,
            "mode": "TRANCHE 3 (FULL DEPLOYMENT)",
            "action": "DEPLOY_100PCT_FULL",
            "reset_rule": RESET_RULE,
            "conditions": conditions,
            "blocked_by": [],
        }
    if in_profit:
        return {
            "tranche_pct": 70,
            "mode": "TRANCHE 2 (EXPANSION)",
            "action": "DEPLOY_70PCT_AGGRESSIVE",
            "reset_rule": RESET_RULE,
            "conditions": conditions,
            "blocked_by": blocked,
        }
    return {
        "tranche_pct": 30,
        "mode": "TRANCHE 1 (PROBE) — 3/3 gate nhung chua du dieu kien len bac 2",
        "action": "HOLD_30PCT_PENDING_CONDITIONS",
        "reset_rule": RESET_RULE,
        "conditions": conditions,
        "blocked_by": blocked,
    }


def passes_summary(gates: list[dict]) -> tuple[bool, int]:
    n = sum(1 for g in gates if g["pass"])
    return n >= 2, n


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def load_market(db: Path, as_of: str) -> dict:
    """Doc turnover (stable universe), index, breadth, spread, DXY cho phien as_of."""
    con = _ro(db)
    try:
        sessions = [r[0] for r in con.execute("SELECT DISTINCT date FROM daily_ohlcv WHERE date <= ? ORDER BY date", (as_of,))]
        if not sessions:
            raise RuntimeError(f"khong co phien nao <= {as_of}")
        need = THRESHOLDS["g1_adv20_sessions"] + 1
        window_sessions = sessions[-need:]
        if len(window_sessions) < need:
            raise RuntimeError(f"chi co {len(window_sessions)} phien, can {need}")
        marks = ",".join("?" * len(window_sessions))
        stable_sql = (
            f"SELECT symbol FROM daily_ohlcv WHERE date IN ({marks}) "
            f"GROUP BY symbol HAVING COUNT(DISTINCT date) = {len(window_sessions)}"
        )
        turnover_rows = con.execute(
            f"SELECT date, SUM(close*volume)/1e9 FROM daily_ohlcv "
            f"WHERE date IN ({marks}) AND symbol IN ({stable_sql}) GROUP BY date ORDER BY date",
            window_sessions + window_sessions,
        ).fetchall()

        idx_rows = [
            {"date": d, "close": c, "volume": v, "ret_pct": r}
            for d, c, v, r in con.execute(
                "SELECT date, close, volume, "
                "(close/LAG(close) OVER (ORDER BY date)-1)*100 FROM daily_ohlcv "
                "WHERE symbol='VNINDEX' AND date <= ? ORDER BY date",
                (as_of,),
            )
        ]

        breadth_rows = con.execute(
            "SELECT date, advances, declines, breadth_pct FROM market_wide_breadth_daily "
            "WHERE date <= ? AND (degraded IS NULL OR degraded = 0) ORDER BY date",
            (as_of,),
        ).fetchall()

        spread_rows = con.execute(
            "SELECT v.date, v.value, u.value FROM "
            "(SELECT date, value FROM macro_history WHERE variable='VGB10Y' AND date <= ?) v "
            "JOIN (SELECT date, value FROM macro_history WHERE variable='US10Y' AND date <= ?) u "
            "ON u.date = v.date ORDER BY v.date",
            (as_of, as_of),
        ).fetchall()

        dxy_rows = con.execute(
            "SELECT date, value FROM macro_history WHERE variable='DXY' AND value IS NOT NULL AND date <= ? ORDER BY date",
            (as_of,),
        ).fetchall()

        macro_latest: dict = {}
        for var, key in (("US10Y", "us10y"), ("USD_VND", "usdvnd")):
            row = con.execute(
                "SELECT date, value FROM macro_history WHERE variable = ? AND date <= ? AND value IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                (var, as_of),
            ).fetchone()
            macro_latest[key] = list(row) if row else None

        regime_rows = con.execute(
            "SELECT date, breadth_pct, status FROM regime_history "
            "WHERE date <= ? AND is_degraded = 0 ORDER BY date DESC LIMIT 2",
            (as_of,),
        ).fetchall()
    finally:
        con.close()

    return {
        "as_of": as_of,
        "turnovers": [t for _, t in turnover_rows],
        "turnover_dates": [d for d, _ in turnover_rows],
        "index_rows": idx_rows,
        "breadth_rows": breadth_rows,
        "spread_rows": spread_rows,
        "dxy_rows": dxy_rows,
        "macro_latest": macro_latest,
        "regime_breadth": regime_rows,
        "sessions_total": len(sessions),
    }


def v1_snapshot(data: dict) -> dict:
    """Anh xa v1 = gate1/gate3/gate4 trong backend/scripts/nine_gates.py (khong goi lai)."""
    live = DATA_DIR / "system_state.json"
    live_lock = True
    if live.exists():
        try:
            live_lock = bool(json.loads(live.read_text(encoding="utf-8")).get("portfolio_critical_lock", True))
        except OSError, ValueError:
            live_lock = True
    conf_score = None
    conf_file = DATA_DIR / "confidence.json"
    if conf_file.exists():
        try:
            d = json.loads(conf_file.read_text(encoding="utf-8"))
            conf_score = float(d.get("score", d.get("confidence", 0)))
        except OSError, ValueError:
            conf_score = None
    bvals = [(d, b) for d, b, *_ in data["regime_breadth"] if b is not None]
    bvals = list(reversed(bvals))[:2]
    g3_ok = len(bvals) >= 2 and all(b > 40 for _, b in bvals)
    today = data["as_of"]
    conf_ok = conf_score is not None and conf_score >= 30.0
    return {
        "G1_entry_lock": {"pass": not live_lock, "reason": f"live_critical_lock={live_lock}"},
        "G3_breadth_gt40x2": {"pass": g3_ok, "reason": f"regime_history last2={bvals}"},
        "G4_confidence_ge30": {"pass": conf_ok, "reason": f"confidence.json={conf_score} ts={today}"},
    }


TRANCHE1_STATE = DATA_DIR / "tranche1_state.json"


def load_tranche1_state(path: Path | None = None) -> bool | None:
    """Trang thai loi/dong cua Tranche 1.

    Khong co state file -> None (NO_DATA): ladder KHONG nang bac (guong an toan)
    va ghi ro vao blocked_by. Khong suy doan gia tri.
    """
    p = path or TRANCHE1_STATE
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if "in_profit" not in d:
        return None
    return bool(d["in_profit"])


def evaluate(db: Path, as_of: str | None = None) -> dict:
    if not db.exists():
        raise FileNotFoundError(db)
    if as_of is None:
        con = _ro(db)
        try:
            as_of = con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()[0]
        finally:
            con.close()
    data = load_market(db, as_of)
    shares = adv_share_series(data["breadth_rows"])
    # VGB10Y bị gắn cờ chuỗi SUY BIẾN (hằng số) → nhánh spread bị VÔ HIỆU, để gate coi như
    # thiếu dữ liệu (fail-closed) thay vì tính trên một hằng số. Nhánh "bộ ba macro" không
    # dùng VGB10Y nên vẫn được đánh giá bình thường.
    # Import cục bộ: tương thích CẢ hai cách chạy —
    #   `python -m governor.unfreeze_gates_v2` (backend/src trên sys.path)
    #   `python backend/src/governor/unfreeze_gates_v2.py` (thêm backend/ vào sys.path)
    try:
        from src.database.series_quality import degenerate_info
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.database.series_quality import degenerate_info

    spreads = (
        []
        if degenerate_info("VGB10Y") is not None
        else spread_series(
            [(d, v) for d, v, _ in data["spread_rows"]],
            [(d, u) for d, _, u in data["spread_rows"]],
        )
    )
    macro_raw = data.get("macro_latest") or {}
    macro = {k: (v[1] if v else None) for k, v in macro_raw.items()}
    g1 = gate1(data["turnovers"], data["index_rows"])
    g2 = gate2(shares)
    g3 = gate3(spreads, data["dxy_rows"], macro)
    gates = [g1, g2, g3]
    two_of_three, n_pass = passes_summary(gates)
    regime_status = data["regime_breadth"][0][2] if data["regime_breadth"] else None
    lad = deployment_ladder(n_pass, regime_status, load_tranche1_state())
    v1 = v1_snapshot(data)
    v1_passes = sum(1 for r in v1.values() if r["pass"])
    return {
        "shadow": SHADOW_ONLY,
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(db),
        "thresholds": THRESHOLDS,
        "gates": gates,
        "passes": n_pass,
        "need_2_of_3": two_of_three,
        "ladder": lad,
        "v1": v1,
        "divergence": {"v1_passes": v1_passes, "v2_passes": n_pass, "divergent": v1_passes != n_pass},
        "data_notes": {
            "turnover_sessions": len(data["turnovers"]),
            "turnover_dates": [data["turnover_dates"][0], data["turnover_dates"][-1]] if data["turnover_dates"] else [],
            "breadth_sessions": len(shares),
            "spread_obs": len(spreads),
            "dxy_obs": len([v for _, v in data["dxy_rows"]]),
            "regime_breadth_last2": data["regime_breadth"],
            "regime_status": regime_status,
            "macro_latest": {k: (list(v) if v else None) for k, v in macro_raw.items()},
            "tranche1_in_profit": lad["conditions"]["tranche1_in_profit"],
        },
    }


def format_report(result: dict) -> str:
    lines = ["=" * 78, "  UNFREEZE GATES v2 — SHADOW (khong anh huong nine_gates live)", "=" * 78]
    lines.append(f"  as_of={result['as_of']} | generated={result['generated_at']} | db={result['db']}")
    lines.append(f"  data: {result['data_notes']}")
    lines.append("-" * 78)
    lines.append("  v1 (mirror nine_gates.py) vs v2 (shadow):")
    for name, res in result["v1"].items():
        lines.append(f"    v1 {name:24s} {'PASS' if res['pass'] else 'FAIL'}  {res['reason']}")
    for g in result["gates"]:
        lines.append(f"    v2 {g['gate']:24s} {'PASS' if g['pass'] else 'FAIL'}  {g['reason']}")
    lines.append("-" * 78)
    lad = result["ladder"]
    div = result["divergence"]
    lines.append(f"  Gates v2: {result['passes']}/3 | can 2/3: {'DAT' if result['need_2_of_3'] else 'KHONG DAT'}")
    lines.append(f"  DEPLOYMENT LADDER: tranche = {lad['tranche_pct']}% | {lad['mode']} | {lad['action']}")
    lines.append(f"  dieu kien: {lad['conditions']}")
    if lad["blocked_by"]:
        lines.append(f"  BI CHAN BOI: {lad['blocked_by']}")
    lines.append(f"  RESET RULE: {lad['reset_rule']}")
    lines.append(
        f"  DIVERGENCE v1/v2: v1 {div['v1_passes']}/3 vs v2 {div['v2_passes']}/3 -> "
        + ("CO LECH — can xem truoc moi cut-over" if div["divergent"] else "khong lech so luong PASS")
    )
    lines.append("  (Mandatory Execution chi xay ra khi tranche > 0 — van CHUA duoc kich hoat)")
    lines.append("=" * 78)
    return "\n".join(str(x) for x in lines)


def append_log(path: Path, result: dict) -> None:
    """Shadow hook: append 1 dong JSONL (append, khong pha huy lich su truoc).

    Day la noi ket noi voi EOD pipeline neu user cho phep goi tu daily_close —
    module nay CHUA tu sua pipeline (van shadow-only).
    """
    line = {
        "as_of": result["as_of"],
        "generated_at": result["generated_at"],
        "v1_passes": result["divergence"]["v1_passes"],
        "v2_passes": result["divergence"]["v2_passes"],
        "divergent": result["divergence"]["divergent"],
        "tranche_pct": result["ladder"]["tranche_pct"],
        "mode": result["ladder"]["mode"],
        "action": result["ladder"]["action"],
        "gates": {g["gate"]: bool(g["pass"]) for g in result["gates"]},
        "blocked_by": result["ladder"]["blocked_by"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--as-of", default=None, help="phien tinh toan (mac dinh: phien moi nhat trong DB)")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--log", type=Path, default=None, help="append 1 dong JSONL vao shadow log (khong mac dinh)")
    args = ap.parse_args(argv)
    result = evaluate(args.db, args.as_of)
    print(format_report(result))
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"da ghi JSON: {args.json}")
    if args.log:
        append_log(args.log, result)
        print(f"da append shadow log: {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
