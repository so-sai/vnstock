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
import subprocess
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
    
    regime = detect_regime(lang_mode=_VERBOSE_LANG)
    d = regime.get("details", {})
    
    # Dynamic column auto-fit
    labels_main = ["Trạng thái", "Độ rộng", "ADX", "ATR ratio", "VNINDEX vs MA200", "MA50 slope"]
    max_w = max(len(_ll(label)) for label in labels_main)
    
    print("=" * 60)
    print("  PTCK — SƠ ĐỒ THỊ TRƯỜNG")
    print("=" * 60)
    print(f"  {_ll('Trạng thái'):{max_w}s} {regime.get('status')} ({regime.get('regime_score'):.2f})")
    print(f"  {_ll('Độ rộng'):{max_w}s} {d.get('breadth_pct', 'N/A')}% (tốc độ: {d.get('breadth_momentum', 0):+.1f}%)")
    print(f"  {_ll('ADX'):{max_w}s} {d.get('adx', 'N/A')}")
    print(f"  {_ll('ATR ratio'):{max_w}s} {d.get('atr_ratio', 'N/A')}")
    print(f"  {_ll('VNINDEX vs MA200'):{max_w}s} {d.get('vnindex_vs_ma200', 'N/A')}")
    print(f"  {_ll('MA50 slope'):{max_w}s} {d.get('ma50_slope', 'N/A')}")
    print()
    
    # Driver state
    try:
        from src.engine.driver_normalizer import driver_state_from_engine_outputs
        ds = driver_state_from_engine_outputs(
            breadth_health=d.get("breadth_pct"),
            t_score=d.get("t_score"),
            v_score=d.get("v_score"),
        )
        labels_drv = ["Driver trội", "Entropy", "BREADTH", "MOMENTUM", "VOLATILITY"]
        max_w_drv = max(len(_ll(label)) for label in labels_drv)
        print(f"  {_ll('Driver trội'):{max_w_drv}s} {_ll(ds.dominant)} ({ds.confidence:.1%})")
        print(f"  {_ll('Entropy'):{max_w_drv}s} {ds.entropy:.2f}")
        for drv, wt in sorted(ds.distribution.items(), key=lambda x: -x[1]):
            print(f"    {_ll(drv):{max_w_drv - 4}s}: {wt:.1%}")
    except Exception as e:
        print(f"  Driver state:   {e}")
    
    print("=" * 60)


def cmd_regime(args):
    """Chi tiết regime analysis."""
    from src.engine.regime_engine import detect_regime
    
    regime = detect_regime(lang_mode=_VERBOSE_LANG)
    d = regime.get("details", {})
    
    # Dynamic column auto-fit
    labels = ["Trạng thái", "Score", "B-Score", "Độ rộng", "ADX", "ATR ratio"]
    max_w = max(len(_ll(label)) for label in labels)
    
    print("=" * 60)
    print("  PTCK — PHÂN TÍCH MÔI TRƯỜNG")
    print("=" * 60)
    print(f"  {_ll('Trạng thái'):{max_w}s} {regime.get('status')}")
    print(f"  {_ll('Score'):{max_w}s} {regime.get('regime_score'):.4f}")
    print(f"  Score thô:      {regime.get('regime_score_raw', 0):.4f}")
    print(f"  EMA alpha:      {regime.get('ema_alpha', 0):.2f}")
    print(f"  Ngày:           {regime.get('date', 'N/A')}")
    print()
    
    print("  ── Chi tiết ──")
    print(f"  {_ll('B-Score'):{max_w}s} {d.get('b_score', 0):.4f}")
    print(f"  T-Score (xu hướng): {d.get('t_score', 0):.4f}")
    print(f"  V-Score (biến động): {d.get('v_score', 0):.4f}")
    print(f"  {_ll('Độ rộng'):{max_w}s} {d.get('breadth_pct', 'N/A')}%")
    print(f"  Breadth std 10d: {d.get('breadth_std_10d', 'N/A')}")
    print(f"  Breadth momentum: {d.get('breadth_momentum', 0):+.1f}%")
    print(f"  {_ll('ADX'):{max_w}s} {d.get('adx', 'N/A')}")
    print(f"  {_ll('ATR ratio'):{max_w}s} {d.get('atr_ratio', 'N/A')}")
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
        exec(open(backend_dir / "src" / "tools" / "market_report.py", encoding='utf-8-sig').read())
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
    manifest = getattr(args, 'manifest', None)
    cmd = [sys.executable, str(backend_dir / "src" / "daily_updater.py")]
    if date:
        cmd += ["--date", date]
    if manifest:
        cmd += ["--manifest", manifest]
    print(f"  Chạy: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  [FAIL] Lỗi: mã thoát {result.returncode}")
    else:
        print("  [OK] Daily update hoàn tất.")


def cmd_gap_analyzer(args):
    """Kiểm toán dữ liệu — phát hiện missing symbols/dates."""
    output = getattr(args, 'output', None)
    lookback = getattr(args, 'lookback_months', 3)
    cmd = [sys.executable, str(backend_dir / "src" / "telemetry" / "gap_analyzer.py")]
    if output:
        cmd += ["--output", output]
    cmd += ["--lookback-months", str(lookback)]
    print(f"  Chạy: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  [FAIL] Lỗi: mã thoát {result.returncode}")


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
    port = getattr(args, 'port', 17039)
    print(f"  Khởi động FastAPI tại http://0.0.0.0:{port}")
    print(f"  Docs: http://localhost:{port}/docs")
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=port, reload=True)


def cmd_db(args):
    """Database operations."""
    from src.db_maintenance import get_table_stats, vacuum_database, get_db_size_mb
    if args.subcommand == "vacuum":
        print("  Đang VACUUM database...")
        vacuum_database()
    elif args.subcommand == "stats":
        print("  Đang thống kê database...")
        stats = get_table_stats()
        print("\nTABLE STATISTICS:")
        for table, count in sorted(stats.items()):
            print(f"  {table}: {count:,} rows")
        print(f"\nDB Size: {get_db_size_mb():.1f} MB")
    elif args.subcommand == "optimize":
        print("  Đang tối ưu hóa toàn bộ database (JSONB + STRICT + VACUUM)...")
        from src.database.db_optimize import run_all
        run_all()
    else:
        print("  Usage: python ptck.py db vacuum|stats|optimize")


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
    anh_chup = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    in_anh_chup(anh_chup, lang_mode=_VERBOSE_LANG)


def cmd_ddi(args):
    """Delta Divergence Index — Δ_SA = dS/dt - α·AC_latency."""
    from src.core.market_snapshot import tao_anh_chup
    from src.alpha.delta_divergence import DeltaDivergenceIndex
    anh_chup = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    ddi = anh_chup.get("delta_divergence", {})
    icon = {"pass": "🟢", "caution": "🟡", "block": "🔴"}.get(ddi.get("action_filter"), "⚪")
    print("\n" + "=" * 55)
    print("  DELTA DIVERGENCE INDEX (DDI)")
    print("=" * 55)
    print(f"  {icon} Δ_SA:          {ddi.get('delta_sa', 'N/A')}")
    print(f"    Action Filter:   {ddi.get('action_filter', 'N/A')}")
    print(f"    dS/dt:           {ddi.get('dS_dt', 'N/A')}")
    print(f"    AC Latency:      {ddi.get('ac_latency', 'N/A')}")
    print(f"    α ({ddi.get('regime', 'N/A')}): {ddi.get('alpha_regime', 'N/A')}")
    if ddi.get("healing_illusion"):
        print(f"    ⚠ HEALING ILLUSION — stress vượt adaptation")
    p_hash = anh_chup.get("params_hash", "unresolved")
    print(f"  Params Hash:      {p_hash}")
    print("=" * 55)


