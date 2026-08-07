"""Market Intelligence Report — single command monthly scan.

Usage::

    python -m backend.src.tools.market_report --month 2026-06 --lang vi
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent
        root = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root = current
                break
            current = current.parent
    for p in (root, root / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("market_report")


def run_cagl_snapshot():
    from src.core.psr.snapshot import SystemStateSnapshotter

    snapper = SystemStateSnapshotter()
    snap = snapper.capture()
    snapper.persist(snap)
    route_count = (snap.api_routes or {}).get("count", 0)
    return {
        "snapshot_id": snap.snapshot_id,
        "route_count": route_count,
        "api_contract_hash": snap.api_contract_hash or "N/A",
        "snapshot_hash": snap.snapshot_hash,
        "timestamp": snap.timestamp,
    }


def run_macro_scan(target_date: str):
    from src.services.macro_service import get_macro_status

    data = get_macro_status(target_date=target_date)
    return data


def run_gold_scan():
    from src.services.macro.gold_service import get_gold_cognition_layer, get_gold_dashboard
    from src.services.macro.gold_world_service import fetch_world_gold_live, is_gold_conflicted

    dash = get_gold_dashboard()
    cognition = get_gold_cognition_layer()
    world = fetch_world_gold_live()
    conflicted = is_gold_conflicted()
    return {
        "dashboard": dash,
        "cognition": cognition,
        "world_gold": world,
        "conflicted": conflicted,
    }


def run_backtest_regime():
    import pandas as pd
    from src.database.db_core import get_connection
    from src.engine.backtest_engine import BacktestAlpha

    engine = BacktestAlpha()
    try:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT date, high, low, adj_close as bench_close FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date",
                conn,
            )
        if df.empty:
            return {"status": "UNKNOWN", "error": "no VNINDEX data in daily_ohlcv"}
        regime_df = engine.detect_market_regime(df)
        latest = regime_df.iloc[-1]
        return {
            "status": latest.get("regime", "UNKNOWN"),
            "adx": round(float(latest.get("adx", 0)), 1),
            "adx_percentile": round(float(latest.get("adx_percentile", 0)), 3),
            "data_points": len(regime_df),
        }
    except Exception as e:
        return {"status": "UNKNOWN", "error": str(e)}


def run_psr_diff(snapshot_id: str):
    from src.core.psr.replay import DeterministicReplayEngine

    engine = DeterministicReplayEngine()
    result = engine.replay(snapshot_id)
    if result is None:
        return None
    return {
        "match": result.match,
        "diffs": [{"field": d.field, "match": d.match} for d in result.diffs],
        "duration_ms": result.replay_duration_ms,
    }


def format_field(key: str, value) -> str:
    labels = {
        "regime": "Thi trường",
        "gold": "Vang",
        "api": "API",
        "data_quality": "Chất lượng dữ liệu",
        "trust": "Tín nhiệm CAO",
        "market_state": "Trạng thái thị trường",
        "api_contract": "Hợp đồng API",
    }
    return labels.get(key, key.replace("_", " "))


def flow_drift_map(screener_results: dict | None) -> dict:
    """Group stocks by flow behaviour using existing screener signals only.

    Returns dict with 4 group keys, each a list of (symbol, reason) tuples.
    Zero new signals — only reclassifies existing vol_trend, composite,
    xac_nhan, and rs_streak columns from screener rdf.
    """
    groups = {"thu_hut_on_dinh": [], "dao_dong": [], "ro_ri": [], "rut_cau_truc": []}
    counts = {"thu_hut_on_dinh": 0, "dao_dong": 0, "ro_ri": 0, "rut_cau_truc": 0}

    if not screener_results:
        return {"groups": groups, "counts": counts}

    df = screener_results.get("rdf")
    if df is None or df.empty:
        return {"groups": groups, "counts": counts}

    for _, row in df.iterrows():
        vol = row.get("vol_trend", 0) or 0
        comp = row.get("composite", 0) or 0
        xn = str(row.get("xac_nhan", ""))
        streak = int(row.get("rs_streak", 0) or 0)
        rank_chg = int(row.get("rank_change", 0) or 0)

        sym = row.get("symbol", "?")
        sector = str(row.get("industry", ""))[:12]

        if vol > 0 and comp > 0.5 and xn in ("MANH", "TB") and streak >= 5:
            groups["thu_hut_on_dinh"].append((sym, f"vol={vol:+.0%}", sector))
            counts["thu_hut_on_dinh"] += 1
        elif vol < 0 and comp < 0.3 and xn == "YEU" and streak == 0 and rank_chg <= 0:
            groups["rut_cau_truc"].append((sym, f"composite={comp:.2f}", sector))
            counts["rut_cau_truc"] += 1
        elif vol < 0 and comp < 0.5:
            groups["ro_ri"].append((sym, f"vol={vol:+.0%}", sector))
            counts["ro_ri"] += 1
        else:
            groups["dao_dong"].append((sym, f"comp={comp:.2f}", sector))
            counts["dao_dong"] += 1

    # Keep top N per group for display
    for g in groups:
        groups[g] = groups[g][:8]

    return {"groups": groups, "counts": counts}


def drift_label(structure: dict | None, leadership: dict | None) -> str:
    """Market-level drift annotation from existing outputs only."""
    if not structure:
        return "KHONG XAC DINH"

    bdi = abs(structure.get("bdi_pct", 0))
    lcr = structure.get("lcr_pct", 0)
    signal = structure.get("bdi_signal", "CAN_BANG")

    red_decay = 0
    if leadership:
        decay = leadership.get("decay_analysis", [])
        red_decay = sum(1 for d in decay if d.get("decay_score", 0) >= 0.7)

    if bdi > 10 and lcr > 35 and signal != "CAN_BANG":
        return f"STRUCTURAL — index +{bdi:.0f}% lech breadth, LCR {lcr:.0f}%, {red_decay} tru lung lay"
    if bdi > 5 or lcr > 40 or red_decay >= 5:
        return f"TRANSIENT — {signal}, LCR {lcr:.0f}%, {red_decay} tru suy giam"
    if red_decay >= 2:
        return "NOISE — mot so tru bat on nhung chua co cau truc"
    return "NONE — thong so dong bo"


def translate_regime(regime: str) -> str:
    mapping = {
        "TRENDING": "xu hướng rõ",
        "RANGING": "đi ngang",
        "CRISIS": "khủng hoảng",
        "SIDEWAYS": "đi ngang",
        "BULL": "tăng",
        "BEAR": "giảm",
        "UNKNOWN": "không xác định",
    }
    return mapping.get(regime, regime)


def main():
    parser = argparse.ArgumentParser(description="Báo cáo thị trường tháng")
    parser.add_argument("--month", default=datetime.now().strftime("%Y-%m"), help="Tháng báo cáo (YYYY-MM)")
    parser.add_argument("--lang", choices=["en", "vi"], default="vi", help="Ngôn ngữ báo cáo")
    args = parser.parse_args()

    month = args.month
    is_vi = args.lang == "vi"

    print("=" * 56)
    if is_vi:
        print(f"  BAO CAO THI TRUONG THANG {month}")
        print(f"  Thoi gian: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    else:
        print(f"  MARKET REPORT — {month}")
        print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 56)
    print()

    # Stage 0: Data Integrity Guard
    from src.database.data_integrity import ensure_vnindex_integrity

    integrity = ensure_vnindex_integrity()
    if integrity["status"] == "FIXED":
        print(f"  [INDEX] Da sua {integrity['rows_fixed']} dong VNINDEX bi loi scale")
    elif integrity["status"] == "CORRUPT":
        print(f"  [INDEX] CANH BAO: VNINDEX dang bi loi — {integrity['detail']}")

    # Stage 0b: Market Structure Intelligence
    print("[0/7] Cau truc thi truong..." if is_vi else "[0/7] Market structure...")
    try:
        from src.engine.market_structure import analyse_market_structure

        structure = analyse_market_structure(lookback=60, top_n=10, verbose=True)
    except Exception as e:
        structure = None
        if is_vi:
            print(f"  Khong the phan tich cau truc: {e}")
        else:
            print(f"  Market structure unavailable: {e}")
    print()

    # Stage 1: CAGL snapshot
    print("[1/6] Snapshot hệ thống..." if is_vi else "[1/6] System snapshot...")
    snap = run_cagl_snapshot()
    print(f"  API: {snap['route_count']} endpoint | hash: {snap['api_contract_hash']}")
    print()

    # Stage 2: Macro FX scan
    print("[2/6] Vĩ mô & ngoại hối..." if is_vi else "[2/6] Macro & FX...")
    macro = run_macro_scan(month)
    if macro and isinstance(macro, dict):
        for k, v in list(macro.items())[:6]:
            print(f"  {k}: {v}")
    print()

    # Stage 3: Gold cognitive
    print("[3/6] Phân tích vàng..." if is_vi else "[3/6] Gold analysis...")
    gold = run_gold_scan()
    cog = gold.get("cognition", {})
    if cog:
        print(f"  Regime: {cog.get('regime', 'N/A')}")
        print(f"  Driver: {cog.get('driver', 'N/A')}")
        print(f"  Premium: {cog.get('domestic_premium', 'N/A')}")
    world = gold.get("world_gold")
    if world:
        print(f"  XAU/USD: {world}")
    print()

    # Stage 4: Backtest regime
    print("[4/6] Xu hướng thị trường..." if is_vi else "[4/6] Market regime...")
    regime = run_backtest_regime()
    if regime:
        r_status = regime.get("status", "UNKNOWN")
        if is_vi:
            r_status = translate_regime(r_status)
            print(f"  Trạng thái: {r_status}")
            if "adx" in regime:
                print(f"  ADX: {regime['adx']} | ADX percentile: {regime['adx_percentile']}")
            if "data_points" in regime:
                print(f"  Số phiên phân tích: {regime['data_points']}")
            if "error" in regime:
                print(f"  Lưu ý: {regime['error']}")
        else:
            print(f"  Regime: {r_status}")
            if "adx" in regime:
                print(f"  ADX: {regime['adx']} | ADX percentile: {regime['adx_percentile']}")
    print()

    # Stage 5: PSR drift
    print("[5/6] So sánh trạng thái..." if is_vi else "[5/6] PSR drift scan...")
    diff = run_psr_diff(snap["snapshot_id"])
    if diff:
        status = "ON DINH" if diff["match"] else "CO BIEN DONG"
        print(f"  Kết quả: {status}" if is_vi else f"  Result: {status}")
        for d in diff["diffs"]:
            icon = "+" if d["match"] else "x"
            label = format_field(d["field"], "")
            st = "on dinh" if d["match"] else "co bien dong"
            if is_vi:
                print(f"  {icon} {label}: {st}")
            else:
                print(f"  {icon} {label}: {'stable' if d['match'] else 'drift'}")
    else:
        print("  Không có snapshot trước để so sánh.")
    print()

    # Stage 6: Leadership Tracker
    print("[6/7] Phân tích mã dẫn dắt..." if is_vi else "[6/7] Leadership tracker...")
    try:
        from src.engine.leadership_tracker import calculate_leadership

        leadership = calculate_leadership(lookback=60, top_n=15)
    except Exception as e:
        leadership = None
        print(f"  ⚠️ Lỗi: {e}")
    print()

    # Stage 7: Stock Discovery Screener
    print("[7/7] Quét cơ hội thị trường..." if is_vi else "[7/7] Stock discovery scan...")
    try:
        from src.engine.screener import scan_market

        screener_results = scan_market(lookback=252, top_n=20)
    except Exception as e:
        screener_results = None
        print(f"  ⚠️ Lỗi: {e}")
    print()

    # Flow Drift Map (aggregation only, no new signals)
    if is_vi and screener_results is not None:
        fdm = flow_drift_map(screener_results)
        if fdm["counts"]["thu_hut_on_dinh"] + fdm["counts"]["rut_cau_truc"] > 0:
            print("-" * 56)
            print("  BAN DO LUONG TIEN THEO TRANG THAI LECH")
            print()
            c = fdm["counts"]
            total = sum(c.values()) or 1
            print(f"  {c['thu_hut_on_dinh']:3d} ma ({c['thu_hut_on_dinh'] / total * 100:5.1f}%)  Thuan hut on dinh")
            print(f"  {c['dao_dong']:3d} ma ({c['dao_dong'] / total * 100:5.1f}%)  Dao dong")
            print(f"  {c['ro_ri']:3d} ma ({c['ro_ri'] / total * 100:5.1f}%)  Ro ri")
            print(f"  {c['rut_cau_truc']:3d} ma ({c['rut_cau_truc'] / total * 100:5.1f}%)  Rut cau truc")
            print()
            for label, key, icon in [
                ("THU HUT ON DINH", "thu_hut_on_dinh", "  "),
                ("DAO DONG", "dao_dong", "  "),
                ("RO RI", "ro_ri", "  "),
                ("RUT CAU TRUC", "rut_cau_truc", "  "),
            ]:
                items = fdm["groups"][key]
                if items:
                    line = ", ".join(f"{s}" for s, _, sec in items[:6])
                    print(f"  {icon} {label}: {line}")
            print()
            print(f"  Tong phan tich: {total} ma")
            print("-" * 56)
            print()

    # Summary
    print("=" * 56)
    if is_vi:
        print("  TONG QUAN THANG", month)
        print()
        if regime:
            r = translate_regime(regime.get("status", "UNKNOWN"))
            print(f"  Thi truong: {r}")
        if structure:
            bdi_label = {"PHAN_KY_DUONG": "PHAN KY DUONG", "PHAN_KY_AM": "PHAN KY AM", "CAN_BANG": "CAN BANG"}
            print(f"  SBMI (ex-top10):  {structure['sbmi_pct']:+.1f}%")
            print(f"  EWMI (binh quan): {structure['ewmi_pct']:+.1f}%")
            print(f"  BDI: {structure['bdi_pct']:+.1f}% ({bdi_label.get(structure['bdi_signal'], 'N/A')})")
            print(f"  LCR: {structure['lcr_pct']:.1f}%")
        print(f"  Do lech cau truc: {drift_label(structure, leadership)}")
        print(f"  API: {snap['route_count']} endpoint - on dinh")
        if gold.get("world_gold"):
            print(f"  Vang TG: {gold['world_gold']}")
        if gold.get("cognition", {}).get("driver"):
            print(f"  Driver vang: {gold['cognition']['driver']}")
        if diff:
            print(f"  Hệ thống: {'on dinh' if diff['match'] else 'co bien dong'}")
        if leadership:
            conc = leadership.get("concentration", 0)
            turnover = leadership.get("market_turnover_bn", 0)
            print(f"  Tap trung dan dat: {conc}%")
            print(f"  Thanh khoan BQ 20d: {turnover:,.0f} ty")
            decay = leadership.get("decay_analysis", [])
            high_risk = [d["symbol"] for d in decay if d["decay_score"] >= 0.5]
            watch = [d["symbol"] for d in decay if 0.3 <= d["decay_score"] < 0.5]
            if high_risk:
                print(f"  🔴 Nguy co mat tru: {', '.join(high_risk)}")
            if watch:
                print(f"  🟡 Can theo doi: {', '.join(watch)}")
        if screener_results is not None:
            dandat = screener_results.get("dandat")
            moinoi = screener_results.get("moinoi")
            if dandat is not None:
                top5 = dandat.head(5)["symbol"].tolist()
                print(f"  🎯 Dan dat: {', '.join(top5)}")
            if moinoi is not None and not moinoi.empty:
                top5n = moinoi.head(5)["symbol"].tolist()
                print(f"  🔥 Moi noi: {', '.join(top5n)}")
    else:
        print(f"  {month} SUMMARY")
        print()
        if regime:
            print(f"  Regime: {regime.get('status', 'N/A')}")
        print(f"  Structure drift: {drift_label(structure, leadership)}")
        print(f"  API: {snap['route_count']} routes - stable")
        print(f"  Gold: {gold.get('world_gold', 'N/A')}")
        if diff:
            print(f"  System: {'stable' if diff['match'] else 'drift detected'}")
        if leadership:
            print(f"  Leadership concentration: {leadership.get('concentration', 0)}%")
        if screener_results is not None:
            dandat = screener_results.get("dandat")
            moinoi = screener_results.get("moinoi")
            if dandat is not None:
                top5 = dandat.head(5)["symbol"].tolist()
                print(f"  Top picks: {', '.join(top5)}")
            if moinoi is not None and not moinoi.empty:
                top5n = moinoi.head(5)["symbol"].tolist()
                print(f"  Rising: {', '.join(top5n)}")
    print("=" * 56)


if __name__ == "__main__":
    main()
