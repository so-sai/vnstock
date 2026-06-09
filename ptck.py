#!/usr/bin/env python
"""
ptck — PTCK_VNSTOCK CLI
========================
Single entrypoint cho mọi operation. 
Không viết script tạm. Dùng CLI này.

Usage:
  python ptck.py market
  python ptck.py regime
  python ptck.py report weekly
  python ptck.py daily-close
  python ptck.py telemetry reputation --refresh
  python ptck.py serve
  python ptck.py status
"""
import sys
import argparse
from pathlib import Path

# ── Windows encoding fix ──────────────────────────────
if sys.platform == "win32":
    sys.stdout = __import__('io').TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── Path setup ─────────────────────────────────────────
# Không thêm backend/src vào sys.path vì shadowing core/ namespace.
# Các module trong backend/src import qua alias src.
PROJECT_ROOT = Path(__file__).resolve().parent
backend_dir = PROJECT_ROOT / "backend"
for p in [backend_dir, PROJECT_ROOT / "core", PROJECT_ROOT]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
# Thêm backend/libs cho vnstock lib
libs_dir = backend_dir / "libs"
if str(libs_dir) not in sys.path:
    sys.path.insert(0, str(libs_dir))


def cmd_market(args):
    """Market snapshot: regime + breadth + flow overview."""
    from src.engine.regime_engine import detect_regime
    from src.core.market_state_coordinator import build_market_state
    
    regime = detect_regime()
    d = regime.get("details", {})
    
    print("=" * 60)
    print("  PTCK — MARKET SNAPSHOT")
    print("=" * 60)
    print(f"  Trạng thái:     {regime.get('status')} ({regime.get('regime_score'):.2f})")
    print(f"  Độ rộng:        {d.get('breadth_pct', 'N/A')}% (vận tốc: {d.get('breadth_momentum', 0):+.1f}%)")
    print(f"  ADX:            {d.get('adx', 'N/A')}")
    print(f"  ATR ratio:      {d.get('atr_ratio', 'N/A')} (biến động)")
    print(f"  VNINDEX vs MA200: {d.get('vnindex_vs_ma200', 'N/A')}")
    print(f"  MA50 slope:     {d.get('ma50_slope', 'N/A')}")
    print()
    
    # Driver state
    try:
        from src.engine.driver_normalizer import driver_state_from_engine_outputs
        ds = driver_state_from_engine_outputs(
            breadth_health=d.get("breadth_pct"),
            t_score=d.get("t_score"),
            v_score=d.get("v_score"),
        )
        print(f"  Driver trội:    {ds.dominant} ({ds.confidence:.1%})")
        print(f"  Entropy:        {ds.entropy:.2f}")
        for drv, wt in sorted(ds.distribution.items(), key=lambda x: -x[1]):
            print(f"    {drv:15s}: {wt:.1%}")
    except Exception as e:
        print(f"  Driver state:   {e}")
    
    print("=" * 60)


def cmd_regime(args):
    """Chi tiết regime analysis."""
    from src.engine.regime_engine import detect_regime
    
    regime = detect_regime()
    d = regime.get("details", {})
    
    print("=" * 60)
    print("  PTCK — REGIME ANALYSIS")
    print("=" * 60)
    print(f"  Trạng thái:     {regime.get('status')}")
    print(f"  Score:          {regime.get('regime_score'):.4f}")
    print(f"  Score raw:      {regime.get('regime_score_raw', 0):.4f}")
    print(f"  EMA alpha:      {regime.get('ema_alpha', 0):.2f}")
    print(f"  Ngày:           {regime.get('date', 'N/A')}")
    print()
    
    print("  ── Chi tiết ──")
    print(f"  B-Score (độ rộng): {d.get('b_score', 0):.4f}")
    print(f"  T-Score (xu hướng): {d.get('t_score', 0):.4f}")
    print(f"  V-Score (biến động): {d.get('v_score', 0):.4f}")
    print(f"  Độ rộng:        {d.get('breadth_pct', 'N/A')}%")
    print(f"  Breadth std 10d: {d.get('breadth_std_10d', 'N/A')}")
    print(f"  Breadth momentum: {d.get('breadth_momentum', 0):+.1f}%")
    print(f"  ADX:            {d.get('adx', 'N/A')}")
    print(f"  ATR ratio:      {d.get('atr_ratio', 'N/A')}")
    print()
    
    # Lịch sử regime
    try:
        from src.database.db_core import get_connection
        with get_connection() as conn:
            conn.row_factory = __import__('sqlite3').Row
            rows = conn.execute("""
                SELECT date, status, regime_score, breadth_pct, breadth_velocity
                FROM regime_history ORDER BY date DESC LIMIT 5
            """).fetchall()
        print("  ── 5 phiên gần nhất ──")
        for r in rows:
            print(f"    {r['date']}: {r['status']:10s} score={r['regime_score']:.2f} breadth={r['breadth_pct']:6.1f}% vel={r['breadth_velocity']:+.1f}%")
    except Exception as e:
        print(f"  Lịch sử: {e}")
    
    print("=" * 60)