def cmd_snapshot_index(args):
    """Xem lịch sử snapshot_index.json."""
    import json
    from src.config import DATA_DIR
    fp = Path(DATA_DIR) / "output" / "snapshot_index.json"
    if not fp.exists():
        print("  ⚠ snapshot_index.json chưa tồn tại. Chạy snapshot trước.")
        return
    idx = json.loads(fp.read_text(encoding="utf-8"))
    if not idx:
        print("  snapshot_index.json rỗng.")
        return
    limit = args.limit if hasattr(args, "limit") else 20
    recent = idx[-limit:]
    print(f"\n{'='*75}")
    print(f"  SNAPSHOT INDEX — {len(idx)} entries (hiển thị {len(recent)} gần nhất)")
    print(f"{'='*75}")
    print(f"  {'Ngày':<12} {'Hash':<17} {'Regime':<10} {'Δ_SA':>7} {'Filter':<8}")
    print(f"  {'-'*12} {'-'*17} {'-'*10} {'-'*7} {'-'*8}")
    for e in recent:
        print(f"  {e.get('ngay',''):<12} {e.get('params_hash',''):<17} "
              f"{e.get('regime',''):<10} {e.get('delta_sa',0):>7.4f} "
              f"{e.get('action_filter',''):<8}")
    print(f"{'='*75}")


def cmd_confidence(args):
    """Bộ tự đánh giá độ tin cậy của quyết định."""
    from src.engine.confidence_layer import đánh_giá_độ_tin_cậy, in_báo_cáo
    from datetime import datetime
    kq = đánh_giá_độ_tin_cậy(target_date=args.date or datetime.now().strftime("%Y-%m-%d"))
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


def cmd_restore_backup(args):
    """Khôi phục alert từ backup gần nhất."""
    from pathlib import Path
    root = Path(__file__).resolve().parent
    alert_dir = root / "backend" / "data" / "alerts"
    backup_dir = alert_dir / "backup"

    if not backup_dir.exists() or not any(backup_dir.iterdir()):
        print("  ⚠ Không tìm thấy backup nào. Chưa từng chạy sbv-update?")
        return

    import shutil
    count = 0
    for f in backup_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, alert_dir / f.name)
            count += 1
    recall_bak = backup_dir / "recall_state.json"
    if recall_bak.exists():
        recall_dst = root / "backend" / "data" / "probe_cache" / "recall_state.json"
        recall_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(recall_bak, recall_dst)
        count += 1
    print(f"  ✅ Đã khôi phục {count} file từ backup.")
    print(f"  Chạy 'python ptck.py status' để kiểm tra trạng thái.")