def cmd_report(args):
    """Báo cáo thị trường."""
    if args.subcommand == "weekly":
        from src.services.weekly_cognitive_report import build_narrative
        narrative = build_narrative()
        print("=" * 60)
        print("  PTCK — WEEKLY COGNITIVE REPORT")
        print("=" * 60)
        if isinstance(narrative, dict):
            for section, content in narrative.items():
                print(f"\n  [{section}]")
                if isinstance(content, str):
                    print(f"    {content}")
                else:
                    for k, v in content.items():
                        print(f"    {k}: {v}")
        else:
            print(f"  {narrative}")
        print("=" * 60)
    elif args.subcommand == "daily":
        from src.services.daily_market_report import build_daily_report, in_bao_cao
        bao_cao = build_daily_report()
        in_bao_cao(bao_cao)
    elif args.subcommand == "monthly":
        month = getattr(args, 'month', None)
        lang = getattr(args, 'lang', 'vi')
        cmd = f"python backend/src/tools/market_report.py"
        if month:
            cmd += f" --month {month}"
        if lang:
            cmd += f" --lang {lang}"
        print(f"  Đang tạo báo cáo... (chạy: {cmd})")
        # Import và chạy
        sys.argv = ["market_report.py"]
        if month:
            sys.argv += ["--month", month]
        if lang:
            sys.argv += ["--lang", lang]
        exec(open(backend_dir / "src" / "tools" / "market_report.py", encoding='utf-8').read())
    else:
        print("  Usage: python ptck.py report weekly|monthly|daily [--month YYYY-MM] [--lang vi]")


def cmd_daily_close(args):
    """Chạy daily closer pipeline."""
    print("=" * 60)
    print("  PTCK — DAILY CLOSER")
    print("=" * 60)
    try:
        from src.daily_closer import run_daily_closer
        run_daily_closer()
        print("  [OK] Daily closer hoàn tất.")
    except Exception as e:
        print(f"  [FAIL] Lỗi: {e}")
    print("=" * 60)


def cmd_daily_update(args):
    """Cập nhật dữ liệu EOD."""
    date = getattr(args, 'date', None)
    cmd_str = "python backend/src/daily_updater.py"
    if date:
        cmd_str += f" --date {date}"
    print(f"  Chạy: {cmd_str}")
    sys.argv = ["daily_updater.py"]
    if date:
        sys.argv += ["--date", date]
    exec(open(backend_dir / "src" / "daily_updater.py", encoding='utf-8').read())


def _localize_driver(driver: str) -> str:
    mapping = {
        "BREADTH": "Độ rộng",
        "FLOW": "Dòng tiền",
        "STRUCTURE": "Cấu trúc",
        "VOLATILITY": "Biến động",
        "MOMENTUM": "Đà",
        "MACRO": "Vĩ mô",
    }
    return mapping.get(driver, driver)