def cmd_check_calendar(args):
    """Kiểm tra lịch lễ so với nguồn online, có flag auto-update."""
    from src.engine.partial_data_entropy import _scrape_holidays, _read_calendar_safe
    import json

    print("  Đang lấy dữ liệu lịch lễ từ timeanddate.com...")
    online = _scrape_holidays()
    if not online:
        print("  ⚠ Không lấy được lịch online. Kiểm tra kết nối mạng.")
        return

    local = _read_calendar_safe()
    missing = sorted(set(online) - set(local))
    extra = sorted(set(local) - set(online))

    print(f"  Lịch online:  {len(online)} ngày")
    print(f"  Lịch local:   {len(local)} ngày")
    if missing:
        print(f"  ⚠ Thiếu {len(missing)} ngày so với online:")
        for d in missing:
            print(f"    + {d}")
    if extra:
        print(f"  ℹ Dư {len(extra)} ngày (có thể đã cũ):")
        for d in extra:
            print(f"    - {d}")
    if not missing and not extra:
        print("  ✅ Lịch khớp hoàn toàn với dữ liệu online.")

    if args.auto_update and missing:
        from src.engine.partial_data_entropy import CALENDAR_PATH
        merged = sorted(set(local + online))
        CALENDAR_PATH.write_text(
            json.dumps({"holidays": merged}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  ✅ Đã cập nhật lịch: {len(merged)} ngày.")


def cmd_cleanup(args):
    """Dọn dẹp định kỳ: telemetry, cache, state files."""
    target = args.target
    if target == "telemetry":
        from src.engine.partial_data_entropy import cleanup_telemetry
        cleanup_telemetry(max_rows=getattr(args, 'max_rows', 1000))
        print(f"  ✅ Telemetry cleaned (keep last {getattr(args, 'max_rows', 1000)} rows).")
    elif target == "all":
        from src.engine.partial_data_entropy import cleanup_telemetry
        cleanup_telemetry(max_rows=1000)
        print("  ✅ Telemetry cleaned.")
        for fname in ["crisis_cooldown.json", "micro_stress.json"]:
            fp = Path(__file__).resolve().parent / "backend" / "data" / "probe_cache" / fname
            if fp.exists():
                fp.unlink()
                print(f"  ✅ {fname} removed.")
    else:
        print("  Usage: python ptck.py cleanup telemetry|all [--max-rows N]")

    # Also delete .tmp orphans
    probe_dir = Path(__file__).resolve().parent / "backend" / "data" / "probe_cache"
    for f in probe_dir.glob("*.tmp"):
        f.unlink()
        print(f"  ✅ Orphan .tmp cleaned: {f.name}")


def cmd_clear_crisis(args):
    """Xóa crisis_cooldown.json + reset micro_stress gate thủ công."""
    from pathlib import Path
    root = Path(__file__).resolve().parent
    cooldown = root / "backend" / "data" / "probe_cache" / "crisis_cooldown.json"
    stress = root / "backend" / "data" / "probe_cache" / "micro_stress.json"
    cleared = 0
    if cooldown.exists():
        cooldown.unlink()
        cleared += 1
    if stress.exists():
        stress.unlink()
        cleared += 1
    if cleared:
        print(f"  ✅ Đã xóa {cleared} file trạng thái. Hệ thống sẽ khởi tạo lại ở lần chạy tiếp theo.")
    else:
        print("  ℹ Không tìm thấy file trạng thái nào — hệ thống đã sạch.")


def cmd_sbv_update(args):
    """5-step recovery: backup → fixtures → pytest → scrape → clear alert."""
    import sys, subprocess, shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent
    alert_dir = root / "backend" / "data" / "alerts"
    backup_dir = alert_dir / "backup"

    print("=" * 60)
    print("  PTCK — SBV UPDATE (5-step recovery)")
    print("=" * 60)

    # ── Step 0: Backup trạng thái hiện tại (alert + recall_state) ──
    print("\n  [0/5] Đang backup trạng thái hiện tại...")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in alert_dir.iterdir():
        if f.is_file() and f.suffix in (".json", ".tmp"):
            shutil.copy2(f, backup_dir / f.name)
    recall_path = root / "backend" / "data" / "probe_cache" / "recall_state.json"
    if recall_path.exists():
        shutil.copy2(recall_path, backup_dir / "recall_state.json")
    print(f"  ✅ Backup tại: {backup_dir}")

    # ── Step 1: Nạp fixtures mới ──
    print("\n  [1/5] Kiểm tra fixtures...")
    fixture_dir = root / "tests" / "fixtures" / "sbv"
    if not fixture_dir.exists():
        print("  ⚠ Không tìm thấy thư mục fixtures. Tạo mới...")
        fixture_dir.mkdir(parents=True, exist_ok=True)
    print(f"  ✅ Fixtures tại: {fixture_dir}")

    # ── Step 2: Regression test ──
    print("\n  [2/5] Đang chạy regression test...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/macro/test_sbv_parser.py", "-v"],
        cwd=root, capture_output=True, text=True,
    )
    print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
    if result.returncode != 0:
        print("  [FAIL] Regression test thất bại — cập nhật fixtures chưa đúng.")
        print("  Sửa fixtures, sau đó chạy lại: python ptck.py sbv-update")
        return

    # ── Step 3: Production scrape (force=True bỏ qua circuit breaker) ──
    print("\n  [3/5] Đang quét SBV thực tế...")
    from src.services.macro.interbank_seeder import _try_sbv, _clear_sbv_alert, _is_sbv_alert_active
    sbv_result = _try_sbv(force=True)
    rates = sbv_result.get("data", {})

    if sbv_result.get("type") != "SUCCESS":
        print(f"  [FAIL] SBV scrape thất bại: type={sbv_result.get('type')}, "
              f"HTTP={sbv_result.get('http_status')}")
        print("  Không giải phóng khóa — alert file được giữ nguyên.")
        print("  Kiểm tra: sbv.gov.vn có thể đang bị chặn hoặc thay đổi cấu trúc.")
        return

    print(f"  ✅ SBV scrape OK: {len(rates)} kỳ hạn")
    for k, v in rates.items():
        print(f"    {k}: {v}")

    # ── Step 3b: Lưu forensic snapshot + cập nhật fixture ──
    raw_html = sbv_result.get("raw_html", "")
    if raw_html:
        fixture_dir = root / "tests" / "fixtures" / "sbv"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / "live.html"
        fixture_path.write_text(raw_html, encoding="utf-8")
        print(f"\n  [3b] ✅ Đã lưu forensic snapshot tại: {fixture_path}")
        # Verify parser vẫn hoạt động trên fixture mới
        try:
            from src.services.macro.interbank_seeder import _parse_sbv_html
            parsed = _parse_sbv_html(raw_html)
            if parsed:
                print(f"  [3b] ✅ Parser OK: {len(parsed)} kỳ hạn — {parsed}")
            else:
                print(f"  [3b] ⚠ Parser trả về rỗng — cập nhật _parse_sbv_html()")
        except Exception:
            print(f"  [3b] ⚠ Lỗi parser — cần fix thủ công")

    # ── Step 4: Clear alert + crisis cooldown — phân loại tự động theo crisis_marker ──
    print("\n  [4/5] Nghiệm thu — giải phóng khóa...")
    _clear_sbv_alert()

    cooldown_path = root / "backend" / "data" / "probe_cache" / "crisis_cooldown.json"
    marker = None
    cooldown_state = {}
    if cooldown_path.exists():
        try:
            import json
            cooldown_state = json.loads(cooldown_path.read_text(encoding="utf-8"))
            marker = cooldown_state.get("crisis_marker", "")
        except Exception:
            pass

    CRITICAL_MARKERS = {"CRISIS_REAL"}
    AUTO_CLEAR_MARKERS = {"FORCED_SAFETY", "CORRUPTED_FALLBACK"}
    marker = marker or ""

    if marker in CRITICAL_MARKERS and not getattr(args, 'sbv_force', False):
        # ── CRISIS_REAL: cảnh báo đỏ, yêu cầu --force, sensor re-scan ──
        print("  ⚠" * 15)
        print("  ⚠  CRITICAL_WARNING: crisis_marker = CRISIS_REAL")
        print("  ⚠  Đây là khủng hoảng thanh khoản THỰC TẾ (ON >= 15%)")
        print("  ⚠  Không phải lỗi file — không clear tự động.")
        print("  ⚠" * 15)
        print()
        print("  Hành động bắt buộc:")
        print("    [1] Xác nhận thủ công: python ptck.py sbv-update --force")
        print("    [2] Kiểm tra ON rate thực tế từ SBV")
        print("    [3] Sensor re-scan tự động khi clear — nếu ON >= 15%,")
        print("        hệ thống lập tức KHÓA LẠI (crisis_active = True).")
        print()
        # Sensor re-scan ngay lập tức
        try:
            from src.engine.partial_data_entropy import update_crisis_cooldown
            current_on = cooldown_state.get("last_on", 0.0) or 0.0
            if current_on >= 15.0:
                update_crisis_cooldown(current_on)
                print("  ⚠ Sensor re-scan: ON=%.1f%% >= 15%% — KHÓA LẠI." % current_on)
            else:
                print("  ℹ Sensor re-scan: ON=%.1f%% < 15%% — an toàn." % current_on)
        except Exception:
            pass
        print("  Chỉ clear: python ptck.py clear-crisis (nếu bạn chắc chắn ON < 15%)")
        return

    if marker in AUTO_CLEAR_MARKERS:
        # ── FORCED_SAFETY / CORRUPTED_FALLBACK: tự động giải tỏa ──
        if marker == "FORCED_SAFETY":
            print("  ℹ crisis_marker = FORCED_SAFETY — mất file trong lúc căng thẳng.")
            print("  → Tự động giải tỏa, tái tạo config sạch.")
        elif marker == "CORRUPTED_FALLBACK":
            print("  ℹ crisis_marker = CORRUPTED_FALLBACK — file hỏng cấu trúc.")
            print("  → Ghi đè file rác bằng JSON hợp lệ (atomic os.replace()).")

    # Xóa / ghi đè state file an toàn
    if marker in AUTO_CLEAR_MARKERS or marker not in CRITICAL_MARKERS:
        if marker:
            _clear_crisis_state(cooldown_path)
        elif cooldown_path.exists():
            _clear_crisis_state(cooldown_path)

    stress_path = root / "backend" / "data" / "probe_cache" / "micro_stress.json"
    if stress_path.exists():
        stress_path.unlink()
        print("  ✅ micro_stress.json cleared.")

    print("  ✅ Alert file cleared. Khóa vị thế 0.0 đã được giải phóng.")
    print("  Hệ thống sẽ lấy dữ liệu SBV mới ở lần refresh_interbank_rate() tiếp theo.")
    print("=" * 60)


def _clear_crisis_state(cooldown_path):
    """Giải tỏa crisis_cooldown.json — ghi đè state sạch thay vì xóa trơn.

    FORCED_SAFETY:  tái tạo config sạch (crisis_active=False)
    CORRUPTED_FALLBACK: ghi đè file rác = JSON hợp lệ (atomic os.replace())
    """
    import json
    from src.engine.partial_data_entropy import _save_cooldown_state
    fresh = {
        "crisis_active": False,
        "crisis_marker": "",
        "consecutive_normal": 0,
        "last_normal_date": "",
        "last_crisis_date": "",
    }
    _save_cooldown_state(fresh)
    print("  ✅ crisis_cooldown.json reset → clean state.")


def cmd_data_quality(args):
    """Bộ đánh giá độ tin cậy dữ liệu (TẦNG 0)."""
    from src.engine.data_quality import xuat_bao_cao
    xuat_bao_cao()


def cmd_index_decompose(args):
    """Phân tích chỉ số thị trường thống nhất."""
    from src.engine.index_reality_unifier import phan_tich_chi_so, in_bao_cao
    from datetime import datetime
    kq = phan_tich_chi_so(args.date or datetime.now().strftime("%Y-%m-%d"))
    in_bao_cao(kq)


def cmd_backfill(args):
    """Engine khôi phục dữ liệu lịch sử (Backfill)."""
    from src.engine.backfill_engine import backfill
    symbols = getattr(args, 'symbols', None)
    if symbols:
        symbols = [s.strip().upper() for s in symbols.split(',')]
    start = getattr(args, 'start', None)
    end = getattr(args, 'end', None)
    dry_run = getattr(args, 'dry_run', False)
    quiet = getattr(args, 'quiet', False)
    backfill(symbols=symbols, start=start, end=end, dry_run=dry_run, verbose=not quiet)


def cmd_backfill_macro(args):
    """Hotfix #2: Backfill 1y historical data for PTD macro tickers (^GSPC, ^IXIC, ^VIX, ^HSI)."""
    import yfinance as yf
    import pandas as pd
    import logging
    logger = logging.getLogger(__name__)

    days = getattr(args, 'days', 252)
    tickers = {'SP500': '^GSPC', 'NASDAQ': '^IXIC', 'VIX': '^VIX', 'HANG_SENG': '^HSI'}
    period = f"{max(days, 252)}d"

    print("=" * 60)
    print("  PTCK — BACKFILL MACRO (PTD Tickers)")
    print("=" * 60)
    print(f"  Tickers: {', '.join(tickers.values())}")
    print(f"  Period:  {period}")
    print()

    data = yf.download(list(tickers.values()), period=period, interval="1d", progress=False)
    if data.empty:
        print("  [FAIL] Không nhận được dữ liệu từ Yahoo Finance.")
        return

    close_data = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data
    inv_map = {v: k for k, v in tickers.items()}
    close_data = close_data.rename(columns=inv_map)
    df_melted = close_data.reset_index().melt(id_vars=['Date'], var_name='variable', value_name='value')
    df_melted.rename(columns={'Date': 'date'}, inplace=True)
    df_melted['date'] = pd.to_datetime(df_melted['date']).dt.strftime('%Y-%m-%d')
    df_melted = df_melted.dropna()

    if df_melted.empty:
        print("  [FAIL] Không có dữ liệu sau khi melt.")
        return

    print(f"  Downloaded: {len(df_melted)} rows")

    from src.database.db_core import get_connection, save_data_upsert
    with get_connection() as conn:
        save_data_upsert('macro_history', df_melted, conn)

    print(f"  [OK] Đã seed {len(df_melted)} rows vào macro_history.")
    print("=" * 60)


def cmd_scan(args):
    """Elite scanner."""
    deep = getattr(args, 'deep', False)
    print("=" * 60)
    print("  PTCK — ELITE SCANNER")
    print("=" * 60)
    from src.engine.elite_scanner import run_elite_scanner
    run_elite_scanner(deep_scan=deep)
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


def cmd_silver(args):
    """Silver information."""
    if args.subcommand == "gs-ratio":
        try:
            from core.macro.precious_metal_ratio import get_gs_ratio_from_db
            gs = get_gs_ratio_from_db()
            print("=" * 60)
            print("  PTCK — GOLD/SILVER RATIO")
            print("=" * 60)
            print(f"  XAUUSD:          {gs.get('xau_usd', 'N/A')}")
            print(f"  XAGUSD:          {gs.get('xag_usd', 'N/A')}")
            print(f"  Gold/Silver Ratio: {gs.get('ratio', 'N/A')}")
            print(f"  Regime:          {gs.get('regime', 'N/A')}")
            print(f"  Tín hiệu:        {gs.get('signal', 'N/A')}")
            print(f"  Severity:        {gs.get('severity', 0):.2f}")
            print("=" * 60)
        except Exception as e:
            print(f"  [FAIL] GS ratio: {e}")
    elif args.subcommand == "seed":
        try:
            from src.services.macro.silver_world_service import seed_world_silver_to_db
            print("  Đang seed XAGUSD...")
            ok = seed_world_silver_to_db()
            print(f"  {'[OK]' if ok else '[FAIL]'} World silver seeded: {ok}")
        except Exception as e:
            print(f"  [FAIL] Silver seed: {e}")
    else:
        try:
            from src.services.macro.silver_service import get_silver_dashboard
            from src.services.macro.silver_world_service import fetch_world_silver_live
            from core.macro.precious_metal_ratio import calculate_gold_silver_ratio, assess_gs_ratio_regime
            from src.services.macro.gold_world_service import fetch_world_gold_live
            dash = get_silver_dashboard()
            xag = fetch_world_silver_live()
            xau = fetch_world_gold_live()
            gs = calculate_gold_silver_ratio(xau, xag)
            gsr = assess_gs_ratio_regime(gs)
            print("=" * 60)
            print("  PTCK — SILVER DASHBOARD")
            print("=" * 60)
            print(f"  BTMC mua:    {dash.get('btmc_buy', 0):>12.0f} VND")
            print(f"  BTMC bán:    {dash.get('btmc_sell', 0):>12.0f} VND")
            print(f"  BTMC spread: {dash.get('btmc_spread', 0):>12.0f} VND")
            print(f"  XAGUSD:              {xag if xag else 'N/A'}")
            print(f"  Gold/Silver Ratio:   {gs if gs else 'N/A'}")
            print(f"  GS Regime:           {gsr.get('regime', 'N/A')}")
            print(f"  GS Signal:           {gsr.get('signal', 'N/A')}")
            print("=" * 60)
        except Exception as e:
            print(f"  [FAIL] Silver dashboard: {e}")


def cmd_rs_audit(args):
    """Audit Top RS — phân tích nguồn gốc sức mạnh."""
    top_n = getattr(args, 'top', 20)
    show_all = getattr(args, 'show_all', False)
    if show_all:
        top_n = 9999
    from src.engine.rs_audit import run_rs_audit, in_bao_cao
    results = run_rs_audit(top_n=top_n)
    in_bao_cao(results)


def cmd_group_influence(args):
    """Bộ đo ảnh hưởng nhóm trụ."""
    from src.engine.group_influence_engine import tinh_anh_huong_nhom, in_bao_cao, xuat_json
    mr = tinh_anh_huong_nhom()
    in_bao_cao(mr)
    xuat_json(mr)


def cmd_strength_discrimination(args):
    """Tầng phân tách xung lực — RS nội tại vs ép trụ điều tiết."""
    from src.engine.strength_discriminator import cmd_phân_tách
    cmd_phân_tách(top_n=getattr(args, 'top', 20))


def cmd_prediction_registry(args):
    """Prediction Registry — ghi lại dự báo RS audit + đo kết quả."""
    from src.telemetry.prediction_registry import (
        log_predictions, update_outcomes, get_registry_stats, get_raw_entries,
    )

    if args.action == "log":
        n = log_predictions(args.date)
        print(f"  [PR] Đã ghi {n} dự báo.")
    elif args.action == "update":
        n = update_outcomes()
        print(f"  [PR] Đã cập nhật {n} kết quả.")
    elif args.action == "list":
        entries = get_raw_entries(args.limit)
        if not entries:
            print("  [PR] Chưa có dữ liệu.")
            return
        print("=" * 100)
        print(f"  PREDICTION REGISTRY — {len(entries)} entries gần nhất")
        print("=" * 100)
        for e in reversed(entries):
            if e.get("event") == "prediction":
                print(f"  PREDICT {e['date']} {e['symbol']:6s} | RS={e['rs']:3d} score={e['diem_xac_nhan']:.2f} {e['phan_loai']:<20s} price={e['price_t0']:>8.1f}")
            elif e.get("event") == "outcome":
                print(f"  OUTCOME {e['date']} {e['symbol']:6s} | {e['horizon']:2d}ngày return={e['return_pct']:+.2f}% exit={e['exit_price']:>8.1f}")
        print("=" * 100)
    else:
        # stats (default)
        s = get_registry_stats()
        HORIZONS = [5, 20, 60]
        print("=" * 60)
        print("  PREDICTION REGISTRY — THỐNG KÊ")
        print("=" * 60)
        if not s:
            print("  Chưa có dữ liệu. Chạy 'python ptck.py prediction-registry log' trước.")
        for kl, data in sorted(s.items()):
            print(f"\n  [{kl}] ({data['count']} mã)")
            for h in HORIZONS:
                hk = f"return_{h}d"
                d = data[hk]
                if d["n"] > 0:
                    print(f"    {h:2d} ngày: avg={d['avg']:+.2f}%  max={d['max']:+.2f}%  min={d['min']:+.2f}%  (n={d['n']})")
                else:
                    print(f"    {h:2d} ngày: chưa có dữ liệu")
        print("=" * 60)


def cmd_registry(args):
    """Param Fingerprint Registry — Sổ tay Dấu vân tay Tham số."""
    from src.portfolio.params_registry import in_bao_cao, load_or_build
    if args.rebuild:
        from src.portfolio.params_registry import build_registry, save_registry
        data = build_registry()
        save_registry(data)
        print("Đã xây dựng lại registry từ audit log.")
    else:
        load_or_build()
    in_bao_cao(regime=args.regime)


def cmd_sandbox(args):
    """Phase 5 Dashboard — Real-time Paper Trading Terminal."""
    from src.execution.paper_broker import PaperBroker, OrderBook, StreamingFeed
    from src.execution.twap_executor import TWAPExecutor
    from src.portfolio.stale_manager import StalePositionManager
    from src.portfolio.system_state import get_state as get_sys_state
    from src.portfolio.paper_context import set_paper_mode
    import time

    set_paper_mode(True)

    n_slices = args.slices
    depth = args.depth
    price = args.price

    book = OrderBook.build("SANDBOX", mid=price, depth_per_level=depth, n_levels=5)
    broker = PaperBroker(book=book)
    feed = StreamingFeed("SANDBOX", base_price=price)

    mgr = StalePositionManager(total_capital=args.capital)
    mgr.ingest_stale("v1", 0.13)
    mgr.ingest_stale("v2", 0.10)

    exe = TWAPExecutor(mgr, broker=broker)
    plan = exe.build_plan(n_slices=n_slices)

    executed = 0
    try:
        while executed < n_slices:
            ts = time.strftime("%H:%M:%S")
            sys_state = get_sys_state()
            s = exe.status()
            report = broker.slippage_report()

            halted = "⚠️ HALT" if broker.is_trading_halt() else "✓ LIVE"
            print(f"\033[2J\033[H", end="")  # clear screen
            print("=" * 60)
            print(f"  PHASE 5 — PAPER TRADING DASHBOARD  [{ts}]  [{halted}]")
            print("=" * 60)
            print(f"  Portfolio")
            print(f"    Capital:        {args.capital:>12,.0f}")
            print(f"    Stale:          {sys_state.get('total_stale_pct', 0):>12.2%}")
            print(f"    Escrow:         {sys_state.get('escrow_balance', 0):>12.2f}")
            print(f"    Locked:         {str(sys_state.get('locked', False)):>12}")
            print(f"  Order Book")
            print(f"    Best Bid:       {broker.get_best_bid('SANDBOX'):>12.2f}")
            print(f"    Best Ask:       {broker.get_best_ask('SANDBOX'):>12.2f}")
            print(f"    Spread:         {book.spread():>12.2f}")
            print(f"    Mid:            {book.mid_price():>12.2f}")
            print(f"  TWAP")
            print(f"    Plan:           {s['plan_status']:>12}")
            print(f"    Slices:         {executed:>4}/{n_slices}")
            print(f"    Filled:         {s['filled_amount']:>12.2f}")
            print(f"    Cursor:         {str(s['cursor']):>12}")
            print(f"    CB Trips:       {s['circuit_breaker_trips']:>12}")
            print(f"  Slippage")
            print(f"    Trades:         {report.get('n', 0):>12}")
            print(f"    Mean:           {report.get('mean', 0):>12.6f}")
            print(f"    Max:            {report.get('max', 0):>12.6f}")
            print(f"  Book Thinning")
            remaining = sum(l.volume for l in book.bids)
            initial = depth * 5 * 0.8
            thinning = 100 * (1 - remaining / max(initial, 1))
            print(f"    Depth Remaining: {remaining:>12.2f}")
            print(f"    Thinning:        {thinning:>11.1f}%")
            print("=" * 60)

            if broker.is_trading_halt():
                print("  ⏸️  TRADING HALT — execution frozen")
                time.sleep(1)
                continue

            idx = executed + 1
            bid = broker.get_best_bid("SANDBOX")
            if bid <= 0:
                print(f"  ⏳ Slice {idx}: empty book, adaptive requeue...")
                r = exe.handle_liquidity_strike(idx, requeue_count=0)
                if r.get("action") == "DEFERRED":
                    exe.rollover_deferred()
                    n_slices = exe.plan.n_slices
                time.sleep(0.5)
                continue

            try:
                r = exe.execute_slice(idx, price=bid)
                if r["success"]:
                    o = broker.query_order(r["broker_order_id"])
                    fill = o.get("filled_qty", 0)
                    total = o.get("quantity", 0)
                    print(f"  ✅ Slice {idx}: {fill:.0f}/{total:.0f} filled")
                    executed += 1
                else:
                    print(f"  ❌ Slice {idx}: {r['reason']}")
                    executed += 1
            except RuntimeError as e:
                print(f"  ❌ {e}")
                # Circuit breaker — let it reset
                time.sleep(1)

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n  ⏹️  Dashboard stopped by user.")

    set_paper_mode(False)
    report = broker.slippage_report()
    print("\n" + "=" * 60)
    print("  SESSION SUMMARY")
    print("=" * 60)
    print(f"  Slices executed: {executed}/{n_slices}")
    print(f"  Total slippage trades: {report.get('n', 0)}")
    print(f"  Mean slippage: {report.get('mean', 0):.6f}")
    print(f"  Max slippage: {report.get('max', 0):.6f}")
    remaining = sum(l.volume for l in book.bids)
    print(f"  Book thinning: {thinning:.1f}% consumed")
    print(f"  Storage: system_state_paper.json (cô lập)")
    print("=" * 60)


def cmd_phase4(args):
    """Phase 4 CAS-DSM — Capitulation Detector + Scale-In + Abortion Protocol."""
    from src.core.market_snapshot import tao_anh_chup
    from src.alpha.capitulation_detector import in_bao_cao as p4_report
    anh_chup = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    p4_report(anh_chup)


def cmd_break_glass(args):
    """Break-Glass Protocol — Single-Pass Key-Anchored Override."""
    from src.portfolio.break_glass import BreakGlassProtocol
    bg = BreakGlassProtocol()
    if args.action == "request":
        result = bg.request(use_time_delay=args.delay)
        print("=" * 50)
        print("  BREAK-GLASSS PROTOCOL — REQUEST")
        print("=" * 50)
        print(f"  Ticket:     {result['ticket_id']}")
        print(f"  Challenge:  {result['challenge']}")
        if result['use_time_delay']:
            print(f"  Mode:       TIME-DELAY ({result['time_delay_minutes']} phút)")
        else:
            print(f"  Mode:       PASSPHRASE")
        print(f"  Hạn:        {result['expires_at']}")
        print(f"  Hint:       python ptck.py break-glass verify {result['ticket_id']} --passphrase <KEY>")
        print("=" * 50)
    elif args.action == "verify":
        result = bg.verify(args.ticket, passphrase=args.passphrase)
        if result["success"]:
            print(f"✅ BREAK-GLASS APPROVED (mode={result['mode']})")
            print("   Khóa đã được gỡ. Có thể thao tác ORPHANED.")
        else:
            print(f"❌ BREAK-GLASS DENIED: {result['reason']}")
            if "remaining_minutes" in result:
                print(f"   Còn {result['remaining_minutes']} phút trong time-delay.")
    elif args.action == "cancel":
        from src.portfolio.break_glass import BreakGlassProtocol
        bg = BreakGlassProtocol()
        result = bg.cancel(args.ticket)
        if result["success"]:
            print("✅ BREAK-GLASS CANCELLED.")
        else:
            print(f"❌ {result['reason']}")
    elif args.action == "status":
        from src.portfolio.system_state import get_state
        st = get_state()
        print("=" * 50)
        print("  PORTFOLIO CRITICAL LOCK STATUS")
        print("=" * 50)
        print(f"  Locked:         {st.get('locked', False)}")
        print(f"  Total stale %:  {st.get('total_stale_pct', 0.0)}")
        print(f"  Escrow balance: {st.get('escrow_balance', 0.0):.2f}")
        print("=" * 50)


def cmd_stale(args):
    """StalePositionManager — quản lý vốn kẹt + escrow."""
    from src.portfolio.stale_manager import StalePositionManager
    mgr = StalePositionManager()
    if args.action == "status":
        s = mgr.status()
        print("=" * 50)
        print("  STALE POSITION MANAGER")
        print("=" * 50)
        print(f"  Lớp stale:     {s['stale_layers']} / {s['total_layers']}")
        print(f"  stale_pcts:    {s['stale_pcts']}")
        print(f"  Tổng stale %:  {s['total_stale_pct']}")
        print(f"  Escrow cache:  {s['escrow_balance']:.2f}")
        print(f"  Hard Shutdown: {s['hard_shutdown']}")
        print(f"  Critical Lock: {s['critical_lock']}")
        print("=" * 50)
    elif args.action == "writeoff":
        result = mgr.writeoff_lifo()
        if result["written"]:
            for w in result["written"]:
                print(f"  ✅ Write-off {w['campaign_id']}: {w['proceeds']:.2f} VND (PNL {w['realized_pnl']:.2f})")
            print(f"  Escrow: {result['escrow_balance']:.2f} | Stale còn lại: {result['remaining_stale_pct']}")
        else:
            print("  Không có stale nào để write-off.")
    elif args.action == "reclaim":
        if not args.campaign:
            print("  ❌ Cần --campaign <id>")
            return
        result = mgr.reclaim(args.campaign)
        print(f"  {'✅' if result['success'] else '❌'} {result.get('reason', 'OK')}")
    elif args.action == "escrow":
        print(f"  Escrow balance: {mgr.escrow_balance:.2f}")
        print(f"  Hard Shutdown:  {mgr.hard_shutdown}")


def cmd_phase5(args):
    """Phase 5 UAT — Paper Trading Simulation với Order Book Thinning."""
    from src.execution.paper_broker import PaperBroker, OrderBook, StreamingFeed
    from src.execution.twap_executor import TWAPExecutor
    from src.portfolio.stale_manager import StalePositionManager
    from src.portfolio.paper_context import set_paper_mode, is_paper_mode

    # Kích hoạt sandbox storage
    set_paper_mode(True)
    print("=" * 55)
    print("  PHASE 5 — PAPER TRADING SIMULATION")
    print("  Storage: system_state_paper.json (cô lập)")
    print("=" * 55)

    n_slices = args.slices
    order_volume = args.depth
    base_price = args.price

    # Build market
    print(f"\n  📊 Market: {base_price} | Depth/level: {order_volume} | Slices: {n_slices}")
    book = OrderBook.build("SANDBOX", mid=base_price, depth_per_level=order_volume, n_levels=5)
    broker = PaperBroker(book=book)

    mgr = StalePositionManager(total_capital=1_000_000)
    mgr.ingest_stale("v1", 0.13)
    mgr.ingest_stale("v2", 0.10)

    exe = TWAPExecutor(mgr, broker=broker)
    plan = exe.build_plan(n_slices=n_slices)
    slice_size = plan.slices[0].amount if plan.slices else 0
    print(f"  Plan: {plan.n_slices} slices × {slice_size:.2f} = {plan.total_amount:.2f} total")

    # Execute slices
    print(f"\n  ── Execution ──")
    for i in range(1, n_slices + 1):
        price = broker.get_best_bid("SANDBOX")
        try:
            r = exe.execute_slice(i, price=price)
            o = broker.query_order(r["broker_order_id"])
            slip = o.get("slippage", 0)
            status = o["status"]
            fill = o.get("filled_qty", 0)
            total_qty = o.get("quantity", 0)
            print(f"  Slice {i}: {status} | fill={fill:.0f}/{total_qty:.0f} | slippage={slip:.4f}")
        except RuntimeError as e:
            print(f"  Slice {i}: ❌ {e}")

    # Slippage report + thinning
    report = broker.slippage_report()
    print(f"\n  ── Slippage Report ──")
    print(f"  Trades: {report['n']} | Mean: {report['mean']:.6f} | Max: {report['max']:.6f}")
    remaining_bid_depth = sum(l.volume for l in book.bids)
    initial_depth = order_volume * 5 * 0.8  # approximate initial
    thinning_pct = 100 * (1 - remaining_bid_depth / max(initial_depth, 1))
    print(f"  Book thinning: {thinning_pct:.1f}% bid depth consumed")

    # Restore production
    set_paper_mode(False)
    print(f"\n  ✅ Paper session complete — paths restored to production.")


def cmd_twap(args):
    """TWAPExecutor — thanh lý stale positions qua slices."""
    from src.portfolio.stale_manager import StalePositionManager
    from src.execution.twap_executor import TWAPExecutor, BrokerAPI
    mgr = StalePositionManager()
    if not mgr.stale_pcts:
        print("  ❌ Không có stale positions — nothing to TWAP.")
        return
    broker = BrokerAPI()
    exe = TWAPExecutor(mgr, broker=broker)
    if args.action == "plan":
        plan = exe.build_plan(n_slices=args.slices)
        print("=" * 50)
        print("  TWAP PLAN")
        print("=" * 50)
        print(f"  Campaign:     {plan.stale_campaign_id}")
        print(f"  Total amount: {plan.total_amount:.2f}")
        print(f"  Slices:       {plan.n_slices} × {plan.slices[0].amount:.2f}")
        print(f"  Status:       {plan.status}")
        print("=" * 50)
    elif args.action == "run":
        exe.build_plan(n_slices=5)
        try:
            result = exe.execute_slice(args.slice, price=args.price)
            if result["success"]:
                print(f"  ✅ Slice {args.slice} submitted: {result['broker_order_id']}")
            else:
                print(f"  ❌ {result['reason']}")
        except RuntimeError as e:
            print(f"  ❌ {e}")
    elif args.action == "status":
        s = exe.status()
        print("=" * 50)
        print("  TWAP STATUS")
        print("=" * 50)
        print(f"  Plan:         {s['plan_status']}")
        print(f"  Slices:       {s['total_slices']}")
        print(f"  Filled:       {s['filled_amount']:.2f}")
        print(f"  Cursor:       {s['cursor']}")
        print(f"  CB trips:     {s['circuit_breaker_trips']}")
        print(f"  Last network: {s['last_known_good_network']}")
        print("=" * 50)
    elif args.action == "resume":
        result = exe.resume()
        if result["success"]:
            print(f"  ✅ Resume: {result['action']}")
        else:
            print(f"  ❌ {result['reason']}")


def cmd_flow_map(args):
    """Bản đồ Dòng vốn Liên thị trường 4 Tầng."""
    from src.engine.cross_market_flow_map import CrossMarketFlowMap
    from src.services.macro_service import get_macro_status
    from src.core.market_snapshot import tao_anh_chup
    import json
    macro = get_macro_status()
    snapshot = tao_anh_chup()
    engine = CrossMarketFlowMap(macro, snapshot)
    data = engine.execute_pipeline()
    vi = engine.localize(data)

    print("=" * 60)
    print("  BẢN ĐỒ DÒNG VỐN LIÊN THỊ TRƯỜNG")
    print("=" * 60)
    print(f"  Thời gian: {vi.get('thời_gian', 'N/A')}")
    print(f"  Ngày:      {vi.get('ngày', 'N/A')}")
    print()

    drivers = vi.get("lực_đẩy_5_phiên", {})
    print("  🏎️  LỰC ĐẨY 5 PHIÊN GẦN NHẤT")
    for k, v in drivers.items():
        arrow = "+" if v >= 0 else ""
        print(f"    {k.replace('_', ' '):25s}: {arrow}{v}")
    print()

    print("  🏠 VÙNG ĐỊNH CƯ DÒNG TIỀN")
    zone_map = {
        "HẦM_TRÚ_ẨN_TIỀN_MẶT": "Tiền đang rút về hầm trú ẩn tiền mặt — chờ thời",
        "HẦM_TRÚ_ẨN_TÀI_SẢN_CỨNG": "Dòng tiền chạy vào tài sản cứng (Vàng/Bạc)",
        "BUNG_XÕA_CỔ_PHIẾU": "Dòng tiền đang bung xõa vào cổ phiếu",
        "LUÂN_CHUYỂN_NGẦM": "Đang trong trạng thái luân chuyển ngầm — chưa rõ xu hướng",
    }
    settlement = vi.get("vùng_định_cư_dòng_tiền", "N/A")
    print(f"    Mã: {settlement}")
    print(f"    Diễn giải: {zone_map.get(settlement, 'Không xác định')}")
    print()

    rep = vi.get("thẩm_phán_tín_nhiệm", {})
    print("  ⚖️  THẨM PHÁN TÍN NHIỆM")
    print(f"    Mức tin cậy vĩ mô VN: {rep.get('mức_tin_cậy', 'N/A')}")
    print(f"    Chất lượng VGB10Y:    {rep.get('chất_lượng_dữ_liệu_vgb10y', 'N/A')}")
    print(f"    Lãi suất liên NH:     {rep.get('trạng_thái_lãi_suất_liên_ngân_hàng', 'N/A')}")
    print()

    drift = vi.get("cảnh_báo_lệch_pha", {})
    print("  ⚡ CẢNH BÁO LỆCH PHA")
    has_drift = drift.get("có_lệch_pha", False)
    if has_drift:
        print(f"    ⚠️  {drift.get('lý_do', 'Phát hiện lệch pha')}")
    else:
        print("    ✅ An toàn — không lệch pha")
    print("=" * 60)


# Module-level language mode — set by main() before dispatching
_VERBOSE_LANG: str = "auto"


def _ll(label: str) -> str:
    """Shorthand: localize_label with current global mode."""
    from src.core.canonical_output_adapter import localize_label
    return localize_label(label, _VERBOSE_LANG)


def _check_sbv_alert_startup():
    """Startup guardrail: kiểm tra alert file, in cảnh báo nếu có."""
    try:
        from src.services.macro.interbank_seeder import _is_sbv_alert_active
        if _is_sbv_alert_active():
            print("=" * 60)
            print(" ⚠ CẢNH BÁO: PHÁO ĐÀI SENTINEL PHÁT HIỆN SỰ CỐ CẤU TRÚC DỮ LIỆU SBV")
            print(" Hệ thống đang vận hành trong trạng thái MÙ VĨ MÔ (Vị thế khóa cứng = 0.0).")
            print(" Thực thi ngay: python ptck.py sbv-update để cập nhật fixtures.")
            print("=" * 60)
            print()
    except Exception:
        pass


def main():
    global _VERBOSE_LANG
    from src.engine.startup_reminder import kiem_tra_va_nhac_nho
    kiem_tra_va_nhac_nho()
    _check_sbv_alert_startup()

    parser = argparse.ArgumentParser(
        prog="ptck",
        description="PTCK_VNSTOCK CLI — Single entrypoint cho mọi thao tác",
    )
    lang_parent = argparse.ArgumentParser(add_help=False)
    lang_parent.add_argument(
        '--verbose-lang', choices=['compact', 'annotated', 'full', 'auto'],
        default='auto', dest='verbose_lang',
        help='Chế độ hiển thị ngôn ngữ CLI (compact/annotated/full/auto)',
    )
    # Also accept --verbose-lang before subcommand
    parser.add_argument(
        '--verbose-lang', choices=['compact', 'annotated', 'full', 'auto'],
        default='auto', dest='verbose_lang',
        help=argparse.SUPPRESS,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # market
    p_market = sub.add_parser("market", parents=[lang_parent], help="Market snapshot")
    p_market.set_defaults(func=cmd_market)

    # regime
    p_regime = sub.add_parser("regime", parents=[lang_parent], help="Regime analysis chi tiết")
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
    p_du.add_argument("--manifest", help="Path to missing_manifest.json (gap filling)")
    p_du.set_defaults(func=cmd_daily_update)

    # gap-analyzer
    p_ga = sub.add_parser("gap-analyzer", help="Kiểm toán dữ liệu - phát hiện missing symbols/dates")
    p_ga.add_argument("--output", help="Đường dẫn xuất manifest (mặc định: backend/data/missing_manifest.json)")
    p_ga.add_argument("--lookback-months", type=int, default=3, help="Số tháng phân tích (mặc định: 3)")
    p_ga.set_defaults(func=cmd_gap_analyzer)

    # telemetry
    p_tel = sub.add_parser("telemetry", help="Telemetry & reputation")
    p_tel.add_argument("subcommand", choices=["reputation", "shadow"])
    p_tel.add_argument("--refresh", action="store_true", help="Refresh dữ liệu")
    p_tel.add_argument("--drift", action="store_true", help="Drift Monitor (chi tiết độ dốc + half-life)")
    p_tel.add_argument("--days", type=int, default=30, help="Số ngày cho shadow metrics")
    p_tel.set_defaults(func=cmd_telemetry)

    # serve
    p_serve = sub.add_parser("serve", help="Khởi động server")
    p_serve.add_argument("--port", type=int, default=17039, help="Port (mặc định 17039)")
    p_serve.set_defaults(func=cmd_serve)

    # db
    p_db = sub.add_parser("db", help="Database operations")
    p_db.add_argument("subcommand", choices=["vacuum", "stats", "optimize"])
    p_db.set_defaults(func=cmd_db)

    # status
    p_status = sub.add_parser("status", parents=[lang_parent], help="Kiểm tra sức khỏe hệ thống")
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
    p_snap = sub.add_parser("snapshot", parents=[lang_parent], help="Ảnh chụp thị trường duy nhất")
    p_snap.set_defaults(func=cmd_snapshot)

    # ddi
    p_ddi = sub.add_parser("ddi", parents=[lang_parent], help="Delta Divergence Index — Δ_SA filter")
    p_ddi.set_defaults(func=cmd_ddi)

    # snapshot-index
    p_si = sub.add_parser("snapshot-index", help="Xem lịch sử snapshot_index.json")
    p_si.add_argument("--limit", type=int, default=20, help="Số dòng hiển thị (mặc định 20)")
    p_si.set_defaults(func=cmd_snapshot_index)

    # registry
    p_reg = sub.add_parser("registry", help="Param Fingerprint Registry — tra cứu params_hash theo regime")
    p_reg.add_argument("--regime", default=None, help="Lọc theo regime (TRENDING/RANGING/CRISIS)")
    p_reg.add_argument("--rebuild", action="store_true", help="Xây dựng lại registry từ audit log")
    p_reg.set_defaults(func=cmd_registry)

    # phase4
    p_p4 = sub.add_parser("phase4", parents=[lang_parent],
                          help="Phase 4 CAS-DSM — Capitulation Detector + Scale-In + Abortion")
    p_p4.set_defaults(func=cmd_phase4)

    # break-glass
    p_bg = sub.add_parser("break-glass", help="Break-Glass Protocol — override Hard Shutdown")
    p_bg_sub = p_bg.add_subparsers(dest="action", required=True)
    p_bg_req = p_bg_sub.add_parser("request", help="Yêu cầu override ticket")
    p_bg_req.add_argument("--delay", action="store_true", help="Dùng time-delay mode (15 phút)")
    p_bg_req.set_defaults(func=cmd_break_glass)
    p_bg_ver = p_bg_sub.add_parser("verify", help="Xác thực ticket")
    p_bg_ver.add_argument("ticket", help="Ticket ID")
    p_bg_ver.add_argument("--passphrase", help="Passphrase (nếu không dùng time-delay)")
    p_bg_ver.set_defaults(func=cmd_break_glass)
    p_bg_can = p_bg_sub.add_parser("cancel", help="Hủy ticket")
    p_bg_can.add_argument("ticket", help="Ticket ID")
    p_bg_can.set_defaults(func=cmd_break_glass)
    p_bg_sta = p_bg_sub.add_parser("status", help="Xem trạng thái lock")
    p_bg_sta.set_defaults(func=cmd_break_glass)

    # stale
    p_st = sub.add_parser("stale", help="StalePositionManager — quản lý vốn kẹt + escrow")
    p_st_sub = p_st.add_subparsers(dest="action", required=True)
    p_st_sta = p_st_sub.add_parser("status", help="Xem trạng thái stale layers + escrow")
    p_st_sta.set_defaults(func=cmd_stale)
    p_st_wo = p_st_sub.add_parser("writeoff", help="Write-off LIFO (campaign mới nhất trước)")
    p_st_wo.set_defaults(func=cmd_stale)
    p_st_re = p_st_sub.add_parser("reclaim", help="Reclaim một lớp stale")
    p_st_re.add_argument("--campaign", help="Campaign ID (vd: v1)")
    p_st_re.set_defaults(func=cmd_stale)
    p_st_es = p_st_sub.add_parser("escrow", help="Xem escrow balance")
    p_st_es.set_defaults(func=cmd_stale)

    # twap
    p_tw = sub.add_parser("twap", help="TWAP Executor — thanh lý stale positions")
    p_tw_sub = p_tw.add_subparsers(dest="action", required=True)
    p_tw_plan = p_tw_sub.add_parser("plan", help="Xây dựng kế hoạch TWAP")
    p_tw_plan.add_argument("--slices", type=int, default=5, help="Số slice (mặc định 5)")
    p_tw_plan.set_defaults(func=cmd_twap)
    p_tw_run = p_tw_sub.add_parser("run", help="Thực thi TWAP")
    p_tw_run.add_argument("--slice", type=int, default=1, help="Slice index bắt đầu")
    p_tw_run.add_argument("--price", type=float, default=100.0, help="Giá limit")
    p_tw_run.set_defaults(func=cmd_twap)
    p_tw_sta = p_tw_sub.add_parser("status", help="Xem trạng thái TWAP")
    p_tw_sta.set_defaults(func=cmd_twap)
    p_tw_resume = p_tw_sub.add_parser("resume", help="Resume TWAP sau mất mạng")
    p_tw_resume.set_defaults(func=cmd_twap)

    # phase5
    p_p5 = sub.add_parser("phase5", help="Phase 5 UAT — Paper Trading Simulation")
    p_p5.add_argument("--slices", type=int, default=5, help="Số slice (mặc định 5)")
    p_p5.add_argument("--depth", type=float, default=5000, help="Depth mỗi level (mặc định 5000)")
    p_p5.add_argument("--price", type=float, default=100.0, help="Giá mid (mặc định 100)")
    p_p5.set_defaults(func=cmd_phase5)

    # sandbox
    p_sb = sub.add_parser("sandbox", help="Phase 5 Dashboard — Real-time Paper Trading")
    p_sb.add_argument("--slices", type=int, default=5, help="Số slice (mặc định 5)")
    p_sb.add_argument("--depth", type=float, default=5000, help="Depth mỗi level (mặc định 5000)")
    p_sb.add_argument("--price", type=float, default=100.0, help="Giá mid (mặc định 100)")
    p_sb.add_argument("--capital", type=float, default=1_000_000, help="Vốn giả lập (mặc định 1,000,000)")
    p_sb.set_defaults(func=cmd_sandbox)

    # confidence
    p_conf = sub.add_parser("confidence", help="Bộ tự đánh giá độ tin cậy")
    p_conf.add_argument("--date", help="Ngày phân tích (YYYY-MM-DD)")
    p_conf.set_defaults(func=cmd_confidence)

    # scan
    p_scan = sub.add_parser("scan", help="Elite scanner")
    p_scan.add_argument("--deep", action="store_true", help="Deep scan")
    p_scan.set_defaults(func=cmd_scan)

    # gold
    p_gold = sub.add_parser("gold", help="Gold information")
    p_gold.add_argument("subcommand", nargs="?", choices=["regime"], default=None, help="Gold subcommand")
    p_gold.set_defaults(func=cmd_gold)

    # silver
    p_silver = sub.add_parser("silver", help="Silver information")
    p_silver.add_argument("subcommand", nargs="?", choices=["gs-ratio", "seed"], default=None, help="Silver subcommand")
    p_silver.set_defaults(func=cmd_silver)

    # rs-audit
    p_ra = sub.add_parser("rs-audit", help="Audit Top RS — phân tích nguồn gốc sức mạnh")
    p_ra.add_argument("--top", type=int, default=20, help="Số lượng mã (mặc định 20)")
    p_ra.add_argument("--all", action="store_true", dest="show_all", help="Hiện tất cả mã RS")
    p_ra.set_defaults(func=cmd_rs_audit)

    # group-influence
    p_gi = sub.add_parser("group-influence", help="Bộ đo ảnh hưởng nhóm trụ")
    p_gi.set_defaults(func=cmd_group_influence)

    # strength-discrimination
    p_sd = sub.add_parser("strength-discrimination", help="Phân tách xung lực nội tại vs ép trụ")
    p_sd.add_argument("--top", type=int, default=20, help="Số lượng mã (mặc định 20)")
    p_sd.set_defaults(func=cmd_strength_discrimination)

    # prediction-registry
    p_pr = sub.add_parser("prediction-registry", help="Nhật ký dự báo — ghi log RS audit + đo kết quả sau 5/20/60 ngày")
    p_pr.add_argument("action", choices=["log", "update", "stats", "list"], default="stats", nargs="?")
    p_pr.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_pr.add_argument("--limit", type=int, default=20, help="Số entries (mặc định 20)")
    p_pr.set_defaults(func=cmd_prediction_registry)

    # flow-map
    p_fm = sub.add_parser("flow-map", help="Bản đồ Dòng vốn Liên thị trường 4 Tầng")
    p_fm.set_defaults(func=cmd_flow_map)

    # cleanup
    p_cl = sub.add_parser("cleanup", help="Dọn dẹp định kỳ: telemetry, state files")
    p_cl.add_argument("target", choices=["telemetry", "all"], help="telemetry: entropy_log; all: telemetry + state")
    p_cl.add_argument("--max-rows", type=int, default=1000, dest="max_rows", help="Số dòng telemetry giữ lại")
    p_cl.set_defaults(func=cmd_cleanup)

    # clear-crisis
    p_ccr = sub.add_parser("clear-crisis", help="Xóa crisis_cooldown + stress gate — reset trạng thái khủng hoảng thủ công")
    p_ccr.set_defaults(func=cmd_clear_crisis)

    # sbv-update
    p_su = sub.add_parser("sbv-update", help="5-step recovery: backup → fixtures → pytest → scrape → clear alert")
    p_su.add_argument("--force", action="store_true", dest="sbv_force",
                      help="Bỏ qua CRITICAL_WARNING — clear CRISIS_REAL marker thủ công")
    p_su.set_defaults(func=cmd_sbv_update)

    # data-quality
    p_dq = sub.add_parser("data-quality", help="Đánh giá độ tin cậy dữ liệu (TẦNG 0)")
    p_dq.set_defaults(func=cmd_data_quality)

    # index-decompose
    p_id = sub.add_parser("index-decompose", help="Phân tích chỉ số thị trường thống nhất")
    p_id.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_id.set_defaults(func=cmd_index_decompose)

    # restore-backup
    p_rb = sub.add_parser("restore-backup", help="Khôi phục trạng thái alert từ backup gần nhất")
    p_rb.set_defaults(func=cmd_restore_backup)

    # check-calendar
    p_cc = sub.add_parser("check-calendar", help="Kiểm tra lịch lễ so với nguồn online")
    p_cc.add_argument("--auto-update", action="store_true", help="Tự động cập nhật calendar nếu lệch")
    p_cc.set_defaults(func=cmd_check_calendar)

    # backfill-history
    p_bf = sub.add_parser("backfill-history", help="Khôi phục dữ liệu lịch sử cho mã thiếu (Backfill)")
    p_bf.add_argument("--symbols", help="Chỉ định mã cụ thể, cách nhau bằng dấu phẩy (VD: VCB,REE,HDB)")
    p_bf.add_argument("--start", default=None, help="Ngày bắt đầu (YYYY-MM-DD, mặc định 2021-01-01)")
    p_bf.add_argument("--end", default=None, help="Ngày kết thúc (YYYY-MM-DD, mặc định hôm nay)")
    p_bf.add_argument("--dry-run", action="store_true", dest="dry_run", help="Chạy thử — không ghi vào DB")
    p_bf.add_argument("--quiet", action="store_true", help="Chỉ in tóm tắt, không in từng mã")
    p_bf.set_defaults(func=cmd_backfill)

    # backfill-macro (Hotfix #2)
    p_bfm = sub.add_parser("backfill-macro", help="Backfill 1y historical data cho PTD macro tickers")
    p_bfm.add_argument("--days", type=int, default=252, help="Số ngày lịch sử (mặc định 252)")
    p_bfm.set_defaults(func=cmd_backfill_macro)

    args = parser.parse_args()
    _VERBOSE_LANG = args.verbose_lang
    args.func(args)


if __name__ == "__main__":
    main()