def cmd_telemetry(args):
    """Telemetry operations."""
    if args.subcommand == "reputation":
        from src.telemetry.driver_reputation import (
            get_reputation_summary,
            get_reputation,
            update_reputation,
            _format_drift_status,
            _format_half_life,
        )
        summary = get_reputation_summary()
        drivers = summary.get("drivers", []) if isinstance(summary, dict) else []
        print("=" * 72)
        print("  PTCK — DRIVER REPUTATION LEDGER")
        print("=" * 72)
        if drivers:
            header = f"  {'Driver':<22s} {'Alpha':>8s} {'Acc':>6s} {'Drift':>8s} {'1/2-life':>10s}"
            print(header)
            print("  " + "-" * 68)
            for row in drivers:
                driver_vi = _localize_driver(row.get("driver", ""))
                alpha = f"{row.get('alpha_pct', 0)*100:.1f}%" if row.get('alpha_pct') else "N/A"
                acc = f"{row.get('accuracy', 0)*100:.0f}%" if row.get('accuracy') else "N/A"
                drift = row.get("performance_drift")
                drift_str = f"{drift*100:+.0f}%" if drift is not None else "—"
                hl = _format_half_life(row.get("half_life_days"))
                print(f"  {driver_vi:<22s} {alpha:>8s} {acc:>6s} {drift_str:>8s} {hl:>10s}")
            print("  " + "-" * 68)

            # Drift alerts
            alerts = []
            for row in drivers:
                d = row.get("performance_drift")
                if d is not None and d < -0.05:
                    hl = row.get("half_life_days")
                    hl_str = _format_half_life(hl)
                    alerts.append((
                        "⚠",
                        _localize_driver(row.get("driver", "")),
                        "đang mất hiệu lực",
                        f"Half-life: {hl_str}",
                    ))
                elif d is not None and d > 0.05:
                    alerts.append((
                        "✓",
                        _localize_driver(row.get("driver", "")),
                        "đang cải thiện",
                        "",
                    ))
            if alerts:
                print()
                for symbol, name, msg, extra in alerts:
                    print(f"  {symbol} {name} {msg}")
                    if extra:
                        print(f"    {extra}")

            # Top driver
            top = summary.get("top_driver")
            if top:
                print(f"\n  🏆 Dẫn đầu: {_localize_driver(top)}")
        else:
            print("  Chưa có dữ liệu. Chạy 'python ptck.py telemetry reputation --refresh'")
        print("=" * 72)

        # ── Drift Monitor detail ────────────────────────────────────
        is_drift = getattr(args, 'drift', False)
        if is_drift and drivers:
            print("\n  ═══ DRIFT MONITOR ═══")
            print(f"  {'Driver':<22s} {'Slope':>10s} {'Drift 30→180':>13s} {'Acc 30d':>8s} {'Acc 180d':>9s} {'1/2-life':>10s}")
            print("  " + "-" * 72)
            for row in drivers:
                dvi = _localize_driver(row.get("driver", ""))
                acc30 = row.get("accuracy_30d")
                acc180 = row.get("accuracy_180d")
                slope = row.get("performance_drift")
                hl = _format_half_life(row.get("half_life_days"))
                s30 = f"{acc30*100:.0f}%" if acc30 is not None else "—"
                s180 = f"{acc180*100:.0f}%" if acc180 is not None else "—"
                slope_s = f"{slope*100:+.1f}%" if slope is not None else "—"
                rel = row.get("relative_drift")
                drift_arrow = f"{_format_drift_status(slope)}" if slope is not None else "—"
                if slope is not None and slope < -0.03:
                    drift_arrow = f"⬇ {_format_drift_status(slope)}"
                elif slope is not None and slope > 0.03:
                    drift_arrow = f"⬆ {_format_drift_status(slope)}"
                else:
                    drift_arrow = f"➡ {_format_drift_status(slope)}"
                print(f"  {dvi:<22s} {slope_s:>10s} {drift_arrow:>20s} {s30:>8s} {s180:>9s} {hl:>10s}")
            print("  " + "-" * 72)

            # Detailed alerts
            decaying = [r for r in drivers if r.get("performance_drift") is not None and r["performance_drift"] < -0.05]
            improving = [r for r in drivers if r.get("performance_drift") is not None and r["performance_drift"] > 0.05]
            if decaying:
                print("\n  ⚠  ĐANG MẤT HIỆU LỰC:")
                for r in decaying:
                    dvi = _localize_driver(r.get("driver", ""))
                    hl = _format_half_life(r.get("half_life_days"))
                    rel = r.get("relative_drift")
                    rel_s = f" (tụt {rel} bậc)" if rel is not None and rel < 0 else ""
                    print(f"     {dvi}{rel_s} — half-life {hl}")
            if improving:
                print("\n  ✓  ĐANG CẢI THIỆN:")
                for r in improving:
                    dvi = _localize_driver(r.get("driver", ""))
                    rel = r.get("relative_drift")
                    rel_s = f" (lên {rel} bậc)" if rel is not None and rel > 0 else ""
                    print(f"     {dvi}{rel_s}")
        elif not is_drift:
            detail = get_reputation()
            if detail:
                print()
                for row in detail:
                    drift = _format_drift_status(row.get("performance_drift"))
                    hl = _format_half_life(row.get("half_life_days"))
                    print(f"  {row['driver']:10s} | {row['regime_tag']:12s} | "
                          f"{row['window_days']:3d}d | Acc: {row.get('accuracy', 0)*100:.0f}% | "
                          f"Alpha: {row.get('alpha_pct', 0)*100:.1f}% | "
                          f"Drift: {drift:20s} | HL: {hl}")
        if getattr(args, 'refresh', False):
            print("\n  Đang refresh reputation...")
            update_reputation()
            print("  ✅ Reputation đã được cập nhật.")
    elif args.subcommand == "shadow":
        days = getattr(args, 'days', 30)
        from src.core.shadow_metrics_schema import compute_shadow_metrics
        metrics = compute_shadow_metrics(window_days=days)
        print("=" * 60)
        print(f"  PTCK — SHADOW METRICS ({days} ngày)")
        print("=" * 60)
        for k, v in metrics.items():
            print(f"  {k}: {v}")
        print("=" * 60)


def cmd_serve(args):
    """Khởi động API server."""
    port = getattr(args, 'port', 8000)
    print(f"  Khởi động FastAPI tại http://0.0.0.0:{port}")
    print(f"  Docs: http://localhost:{port}/docs")
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=port, reload=True)


def cmd_db(args):
    """Database operations."""
    if args.subcommand == "vacuum":
        print("  Đang VACUUM database...")
        sys.argv = ["db_maintenance.py", "--vacuum"]
        exec(open(backend_dir / "src" / "db_maintenance.py", encoding='utf-8').read())
    elif args.subcommand == "stats":
        print("  Đang thống kê database...")
        sys.argv = ["db_maintenance.py", "--stats"]
        exec(open(backend_dir / "src" / "db_maintenance.py", encoding='utf-8').read())
    else:
        print("  Usage: python ptck.py db vacuum|stats")


def cmd_status(args):
    """Kiểm tra sức khỏe hệ thống."""
    print("=" * 60)
    print("  PTCK — SYSTEM HEALTH CHECK")
    print("=" * 60)
    
    checks = []
    
    # 1. Python version
    checks.append(("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", True))
    
    # 2. Project root
    checks.append(("Project root", str(PROJECT_ROOT), PROJECT_ROOT.exists()))
    
    # 3. Backend dir
    checks.append(("Backend", str(backend_dir), backend_dir.exists()))
    
    # 4. DB files
    db_found = False
    for db_pattern in ["*.db", "*.sqlite", "*.sqlite3"]:
        for db_file in backend_dir.glob(f"**/{db_pattern}"):
            db_found = True
            size_mb = db_file.stat().st_size / (1024 * 1024)
            checks.append((f"DB: {db_file.name}", f"{size_mb:.1f} MB", True))
            break
        if db_found:
            break
    if not db_found:
        checks.append(("Database", "Không tìm thấy", False))
    
    # 5. Engine imports
    engine_tests = [
        ("regime_engine", "src.engine.regime_engine"),
        ("breadth_engine", "src.engine.breadth_engine"),
        ("driver_normalizer", "src.engine.driver_normalizer"),
        ("canonical_output_adapter", "src.core.canonical_output_adapter"),
        ("daily_closer", "src.daily_closer"),
        ("structural_detector", "src.engine.structural_detector"),
        ("orchestrator", "src.engine.orchestrator"),
        ("telemetry.reputation", "src.telemetry.driver_reputation"),
    ]
    for label, module_path in engine_tests:
        try:
            __import__(module_path)
            checks.append((f"Module: {label}", "OK", True))
        except Exception as e:
            checks.append((f"Module: {label}", str(e)[:50], False))
    
    # Print
    for label, value, ok in checks:
        icon = "[OK]" if ok else "[FAIL]"
        print(f"  {icon:6s} {label:35s} {value}")
    
    # Summary
    passed = sum(1 for _, _, ok in checks if ok)
    total = len(checks)
    print()
    print(f"  Kết quả: {passed}/{total} checks passed")
    print("=" * 60)


def cmd_structural(args):
    """Bộ phát hiện lệch cấu trúc thị trường."""
    from src.engine.structural_detector import detect_cau_truc, in_bao_cao
    from datetime import datetime
    kq = detect_cau_truc(args.date or datetime.now().strftime("%Y-%m-%d"))
    in_bao_cao(kq)


def cmd_final(args):
    """Bộ não quyết định cuối cùng."""
    from src.engine.orchestrator import quyet_dinh_cuoi, in_bao_cao
    from datetime import datetime
    kq = quyet_dinh_cuoi(args.date or datetime.now().strftime("%Y-%m-%d"))
    in_bao_cao(kq)


def cmd_snapshot(args):
    """Tạo ảnh chụp thị trường duy nhất."""
    from src.core.market_snapshot import tao_anh_chup, in_anh_chup
    anh_chup = tao_anh_chup()
    in_anh_chup(anh_chup)


def cmd_confidence(args):
    """Bộ tự đánh giá độ tin cậy của quyết định."""
    from src.engine.confidence_layer import đánh_giá_độ_tin_cậy, in_báo_cáo
    kq = đánh_giá_độ_tin_cậy()
    in_báo_cáo(kq)
    # Đọc quyết định cuối cùng từ file (không tính lại)
    from pathlib import Path
    from src.config import DATA_DIR
    fp = Path(DATA_DIR) / "output" / "final_decision.json"
    if fp.exists():
        import json
        final = json.loads(fp.read_text(encoding="utf-8"))
        print(f"  → Quyết định hiện tại: {final.get('quyet_dinh', 'N/A')}")
        if final.get("bi_chặn_bởi_bảo_vệ"):
            print(f"  ⚠ Đã bị lớp bảo vệ chặn: {final.get('lý_do_chặn', '')}")


def cmd_data_quality(args):
    """Bộ đánh giá độ tin cậy dữ liệu (TẦNG 0)."""
    from src.engine.data_quality import xuat_bao_cao
    xuat_bao_cao()


def cmd_scan(args):
    """Elite scanner."""
    deep = getattr(args, 'deep', False)
    print("=" * 60)
    print("  PTCK — ELITE SCANNER")
    print("=" * 60)
    sys.argv = ["elite_scanner.py"]
    if deep:
        sys.argv.append("--deep")
    exec(open(backend_dir / "src" / "engine" / "elite_scanner.py", encoding='utf-8').read())
    print("=" * 60)


def cmd_gold(args):
    """Gold information."""
    if args.subcommand == "regime":
        try:
            from core.macro.gold_regime_engine import analyze_gold_regime, cross_reference_with_market
            from src.services.macro_service import get_macro_status
            regime = analyze_gold_regime()
            macro = {}
            try:
                macro = get_macro_status()
            except Exception:
                pass
            cognition = cross_reference_with_market(macro)
            print("=" * 60)
            print("  PTCK — GOLD REGIME")
            print("=" * 60)
            if isinstance(regime, dict):
                for k, v in regime.items():
                    print(f"  {k}: {v}")
            if cognition:
                print()
                for k, v in cognition.get("gold_cognition", {}).items():
                    print(f"  {k}: {v}")
            print("=" * 60)
        except Exception as e:
            print(f"  [FAIL] Gold regime: {e}")
    else:
        try:
            from src.services.macro.gold_service import get_gold_dashboard, get_gold_cognition_layer
            dash = get_gold_dashboard()
            print("=" * 60)
            print("  PTCK — GOLD DASHBOARD")
            print("=" * 60)
            print(f"  SJC mua:   {dash.get('sjc_buy', 'N/A'):>12.0f} VND")
            print(f"  SJC bán:   {dash.get('sjc_sell', 'N/A'):>12.0f} VND")
            print(f"  SJC spread: {dash.get('sjc_spread', 'N/A'):>12.0f} VND")
            print(f"  BTMC mua:  {dash.get('btmc_buy', 'N/A'):>12.0f} VND")
            print(f"  BTMC bán:  {dash.get('btmc_sell', 'N/A'):>12.0f} VND")
            print(f"  BTMC spread: {dash.get('btmc_spread', 'N/A'):>12.0f} VND")
            print("=" * 60)
        except Exception as e:
            print(f"  [FAIL] Gold dashboard: {e}")


def main():
    parser = argparse.ArgumentParser(
        prog="ptck",
        description="PTCK_VNSTOCK CLI — Single entrypoint cho mọi thao tác",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # market
    p_market = sub.add_parser("market", help="Market snapshot")
    p_market.set_defaults(func=cmd_market)

    # regime
    p_regime = sub.add_parser("regime", help="Regime analysis chi tiết")
    p_regime.set_defaults(func=cmd_regime)

    # report
    p_report = sub.add_parser("report", help="Báo cáo (weekly/monthly/daily)")
    p_report.add_argument("subcommand", choices=["weekly", "monthly", "daily"], help="Loại báo cáo")
    p_report.add_argument("--month", help="Tháng cho báo cáo monthly (YYYY-MM)")
    p_report.add_argument("--lang", default="vi", help="Ngôn ngữ (vi/en)")
    p_report.set_defaults(func=cmd_report)

    # daily-close
    p_dc = sub.add_parser("daily-close", help="Chạy daily closer pipeline")
    p_dc.set_defaults(func=cmd_daily_close)

    # daily-update
    p_du = sub.add_parser("daily-update", help="Cập nhật dữ liệu EOD")
    p_du.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_du.set_defaults(func=cmd_daily_update)

    # telemetry
    p_tel = sub.add_parser("telemetry", help="Telemetry & reputation")
    p_tel.add_argument("subcommand", choices=["reputation", "shadow"])
    p_tel.add_argument("--refresh", action="store_true", help="Refresh dữ liệu")
    p_tel.add_argument("--drift", action="store_true", help="Drift Monitor (chi tiết độ dốc + half-life)")
    p_tel.add_argument("--days", type=int, default=30, help="Số ngày cho shadow metrics")
    p_tel.set_defaults(func=cmd_telemetry)

    # serve
    p_serve = sub.add_parser("serve", help="Khởi động server")
    p_serve.add_argument("--port", type=int, default=8000, help="Port")
    p_serve.set_defaults(func=cmd_serve)

    # db
    p_db = sub.add_parser("db", help="Database operations")
    p_db.add_argument("subcommand", choices=["vacuum", "stats"])
    p_db.set_defaults(func=cmd_db)

    # status
    p_status = sub.add_parser("status", help="Kiểm tra sức khỏe hệ thống")
    p_status.set_defaults(func=cmd_status)

    # structural
    p_struct = sub.add_parser("structural", help="Bộ phát hiện lệch cấu trúc thị trường")
    p_struct.add_argument("--date", help="Ngày phân tích (YYYY-MM-DD)")
    p_struct.set_defaults(func=cmd_structural)

    # final-decision
    p_final = sub.add_parser("final-decision", help="Bộ não quyết định cuối cùng")
    p_final.add_argument("--date", help="Ngày quyết định (YYYY-MM-DD)")
    p_final.set_defaults(func=cmd_final)

    # snapshot
    p_snap = sub.add_parser("snapshot", help="Ảnh chụp thị trường duy nhất")
    p_snap.set_defaults(func=cmd_snapshot)

    # confidence
    p_conf = sub.add_parser("confidence", help="Bộ tự đánh giá độ tin cậy")
    p_conf.set_defaults(func=cmd_confidence)

    # scan
    p_scan = sub.add_parser("scan", help="Elite scanner")
    p_scan.add_argument("--deep", action="store_true", help="Deep scan")
    p_scan.set_defaults(func=cmd_scan)

    # gold
    p_gold = sub.add_parser("gold", help="Gold information")
    p_gold.add_argument("subcommand", nargs="?", choices=["regime"], default=None, help="Gold subcommand")
    p_gold.set_defaults(func=cmd_gold)

    # data-quality
    p_dq = sub.add_parser("data-quality", help="Đánh giá độ tin cậy dữ liệu (TẦNG 0)")
    p_dq.set_defaults(func=cmd_data_quality)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
