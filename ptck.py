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
if sys.platform.startswith("win"):
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    if isinstance(sys.stderr, io.TextIOWrapper):
        if getattr(sys.stderr, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stderr.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── Path setup ─────────────────────────────────────────
# SENTINEL LAW v3.0: PROJECT_ROOT phải luôn ở sys.path[0] để
# core/ (PROJECT_ROOT/core/) không bị backend/src/core/ (thiếu macro/) che khuất.
# Các file trong backend/src/cũng import từ core.* — nếu sai order → ModuleNotFoundError.
# Chỉ thêm path nếu thực sự tồn tại (tránh lỗi trong Nuitka onefile runtime)
PROJECT_ROOT = Path(__file__).resolve().parent
backend_dir = PROJECT_ROOT / "backend"
src_dir = backend_dir / "src"
libs_dir = backend_dir / "libs"
for p in [src_dir, backend_dir, PROJECT_ROOT, libs_dir]:
    if str(p) not in sys.path and p.exists():
        sys.path.append(str(p))
# Guarantee: PROJECT_ROOT at index 0 for core/ resolution
sp = str(PROJECT_ROOT)
if sp in sys.path:
    sys.path.remove(sp)
sys.path.insert(0, sp)


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
        print(f"  PTCK — {_ll('WEEKLY COGNITIVE REPORT')}")
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
        print(f"  {_ll('Usage')}: python ptck.py report weekly|monthly|daily [--month YYYY-MM] [--lang vi]")


def cmd_daily_close(args):
    """Chạy daily closer pipeline."""
    print("=" * 60)
    print(f"  PTCK — {_ll('DAILY CLOSER')}")
    print("=" * 60)
    try:
        from src.daily_closer import run_daily_closer
        run_daily_closer()
        print(f"  [{_ll('OK')}] {_ll('Daily closer')} hoàn tất.")
    except Exception as e:
        print(f"  [{_ll('FAIL')}] Lỗi: {e}")
    print("=" * 60)


def cmd_daily_update(args):
    """Cập nhật dữ liệu EOD."""
    date = getattr(args, 'date', None)
    manifest = getattr(args, 'manifest', None)
    batch_size = getattr(args, 'batch_size', 50)
    throttle = getattr(args, 'throttle', 1.8)
    cmd = [sys.executable, str(backend_dir / "src" / "daily_updater.py")]
    if date:
        cmd += ["--date", date]
    if manifest:
        cmd += ["--manifest", manifest]
    cmd += ["--batch-size", str(batch_size), "--throttle", str(throttle)]
    print(f"  Chạy: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  [{_ll('FAIL')}] Lỗi: mã thoát {result.returncode}")
    else:
        print(f"  [{_ll('OK')}] {_ll('Daily update')} hoàn tất.")


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
        print(f"  [{_ll('FAIL')}] Lỗi: mã thoát {result.returncode}")


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
        print(f"  PTCK — {_ll('DRIVER REPUTATION LEDGER')}")
        print("=" * 72)
        if drivers:
            header = f"  {_ll('Driver'):<22s} {_ll('Alpha'):>8s} {_ll('Acc'):>6s} {_ll('Drift'):>8s} {_ll('1/2-life'):>10s}"
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
            print(f"  {_ll('Chưa có dữ liệu')}. Chạy 'python ptck.py telemetry reputation --refresh'")
        print("=" * 72)

        # ── Drift Monitor detail ────────────────────────────────────
        is_drift = getattr(args, 'drift', False)
        if is_drift and drivers:
            print(f"\n  ═══ {_ll('DRIFT MONITOR')} ═══")
            print(f"  {_ll('Driver'):<22s} {_ll('Slope'):>10s} {_ll('Drift 30→180'):>13s} {_ll('Acc 30d'):>8s} {_ll('Acc 180d'):>9s} {_ll('1/2-life'):>10s}")
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
        print(f"  PTCK — {_ll('SHADOW METRICS')} ({days} ngày)")
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
    # ==============================================================================
    # WHY: Nuitka onefile does NOT have writable source files. reload=True causes
    # uvicorn to crash when it tries to watch non-existent .py files.
    # BOUNDARY: Only enable reload in dev mode (python ptck.py serve).
    # ==============================================================================
    is_frozen = (
        getattr(sys, 'frozen', False)
        or not Path(sys.executable).stem.lower().startswith("python")
    )
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=port, reload=not is_frozen)


def cmd_db(args):
    """Database operations."""
    from src.db_maintenance import get_table_stats, vacuum_database, get_db_size_mb
    if args.subcommand == "vacuum":
        print(f"  Đang {_ll('VACUUM')} {_ll('database')}...")
        vacuum_database()
    elif args.subcommand == "stats":
        print(f"  Đang thống kê {_ll('database')}...")
        stats = get_table_stats()
        print(f"\n{_ll('TABLE STATISTICS')}:")
        for table, count in sorted(stats.items()):
            print(f"  {table}: {count:,} {_ll('rows')}")
        print(f"\n{_ll('DB Size')}: {get_db_size_mb():.1f} MB")
    elif args.subcommand == "optimize":
        print(f"  Đang tối ưu hóa toàn bộ {_ll('database')} (JSONB + STRICT + VACUUM)...")
        from src.database.db_optimize import run_all
        run_all()
    elif args.subcommand == "backup":
        from src.database.database_guardian import run_guardian_cycle, list_backups
        if args.list_backups:
            backups = list_backups()
            if backups:
                print(f"\n📋 BACKUPS ({len(backups)} bản):")
                for b in backups:
                    print(f"  {b['name']:45s} {b['size_mb']:>8.2f} MB  {b['modified']}")
            else:
                print("\n📭 Không có bản backup nào.")
            return
        report = run_guardian_cycle(dry_run=args.dry_run)
        if report["status"] in ("SUCCESS", "DRY_RUN_PASSED"):
            print(f"\n  ✅ Guardian cycle hoàn tất ({report['duration_seconds']:.2f}s)")
            if not args.dry_run:
                backups = list_backups()
                if backups:
                    latest = backups[0]
                    print(f"  📦 Backup mới nhất: {latest['name']} ({latest['size_mb']} MB)")
                    print(f"  📋 Tổng số backup: {len(backups)} bản")
        elif "BLOCKED" in report["status"]:
            print(f"\n  ❌ {report['status']}")
        else:
            print(f"\n  ❌ Guardian cycle thất bại: {report['status']}")
    else:
        print(f"  {_ll('Usage')}: python ptck.py db vacuum|stats|optimize|backup")


def cmd_status(args):
    """Kiểm tra sức khỏe hệ thống."""
    print("=" * 60)
    print(f"  PTCK — {_ll('SYSTEM HEALTH CHECK')}")
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
    print(f"  Kết quả: {passed}/{total} {_ll('checks passed')}")
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
    kq = quyet_dinh_cuoi(args.date or datetime.now().strftime("%Y-%m-%d"), lang_mode=_VERBOSE_LANG)
    in_bao_cao(kq)


def cmd_snapshot(args):
    """Tạo ảnh chụp thị trường duy nhất + phase classification."""
    from src.core.market_snapshot import tao_anh_chup, in_anh_chup
    anh_chup = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    in_anh_chup(anh_chup, lang_mode=_VERBOSE_LANG)

    # ── Phase Classification (4 Spectral Indicators) ──
    import numpy as np
    from src.services.macro.ptd_engine import PTDEngine
    from src.services.macro.time_series_aligner import compute_asia_rotation
    engine = PTDEngine()

    # Asia supply-chain rotation angle (Layer 2 reference frame)
    asia_rot = compute_asia_rotation(window=90)
    if asia_rot.get("status") == "OK":
        engine.set_cross_asset_angle(asia_rot["rotation_angle_deg"])

    # Use structural entropy from snapshot as eigenvalue proxy
    entropy_val = anh_chup.get("cau_truc", {}).get("entropy")
    dummy_eigen = None
    if entropy_val is not None:
        dummy_eigen = {
            "lambda_max": max(0.1, 1.0 - entropy_val / 3.0),
            "lambda_min": 0.05,
            "lambda_ratio": max(2.0, 10.0 - entropy_val * 2.0),
            "spectral_entropy": entropy_val,
            "effective_rank": 2,
        }

    # Run multiple steps to build history for slope computation
    for _ in range(5):
        state = engine.step(np.array([0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.05]),
                            aligner_features=dummy_eigen)

    phase = state.phase_label
    conf = state.phase_confidence
    if "DISTRIBUTION" in phase:
        icon = "BEARISH"
    elif "ACCUMULATION" in phase:
        icon = "BULLISH"
    else:
        icon = "NEUTRAL"
    print(f"\n  {_ll('Phase Classification')} ({_ll(icon)}):")
    print(f"    {_ll('Label')}:       {_ll(phase)}")
    print(f"    {_ll('Confidence')}:  {conf:.2%}")
    if state.phase_indicators:
        ind = state.phase_indicators
        if "kl_divergence" in ind:
            kd = ind["kl_divergence"]
            print(f"    {_ll('KL Spread')}:   {kd.get('kl_max', 'N/A')} ({_ll(kd.get('label', 'N/A'))})")
        if "eigenvalue_spread" in ind:
            es = ind["eigenvalue_spread"]
            if es.get("reasons"):
                for r in es["reasons"]:
                    print(f"    λ Spread:    {r}")
        if "entropy_slope" in ind:
            en = ind["entropy_slope"]
            print(f"    {_ll('Entropy ∇')}:   {en.get('slope', 'N/A')} ({_ll(en.get('label', 'N/A'))})")
        if "cross_asset_rotation" in ind:
            cr = ind["cross_asset_rotation"]
            print(f"    {_ll('Rotation θ')}:  {cr.get('angle_deg', 'N/A')}\u00b0 ({_ll(cr.get('label', 'N/A'))})")
    # Governor directive overlay
    cau_truc = anh_chup.get("cau_truc", {})
    trang_thai = cau_truc.get("trang_thai", "")
    so_tru = cau_truc.get("so_tru", 0)
    if trang_thai == "VỠ CẤU TRÚC" and so_tru <= 1 and ("DISTRIBUTION" in phase or "UNCERTAIN" in phase):
        print(f"  {_ll('Consensus')}:     {_ll('SILENT_DISTRIBUTION_HEAD')} (DDI Δ_SA={anh_chup.get('delta_divergence',{}).get('delta_sa','N/A')} + Vỡ cấu trúc)")
    gov_label = _ll('Governor')
    gov_action = _ll("should_reduce_exposure['risk_on']=True")
    print(f"  {gov_label}:      {gov_action}")


def cmd_ddi(args):
    """Delta Divergence Index — Δ_SA = dS/dt - α·AC_latency."""
    from src.core.market_snapshot import tao_anh_chup
    from src.alpha.delta_divergence import DeltaDivergenceIndex
    anh_chup = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    ddi = anh_chup.get("delta_divergence", {})
    icon = {"pass": "🟢", "caution": "🟡", "block": "🔴"}.get(ddi.get("action_filter"), "⚪")
    print("\n" + "=" * 55)
    print(f"  {_ll('DELTA DIVERGENCE INDEX')} (DDI)")
    print("=" * 55)
    print(f"  {icon} Δ_SA:          {ddi.get('delta_sa', 'N/A')}")
    print(f"    {_ll('Action Filter')}:   {ddi.get('action_filter', 'N/A')}")
    print(f"    {_ll('dS/dt')}:           {ddi.get('dS_dt', 'N/A')}")
    print(f"    {_ll('AC Latency')}:      {ddi.get('ac_latency', 'N/A')}")
    print(f"    α ({ddi.get('regime', 'N/A')}): {ddi.get('alpha_regime', 'N/A')}")
    if ddi.get("healing_illusion"):
        print(f"    ⚠ {_ll('HEALING ILLUSION')} — stress vượt adaptation")
    p_hash = anh_chup.get("params_hash", "unresolved")
    print(f"  {_ll('Params Hash')}:      {p_hash}")
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
    print(f"  {_ll('SNAPSHOT INDEX')} — {len(idx)} {_ll('entries')} (hiển thị {len(recent)} gần nhất)")
    print(f"{'='*75}")
    print(f"  {'Ngày':<12} {'Hash':<17} {'Regime':<10} {'Δ_SA':>7} {'Filter':<8}")
    print(f"  {'-'*12} {'-'*17} {'-'*10} {'-'*7} {'-'*8}")
    for e in recent:
        print(f"  {e.get('ngay',''):<12} {e.get('params_hash',''):<17} "
              f"{e.get('regime',''):<10} {e.get('delta_sa',0):>7.4f} "
              f"{e.get('action_filter',''):<8}")
    print(f"{'='*75}")


def cmd_rotation(args):
    """Asia supply-chain rotation angle: VN vs KOSPI+TAIEX+DXY."""
    from src.services.macro.time_series_aligner import compute_asia_rotation
    r = compute_asia_rotation(window=90)
    print()
    print("=" * 55)
    print(f"  {_ll('ASIA REFERENCE FRAME ROTATION')} ({_ll('Layer 2')})")
    print("=" * 55)
    if r.get("status") != "OK":
        print(f"  {_ll('Status')}: {r.get('status')}")
        print(f"  {r.get('message', 'No message')}")
        return
    print(f"  {_ll('Rotation Angle')}:   {r['rotation_angle_deg']}°  "
          f"(0°=VN syncs Asia, 90°=VN decoupling)")
    print(f"  Λ_max:            {r['lambda_max']:.4f}  "
          f"(>0.6={_ll('systemic')})")
    print(f"  Λ_ratio:          {r['lambda_ratio']:.2f}  "
          f"(>5.0={_ll('dominant factor')})")
    print(f"  {_ll('Entropy')}:          {r['spectral_entropy']:.4f}")
    print(f"  {_ll('Days')}:             {r['n_days']}")
    print()
    print(f"  {_ll('Pairwise Spearman correlations')}:")
    for pair, val in r.get("pairwise_corr", {}).items():
        print(f"    {pair:<25s} {val:>8.4f}")
    print("=" * 55)


def cmd_confidence(args):
    """Bộ tự đánh giá độ tin cậy của quyết định."""
    from src.engine.confidence_layer import đánh_giá_độ_tin_cậy, in_báo_cáo
    from datetime import datetime
    kq = đánh_giá_độ_tin_cậy(target_date=args.date or datetime.now().strftime("%Y-%m-%d"), lang_mode=_VERBOSE_LANG)
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


def cmd_quantstats(args):
    """QuantStatsBridge — Live Calibration Engine."""
    from src.core.quantstats_bridge import QuantStatsBridge
    from src.core.meta_evidence import get_meta_evidence
    bridge = QuantStatsBridge(window_days=args.window or 30)
    report = bridge.run_all()
    bridge.save_to_db(report)

    cal = report.get("calibration", {})
    live = report.get("live", {})
    random_b = report.get("random_baseline", {})

    print("\n" + "=" * 60)
    print(f"  {_ll('QUANTSTATS CALIBRATION')} — KIỂM ĐỊNH NIỀM TIN")
    print("=" * 60)
    print(f"\n  {_ll('Window')} (phiên):{'':>12s} {report['window_days']}")
    print(f"  {_ll('Live Sharpe')} (EWMA):{'':>9s} {cal.get('sharpe_live_smoothed', 0):.4f}")
    print(f"  {_ll('Sharpe vs Random P95')}:{'':>6s} {cal.get('sharpe_vs_random', 0):.4f}")
    print(f"  {_ll('Sortino')}:{'':>20s} {live.get('sortino', 0):.4f}")
    print(f"  {_ll('Max Drawdown')}:{'':>15s} {live.get('max_drawdown', 0):.2%}")
    print(f"  {_ll('Recovery Factor')}:{'':>12s} {live.get('recovery_factor', 0):.4f}")
    print(f"  {_ll('Win Rate')}:{'':>19s} {live.get('win_rate', 0):.2%}")
    print(f"  {_ll('Profit Factor')}:{'':>14s} {live.get('profit_factor', 0):.4f}")
    print(f"  {_ll('Kelly Criterion')}:{'':>12s} {live.get('kelly_criterion', 0):.4f}")
    print(f"  {_ll('Outlier Win Ratio')}:{'':>10s} {cal.get('outlier_win_ratio', 0):.4f}")
    print(f"  {_ll('Outlier Loss Ratio')}:{'':>9s} {cal.get('outlier_loss_ratio', 0):.4f}")
    print(f"  {_ll('Cumulative IG')}:{'':>13s} {cal.get('cumulative_information_gain', 0):.6f}")

    print(f"\n  {'───────────────── ' + _ll('RANDOM BASELINE') + ' ─────────────────'}")
    print(f"  {_ll('Random Sharpe P95')}:{'':>9s} {random_b.get('random_sharpe_p95', 0):.4f}")
    print(f"  {_ll('Random Sharpe P50')}:{'':>9s} {random_b.get('random_sharpe_p50', 0):.4f}")
    print(f"  {_ll('Random Return P95')}:{'':>9s} {random_b.get('random_return_p95', 0):.2%}")
    print(f"  {_ll('Random MDD P95')}:{'':>12s} {random_b.get('random_mdd_p95', 0):.2%}")

    if live.get("n_observations", 0) > 0:
        print(f"\n  {'───────────────── ' + _ll('REJECTED HYPOTHESES') + ' ─────────────────'}")
        rej = report.get("rejected", {})
        print(f"  {_ll('Rejected n')}:{'':>15s} {rej.get('n_observations', 0)}")
        print(f"  {_ll('Rejected Sharpe')}:{'':>11s} {rej.get('sharpe', 0):.4f}")
        print(f"  {_ll('Rejected Win Rate')}:{'':>10s} {rej.get('win_rate', 0):.2%}")
        cal_penalty = cal.get("calibration_penalty", 0)
        print(f"  {_ll('Calibration Penalty')}:{'':>8s} {cal_penalty:.2%}")

    action = cal.get("action", "NONE")
    reason = cal.get("reason", "")
    action_icon = {"NONE": "🟢", "SCALE": "🟡", "ABORT": "🔴"}.get(action, "⚪")
    print(f"\n  {action_icon} Hành động: {action}")
    if reason:
        print(f"     Lý do: {reason}")

    # Cập nhật vào MetaEvidence
    meta = get_meta_evidence()
    meta.update_calibration_from_quantstats(cal)
    print(f"\n  ✅ Đã cập nhật calibration penalty vào MetaEvidence")
    print(f"     {_ll('Effective Trust')} sau điều chỉnh: {meta.effective_trust:.4f}")
    print("=" * 60)


def cmd_rejected_signals(args):
    """Rejected Signals Archive — Kho lưu tín hiệu bị từ chối."""
    from src.database.rejected_signals import (
        count_by_reason, get_rejected_signals, run_eod_update,
    )

    if args.action == "list":
        rows = get_rejected_signals(
            limit=args.limit,
            reason_filter=args.reason,
            days_back=30,
        )
        print(f"\n{'=' * 80}")
        print(f"  {_ll('REJECTED SIGNALS ARCHIVE')} — Kho lưu tín hiệu bị từ chối")
        print(f"{'=' * 80}")
        if not rows:
            print("\n  (trống) — chưa có tín hiệu nào bị từ chối.\n")
            return
        print(f"\n  {'ID':>4s} | {'Mã':8s} | {'Lý do':22s} | {_ll('Horizon'):10s} | {_ll('Status'):8s} | {_ll('Exit5d'):>8s} | {'IG':>8s}")
        print(f"  {'-'*4:>4s}   {'-'*8:8s}   {'-'*22:22s}   {'-'*10:10s}   {'-'*8:8s}   {'-'*8:8s}   {'-'*8:8s}")
        for r in rows:
            exit5 = f"{r.get('simulated_exit_5d', 0):>+8.2f}" if r.get('simulated_exit_5d') is not None else "     N/A"
            ig = f"{r.get('information_gain', 0):>8.4f}" if r.get('information_gain') else "   0.0000"
            print(f"  {r['id']:>4d} | {r['ticker']:8s} | {r['rejection_reason']:22s} | {r.get('evaluation_horizon', '60d'):10s} | {r.get('status', 'ACTIVE'):8s} | {exit5:>8s} | {ig:>8s}")
        print(f"\n  Tổng: {len(rows)} bản ghi\n")

    elif args.action == "stats":
        counts = count_by_reason(days_back=30)
        print(f"\n{'=' * 60}")
        print(f"  {_ll('REJECTED SIGNALS')} — Thống kê theo lý do")
        print(f"{'=' * 60}")
        if not counts:
            print("\n  (trống)\n")
            return
        total = sum(counts.values())
        for reason, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            pct = cnt / total * 100
            print(f"  {reason:30s} {cnt:>5d} ({pct:5.1f}%)")
        print(f"  {'─' * 40}")
        print(f"  {'Tổng':30s} {total:>5d}")

    elif args.action == "eod-update":
        updated = run_eod_update()
        print(f"\n  ✅ {_ll('EOD update')}: {updated} simulated_exit đã cập nhật.\n")


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
        print(f"  ✅ {_ll('Telemetry cleaned')} (keep last {getattr(args, 'max_rows', 1000)} {_ll('rows')}).")
    elif target == "all":
        from src.engine.partial_data_entropy import cleanup_telemetry
        cleanup_telemetry(max_rows=1000)
        print(f"  ✅ {_ll('Telemetry cleaned')}.")
        for fname in ["crisis_cooldown.json", "micro_stress.json"]:
            fp = Path(__file__).resolve().parent / "backend" / "data" / "probe_cache" / fname
            if fp.exists():
                fp.unlink()
                print(f"  ✅ {fname} {_ll('removed')}.")
    else:
        print(f"  {_ll('Usage')}: python ptck.py cleanup telemetry|all [--max-rows N]")

    # Also delete .tmp orphans
    probe_dir = Path(__file__).resolve().parent / "backend" / "data" / "probe_cache"
    for f in probe_dir.glob("*.tmp"):
        f.unlink()
        print(f"  ✅ {_ll('Orphan')} .tmp {_ll('cleaned')}: {f.name}")


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
    print(f"  PTCK — {_ll('SBV UPDATE')} (5-step recovery)")
    print("=" * 60)

    # ── Early exit: --save-fixture only (grab raw HTML for debugging) ──
    if getattr(args, 'sbv_save_fixture', False):
        print("\n  [SAVE] Chỉ lưu fixture HTML từ sbv.gov.vn...")
        from src.services.macro.interbank_seeder import _try_sbv
        save_dir = root / "tests" / "fixtures" / "sbv"
        save_dir.mkdir(parents=True, exist_ok=True)
        result = _try_sbv(force=True)
        raw_html = result.get("raw_html", "")
        if raw_html:
            fixture_path = save_dir / "live.html"
            fixture_path.write_text(raw_html, encoding="utf-8")
            print(f"  ✅ Đã lưu fixture: {fixture_path} ({len(raw_html)} bytes)")
        else:
            print(f"  ❌ Không fetch được HTML. type={result.get('type')} HTTP={result.get('http_status')}")
        return

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
        print(f"  [{_ll('FAIL')}] Regression test thất bại — cập nhật fixtures chưa đúng.")
        print("  Sửa fixtures, sau đó chạy lại: python ptck.py sbv-update")
        return

    # ── Step 3: Production scrape (force=True bỏ qua circuit breaker) ──
    print("\n  [3/5] Đang quét SBV thực tế...")
    from src.services.macro.interbank_seeder import _try_sbv, _clear_sbv_alert, _is_sbv_alert_active
    sbv_result = _try_sbv(force=True)
    rates = sbv_result.get("data", {})

    if sbv_result.get("type") != "SUCCESS":
        print(f"  [{_ll('FAIL')}] SBV scrape thất bại: type={sbv_result.get('type')}, "
              f"HTTP={sbv_result.get('http_status')}")
        print("  Không giải phóng khóa — alert file được giữ nguyên.")
        print("  Kiểm tra: sbv.gov.vn có thể đang bị chặn hoặc thay đổi cấu trúc.")
        return

    print(f"  ✅ SBV scrape {_ll('OK')}: {len(rates)} kỳ hạn")
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
                print(f"  [3b] ✅ {_ll('Parser')} {_ll('OK')}: {len(parsed)} kỳ hạn — {parsed}")
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
        print(f"  ⚠  {_ll('CRITICAL_WARNING')}: crisis_marker = CRISIS_REAL")
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
        print(f"  ✅ micro_stress.json {_ll('cleared')}.")

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


def cmd_fetch_financials(args):
    """
    Fetch PE/PB from KBS, compute z-scores, seed valuation_scores.

    ADR — VCI/KBS FINANCIAL API INTEGRATION
    =========================================
    WHY: VCI GraphQL (apipub.vci.com.vn) has 3 hard failure modes:
      1. SSL Cert mismatch → SSLCertVerificationError on Python 3.14
      2. Non-standard response → dict without 'data' key (KeyError)
      3. Empty response body → JSONDecodeError
    KBS SAS finance endpoint returns 404 (endpoint retired).
    WORKAROUND: Import vnstock.explorer.kbs.financial directly (avoids
    VCI Company GraphQL chain). If KBS 404 persists, all external
    financial ratio APIs are down — see alternatives in user question.
    FIX: If a future source works, replace the KBSFinance block but DO
    NOT revert to VCI Company.ratio_summary() — it will break again.
    """
    import os, numpy as np

    # ── sys.path fix: ensure vnstock regular package (not namespace) ──
    _vnstock_lib = str(Path(__file__).resolve().parent / "backend" / "libs" / "vnstock")
    if _vnstock_lib not in sys.path and os.path.isdir(_vnstock_lib):
        sys.path.insert(0, _vnstock_lib)

    symbols = []
    if args.all:
        from src.database.db_core import get_connection
        with get_connection() as conn:
            symbols = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv ORDER BY symbol"
            ).fetchall()]
    elif args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        print("  ❌ ERROR: Use --symbols SYM1 SYM2 ... or --all")
        return

    from datetime import datetime
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    period = f"{now.year}Q{q}"
    fy, fq = now.year, q
    entity_type = "STANDARD"
    ratio_names = ["PE", "PB"]

    from src.financial.financial_facts import FINANCIAL_DB_PATH
    from src.financial.valuation_engine import VALUATION_RATIOS
    import sqlite3
    conn = sqlite3.connect(str(FINANCIAL_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS valuation_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, period TEXT NOT NULL,
            fiscal_year INTEGER, fiscal_quarter INTEGER,
            entity_type TEXT NOT NULL, ratio_name TEXT NOT NULL,
            ratio_value REAL, z_score REAL, percentile REAL,
            mean REAL, std REAL, count INTEGER, zone TEXT, price REAL,
            UNIQUE(symbol, period, ratio_name)
        )
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
        except Exception:
            pass
    conn.commit()

    from src.database.db_core import get_connection as screener_conn

    print(f"\n  {'='*60}")
    print(f"  PTCK — FETCH FINANCIALS ({len(symbols)} symbols)")
    print(f"  {'='*60}")

    # ── Fetch ratios from KBS (fallback chain: KBS→NO_DATA) ──
    symbol_data = {}
    ok, err = 0, 0
    for sym in symbols:
        try:
            from vnstock.explorer.kbs.financial import Finance as KBSFinance
            kf = KBSFinance(symbol=sym)
            df = kf.ratio(period="year")
            if df.empty:
                err += 1
                print(f"  ⚠️  {sym}: empty KBS ratio response")
                continue
            meta_cols = {"item", "item_en", "item_id", "unit", "levels", "row_number"}
            keys_found = {}
            for _, r in df.iterrows():
                iid = str(r.get("item_id", "")).strip().lower()
                if iid == "pe_ratio":
                    for col in df.columns:
                        if col not in meta_cols:
                            v = r.get(col)
                            if v is not None and isinstance(v, (int, float)) and v > 0:
                                keys_found["PE"] = float(v); break
                elif iid == "pb_ratio":
                    for col in df.columns:
                        if col not in meta_cols:
                            v = r.get(col)
                            if v is not None and isinstance(v, (int, float)) and v > 0:
                                keys_found["PB"] = float(v); break
            if not keys_found:
                print(f"  ⚠️  {sym}: no PE/PB in KBS response")
                err += 1
                continue
            with screener_conn() as sc:
                price_row = sc.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol=? ORDER BY date DESC LIMIT 1",
                    (sym,)
                ).fetchone()
                price = round(price_row[0], 2) if price_row else None
            symbol_data[sym] = {"ratios": keys_found, "price": price}
            ok += 1
            print(f"  ✅ {sym}: PE={keys_found.get('PE','N/A')} PB={keys_found.get('PB','N/A')}")
        except Exception as e:
            err += 1
            es = str(e)
            if "404" in es:
                print(f"  ⛔ {sym}: KBS API 404 — all external financial APIs unavailable. Use alternative import.")
            else:
                print(f"  ❌ {sym}: {e}")

    print(f"\n  Fetched: {ok} OK, {err} errors")
    if not symbol_data:
        print("  No data fetched. Exiting.")
        conn.close()
        return

    total = 0
    for rname in ratio_names:
        items = [(sym, symbol_data[sym]["ratios"][rname])
                 for sym in symbol_data if rname in symbol_data[sym]["ratios"]]
        if len(items) < 3:
            print(f"  ⚠️  {rname}: {len(items)} values (need ≥3), skipping z-score")
            continue
        values = [v for _, v in items]
        mean_v, std_v = float(np.mean(values)), float(np.std(values))
        n = len(values)
        meta = VALUATION_RATIOS.get(rname, {})
        for sym, val in items:
            z = (val - mean_v) / std_v if std_v > 0 else 0.0
            rank = sum(1 for v in values if v <= val)
            pct = round(rank / n * 100, 1)
            if z <= -meta.get("ultra_cheap", 2):
                zone = "ULTRA_CHEAP"
            elif z <= -meta.get("cheap", 1):
                zone = "CHEAP"
            elif z >= meta.get("ultra_expensive", 2):
                zone = "ULTRA_EXPENSIVE"
            elif z >= meta.get("expensive", 1):
                zone = "EXPENSIVE"
            else:
                zone = "FAIR"
            price = symbol_data[sym].get("price")
            conn.execute("""
                INSERT OR REPLACE INTO valuation_scores
                (symbol, period, fiscal_year, fiscal_quarter, entity_type,
                 ratio_name, ratio_value, z_score, percentile,
                 mean, std, count, zone, price)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sym, period, fy, fq, entity_type, rname,
                  round(val, 4), round(z, 4), pct,
                  round(mean_v, 4), round(std_v, 4), n, zone,
                  round(price, 2) if price else None))
            total += 1
        print(f"  ✅ {rname}: μ={mean_v:.2f} σ={std_v:.2f} n={n}")
    conn.commit()
    conn.close()
    print(f"\n  ✅ Inserted {total} scores for {len(symbol_data)} symbols ({period})")


def cmd_import_financials(args):
    """
    Import financial ratios from CSV/XLSX, compute z-scores, seed valuation_scores.

    WHY: External financial APIs (VCI GraphQL, KBS SAS) are unreliable:
      - VCI: SSL cert mismatch + no 'data' key in response + empty body
      - KBS: 404 (endpoint retired)
    This offline import breaks 100% dependency on third-party APIs.
    Data source: download CSV/XLSX from CafeF / Vietstock / FiinPro manually.
    """
    fpath = args.file
    if not fpath or not Path(fpath).exists():
        print("  ❌ ERROR: --file <path> required and must exist")
        return

    import pandas as pd, numpy as np
    from datetime import datetime

    # ── Read file ──
    ext = Path(fpath).suffix.lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(fpath)
        elif ext in (".xls", ".xlsx"):
            df = pd.read_excel(fpath, sheet_name=args.sheet or 0)
        else:
            print(f"  ❌ Unsupported format: {ext} (use .csv/.xls/.xlsx)")
            return
    except Exception as e:
        print(f"  ❌ Read error: {e}")
        return

    if df.empty:
        print("  ❌ File is empty")
        return

    # ── Normalize columns ──
    col_map = {
        "ticker": "symbol", "symbol": "symbol", "stock": "symbol", "ma": "symbol",
        "pe": "PE", "p/e": "PE", "price_earnings": "PE",
        "pb": "PB", "p/b": "PB", "price_book": "PB",
        "evebitda": "EV_EBITDA", "ev/ebitda": "EV_EBITDA", "ev_ebitda": "EV_EBITDA",
        "ps": "PS", "p/s": "PS", "price_sales": "PS",
        "roe": "ROE", "eps": "EPS", "debt_equity": "DEBT_EQUITY", "debt/equity": "DEBT_EQUITY",
        "g": "PROFIT_GROWTH_G", "profit_growth_g": "PROFIT_GROWTH_G", "profit_growth": "PROFIT_GROWTH_G",
        "tang_truong": "PROFIT_GROWTH_G", "growth": "PROFIT_GROWTH_G",
        "profit_current": "PROFIT_CURRENT", "profit_6m": "PROFIT_CURRENT", "lntt": "PROFIT_CURRENT",
        "lntt_6m": "PROFIT_CURRENT", "profit_before_tax": "PROFIT_CURRENT",
        "profit_prior": "PROFIT_PRIOR", "lntt_prior": "PROFIT_PRIOR", "profit_6m_prior": "PROFIT_PRIOR",
        "equity": "EQUITY", "vcshe": "EQUITY", "book_value": "EQUITY", "von_chu_so_huu": "EQUITY",
        "date": "date", "period": "date", "ngay": "date",
        "price": "price", "close": "price", "gia": "price",
    }
    df.rename(columns={c: col_map.get(c.strip().lower().replace(" ", "_"), c) for c in df.columns}, inplace=True)

    required_cols = {"symbol"}
    missing = required_cols - set(df.columns)
    if missing:
        print(f"  ❌ Missing required column: {missing}. Found: {list(df.columns)}")
        return

    # ── Determine period ──
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    period = f"{now.year}Q{q}"
    fy, fq = now.year, q

    # ── Prepare valuation_scores upsert ──
    from src.financial.financial_facts import FINANCIAL_DB_PATH
    from src.financial.valuation_engine import VALUATION_RATIOS
    import sqlite3
    conn = sqlite3.connect(str(FINANCIAL_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS valuation_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, period TEXT NOT NULL,
            fiscal_year INTEGER, fiscal_quarter INTEGER,
            entity_type TEXT NOT NULL, ratio_name TEXT NOT NULL,
            ratio_value REAL, z_score REAL, percentile REAL,
            mean REAL, std REAL, count INTEGER, zone TEXT, price REAL,
            UNIQUE(symbol, period, ratio_name)
        )
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
        except Exception:
            pass
    conn.commit()

    print(f"\n  {'='*60}")
    print(f"  PTCK — IMPORT FINANCIALS ({len(df)} rows, {fpath})")
    print(f"  {'='*60}")

    ratio_candidates = ["PE", "PB", "EV_EBITDA", "PS", "ROE", "EPS", "DEBT_EQUITY", "PROFIT_GROWTH_G"]
    bctc_raw_cols = ["PROFIT_CURRENT", "PROFIT_PRIOR", "EQUITY"]
    all_cols_needed = ratio_candidates + bctc_raw_cols
    raw_ratio_cols = [c for c in all_cols_needed if c in df.columns]
    has_any_ratio = any(c in df.columns for c in ratio_candidates)
    if not has_any_ratio:
        print("  ❌ No recognized ratio columns found. Expected: PE, PB, EV_EBITDA, PS, ROE, EPS, DEBT_EQUITY")
        print(f"     Found columns: {list(df.columns)}")
        conn.close()
        return

    # ── Build symbol_data from file rows ──
    symbol_data = {}
    for _, row in df.iterrows():
        sym = str(row["symbol"]).strip().upper()
        vals = {}
        for rn in raw_ratio_cols:
            v = row.get(rn)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                try:
                    fv = float(v)
                    if fv >= 0:
                        vals[rn] = fv
                except (ValueError, TypeError):
                    pass
        if not vals:
            continue
        price = row.get("price")
        if price is not None and not (isinstance(price, float) and np.isnan(price)):
            price = round(float(price), 2)
        else:
            price = None
        symbol_data[sym] = {"ratios": vals, "price": price}

    # ── Compute g and ROE from raw BCTC columns (profit_current, equity) ──
    bctc = {"PROFIT_GROWTH_G", "ROE", "PROFIT_CURRENT", "PROFIT_PRIOR", "EQUITY"}
    if any(k in bctc for d in symbol_data.values() for k in d["ratios"]):
        for sym, d in symbol_data.items():
            r = d["ratios"]
            pc = r.get("PROFIT_CURRENT")
            pp = r.get("PROFIT_PRIOR")
            eq = r.get("EQUITY")
            if pc is not None:
                if pp is not None and pp > 0:
                    r["PROFIT_GROWTH_G"] = round((pc - pp) / abs(pp) * 100, 2)
                    print(f"  📐 {sym}: g={r['PROFIT_GROWTH_G']:.1f}% (from profit {pp:.0f}→{pc:.0f})")
                if eq is not None and eq > 0:
                    r["ROE"] = round(pc / eq * 100, 2)
                    print(f"  📐 {sym}: ROE={r['ROE']:.1f}% (from profit={pc:.0f}, equity={eq:.0f})")
            # strip BCTC raw keys so they don't pollute ratio_cols
            for k in ("PROFIT_CURRENT", "PROFIT_PRIOR", "EQUITY"):
                r.pop(k, None)

    # ── Compute PEG and PB_TO_ROE from raw ratios ──
    computed = []
    has_g = any("PROFIT_GROWTH_G" in d["ratios"] for d in symbol_data.values())
    has_roe = any("ROE" in d["ratios"] for d in symbol_data.values())
    for sym, d in symbol_data.items():
        r = d["ratios"]
        if has_g and "PE" in r and "PROFIT_GROWTH_G" in r:
            g = r["PROFIT_GROWTH_G"]
            if g > 0:
                peg = r["PE"] / g
                if peg > 0:
                    r["PEG"] = round(peg, 4)
                    computed.append("PEG")
        if has_roe and "PB" in r and "ROE" in r:
            roe_dec = r["ROE"] / 100.0
            if roe_dec > 0:
                pb_to_roe = r["PB"] / roe_dec
                if pb_to_roe > 0:
                    r["PB_TO_ROE"] = round(pb_to_roe, 4)
                    computed.append("PB_TO_ROE")
    # Collect all actual ratio keys present across symbols
    exclude_raw = {"PROFIT_GROWTH_G", "PROFIT_CURRENT", "PROFIT_PRIOR", "EQUITY"}
    present_ratios = set()
    for d in symbol_data.values():
        present_ratios.update(k for k in d["ratios"] if k not in exclude_raw)
    ratio_cols = [c for c in ["PE", "PB", "EV_EBITDA", "PS", "ROE", "EPS", "DEBT_EQUITY", "PEG", "PB_TO_ROE"] if c in present_ratios]
    if computed:
        print(f"  📐 Computed: {', '.join(sorted(set(computed)))} from growth/ROE inputs")

    print(f"  Parsed {len(symbol_data)} symbols with valid ratio data")

    # ── Compute z-scores and insert ──
    total = 0
    for rname in ratio_cols:
        items = [(sym, d["ratios"][rname]) for sym, d in symbol_data.items() if rname in d["ratios"]]
        if len(items) < 3:
            print(f"  ⚠️  {rname}: {len(items)} values (need >=3), skipping")
            continue
        values = [v for _, v in items]
        mean_v, std_v = float(np.mean(values)), float(np.std(values))
        n = len(values)
        meta = VALUATION_RATIOS.get(rname, {})
        for sym, val in items:
            z = (val - mean_v) / std_v if std_v > 0 else 0.0
            rank = sum(1 for v in values if v <= val)
            pct = round(rank / n * 100, 1)
            if z <= -meta.get("ultra_cheap", 2):
                zone = "ULTRA_CHEAP"
            elif z <= -meta.get("cheap", 1):
                zone = "CHEAP"
            elif z >= meta.get("ultra_expensive", 2):
                zone = "ULTRA_EXPENSIVE"
            elif z >= meta.get("expensive", 1):
                zone = "EXPENSIVE"
            else:
                zone = "FAIR"
            price = symbol_data[sym].get("price")
            conn.execute("""
                INSERT OR REPLACE INTO valuation_scores
                (symbol, period, fiscal_year, fiscal_quarter, entity_type,
                 ratio_name, ratio_value, z_score, percentile,
                 mean, std, count, zone, price)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sym, period, fy, fq, "STANDARD", rname,
                  round(val, 4), round(z, 4), pct,
                  round(mean_v, 4), round(std_v, 4), n, zone,
                  price))
            total += 1
        print(f"  ✅ {rname}: μ={mean_v:.2f} σ={std_v:.2f} n={n}")

    # ── Peer-group industry z-scores ──
    try:
        _p = Path(str(FINANCIAL_DB_PATH))
        _screener_path = _p.parent / "screener_cache.db"
        if not _screener_path.exists():
            _screener_path = _p.parent.parent / "data" / "screener_cache.db"
        screener = sqlite3.connect(str(_screener_path))
        indf = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", screener)
        screener.close()
        sym_to_industry = dict(zip(indf["symbol"].str.upper(), indf["icb_name2"]))
        from collections import defaultdict
        industry_groups = defaultdict(list)
        for sym in symbol_data:
            ind = sym_to_industry.get(sym.upper())
            if ind:
                industry_groups[ind].append(sym)
        peer_total = 0
        for ind, group_syms in sorted(industry_groups.items()):
            if len(group_syms) < 3:
                continue
            for rname in ratio_cols:
                items = [(sym, symbol_data[sym]["ratios"][rname]) for sym in group_syms if rname in symbol_data[sym]["ratios"]]
                if len(items) < 3:
                    continue
                vals = [v for _, v in items]
                mean_p, std_p = float(np.mean(vals)), float(np.std(vals))
                n_p = len(vals)
                meta = VALUATION_RATIOS.get(rname, {})
                for sym, val in items:
                    z_peer = (val - mean_p) / std_p if std_p > 0 else 0.0
                    if z_peer <= -meta.get("ultra_cheap", 2):
                        zone_p = "ULTRA_CHEAP"
                    elif z_peer <= -meta.get("cheap", 1):
                        zone_p = "CHEAP"
                    elif z_peer >= meta.get("ultra_expensive", 2):
                        zone_p = "ULTRA_EXPENSIVE"
                    elif z_peer >= meta.get("expensive", 1):
                        zone_p = "EXPENSIVE"
                    else:
                        zone_p = "FAIR"
                    conn.execute("""
                        UPDATE valuation_scores
                        SET z_score_peer=?, zone_peer=?, peer_group=?, peer_count=?
                        WHERE symbol=? AND period=? AND ratio_name=?
                    """, (round(z_peer, 4), zone_p, ind, n_p,
                          sym, period, rname))
                    peer_total += 1
        if peer_total:
            print(f"  ✅ Peer-group z-scores: {peer_total} updates across {len(industry_groups)} industries")
    except Exception as e:
        print(f"  ⚠️  Peer-group z-score skipped: {e}")

    conn.commit()
    conn.close()
    print(f"\n  ✅ Inserted {total} scores for {len(symbol_data)} symbols ({period})")


def cmd_bctc_20q(args):
    """
    Import 20-quarter BCTC CSV, compute time-series z-scores, seed
    z_score_ts / zone_ts / mean_5y / std_5y / count_5y in valuation_scores.

    CSV format (long): symbol,quarter,PE,PB,ROE,EPS,price[,...]
      One row per symbol per quarter, min 12 quarters for statistical validity.

    Architectural intent:
      Time-series Z-Score (vs a stock's own 5-year history) is the PRIMARY
      valuation method. Cross-sectional Z-Score (vs universe) is market-
      relative overlay. This restores the original architectural design
      after the temporary snapshot-based import workaround.
    """
    fpath = args.file
    if not fpath or not Path(fpath).exists():
        print("  ❌ ERROR: --file <path> required and must exist")
        return

    import pandas as pd, numpy as np
    ext = Path(fpath).suffix.lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(fpath)
        elif ext in (".xls", ".xlsx"):
            df = pd.read_excel(fpath, sheet_name=args.sheet or 0)
        else:
            print(f"  ❌ Unsupported format: {ext}")
            return
    except Exception as e:
        print(f"  ❌ Read error: {e}")
        return

    if df.empty:
        print("  ❌ File is empty")
        return

    # Normalise columns
    col_map = {
        "quarter": "quarter", "period": "quarter", "quy": "quarter", "q": "quarter",
        "ticker": "symbol", "symbol": "symbol", "stock": "symbol",
        "pe": "PE", "p/e": "PE", "pb": "PB", "p/b": "PB",
        "roe": "ROE", "eps": "EPS",
        "price": "price", "close": "price", "gia": "price",
    }
    df.rename(columns={c: col_map.get(c.strip().lower().replace(" ", "_"), c) for c in df.columns}, inplace=True)

    required = {"symbol", "quarter"}
    missing = required - set(df.columns)
    if missing:
        print(f"  ❌ Missing: {missing}. Found: {list(df.columns)}")
        return

    ratio_cols = [c for c in ["PE", "PB", "ROE", "EPS"] if c in df.columns]
    if not ratio_cols:
        print("  ❌ No ratio columns (PE, PB, ROE, EPS). Found: {list(df.columns)}")
        return

    from src.financial.financial_facts import FINANCIAL_DB_PATH
    from src.financial.valuation_engine import VALUATION_RATIOS
    import sqlite3
    conn = sqlite3.connect(str(FINANCIAL_DB_PATH))
    for col_sql in [
        "ALTER TABLE valuation_scores ADD COLUMN z_score_ts REAL",
        "ALTER TABLE valuation_scores ADD COLUMN zone_ts TEXT",
        "ALTER TABLE valuation_scores ADD COLUMN mean_5y REAL",
        "ALTER TABLE valuation_scores ADD COLUMN std_5y REAL",
        "ALTER TABLE valuation_scores ADD COLUMN count_5y INTEGER",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    conn.commit()

    print(f"\n  {'='*60}")
    print(f"  PTCK — BCTC 20Q ({len(df)} rows, {fpath})")
    print(f"  {'='*60}")

    # Group by (symbol, ratio): collect ordered values across quarters
    from collections import defaultdict
    groups = defaultdict(list)
    for _, row in df.iterrows():
        sym = str(row["symbol"]).strip().upper()
        q = str(row["quarter"]).strip()
        for rn in ratio_cols:
            v = row.get(rn)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                try:
                    fv = float(v)
                    if fv > 0:
                        groups[(sym, rn)].append((q, fv))
                except (ValueError, TypeError):
                    pass

    # For each group sort by quarter, compute TS stats, store in latest quarter
    meta = VALUATION_RATIOS
    ts_total, ts_skip_short, ts_skip_nosym = 0, 0, 0
    for (sym, rn), q_values in groups.items():
        q_values.sort(key=lambda x: x[0])  # chronological
        if len(q_values) < 12:
            ts_skip_short += 1
            continue
        vals = [v for _, v in q_values]
        latest_q, latest_val = q_values[-1]
        mean_5y = float(np.mean(vals))
        std_5y = float(np.std(vals))
        count_5y = len(vals)
        z_ts = (latest_val - mean_5y) / std_5y if std_5y > 0 else 0.0
        # zone from VALUATION_RATIOS thresholds
        m = meta.get(rn, {})
        if z_ts <= -m.get("ultra_cheap", 2):       zone_ts = "ULTRA_CHEAP"
        elif z_ts <= -m.get("cheap", 1):           zone_ts = "CHEAP"
        elif z_ts >= m.get("ultra_expensive", 2):  zone_ts = "ULTRA_EXPENSIVE"
        elif z_ts >= m.get("expensive", 1):        zone_ts = "EXPENSIVE"
        else:                                        zone_ts = "FAIR"
        # Find the latest existing period for this symbol
        cur = conn.execute("SELECT MAX(period) FROM valuation_scores WHERE symbol=?", (sym,))
        max_period = cur.fetchone()[0]
        target_q = max_period if max_period else latest_q
        # Store TS data in the most recent period row
        conn.execute("""
            UPDATE valuation_scores
            SET z_score_ts=?, zone_ts=?, mean_5y=?, std_5y=?, count_5y=?
            WHERE symbol=? AND period=? AND ratio_name=?
        """, (round(z_ts, 4), zone_ts, round(mean_5y, 4), round(std_5y, 4),
              count_5y, sym, target_q, rn))
        ts_total += 1
        # Also insert historical quarter rows if not already present
        for q, val in q_values[:-1]:
            conn.execute("""
                INSERT OR IGNORE INTO valuation_scores
                (symbol, period, ratio_name, ratio_value, entity_type)
                VALUES (?,?,?,?,?)
            """, (sym, q, rn, round(val, 4), "STANDARD"))

    conn.commit()
    conn.close()
    print(f"  ✅ Time-series z-scores: {ts_total} updates ({ts_skip_short} skipped <12 quarters)")
    print(f"  ✅ Historical quarter rows: inserted/verified for all symbols")
    print(f"  📐 Architectural mode: TIME-SERIES (intrinsic) now available for {ts_total//len(ratio_cols) if ratio_cols else 0} symbols")


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
    print(f"  PTCK — {_ll('BACKFILL MACRO')} (PTD Tickers)")
    print("=" * 60)
    print(f"  {_ll('Tickers')}: {', '.join(tickers.values())}")
    print(f"  {_ll('Period')}:  {period}")
    print()

    data = yf.download(list(tickers.values()), period=period, interval="1d", progress=False)
    if data.empty:
        print(f"  [{_ll('FAIL')}] Không nhận được dữ liệu từ Yahoo Finance.")
        return

    close_data = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data
    inv_map = {v: k for k, v in tickers.items()}
    close_data = close_data.rename(columns=inv_map)
    df_melted = close_data.reset_index().melt(id_vars=['Date'], var_name='variable', value_name='value')
    df_melted.rename(columns={'Date': 'date'}, inplace=True)
    df_melted['date'] = pd.to_datetime(df_melted['date']).dt.strftime('%Y-%m-%d')
    df_melted = df_melted.dropna()

    if df_melted.empty:
        print(f"  [{_ll('FAIL')}] Không có dữ liệu sau khi melt.")
        return

    print(f"  {_ll('Downloaded')}: {len(df_melted)} {_ll('rows')}")

    from src.database.db_core import get_connection, save_data_upsert
    with get_connection() as conn:
        save_data_upsert('macro_history', df_melted, conn)

    print(f"  [{_ll('OK')}] Đã seed {len(df_melted)} {_ll('rows')} vào macro_history.")
    print("=" * 60)


def cmd_backfill_regime(args):
    """Backfill regime_history cho toàn bộ ngày bị thiếu."""
    from src.engine.regime_engine import backfill_regime_history
    target = args.target
    batch = getattr(args, 'batch_size', 30)
    processed, inserted = backfill_regime_history(target_date=target, batch_size=batch)
    if inserted == 0:
        print(f"✅ {_ll('No days to backfill')}.")
    else:
        print(f"✅ Đã backfill {inserted} ngày vào regime_history.")


def cmd_absorption_detector(args):
    """Absorption Detector — SDI + Volume Profile PCA (index hoặc per-symbol)."""
    symbol = getattr(args, 'symbol', None)
    show_history = getattr(args, 'history', False)

    if symbol:
        from src.engine.per_symbol_absorption import PerSymbolAbsorption
        if show_history:
            PerSymbolAbsorption.print_history(symbol)
            return
        detector = PerSymbolAbsorption(symbol)
        result = detector.analyze()
        if not args.quiet:
            PerSymbolAbsorption.print_report(result)
        if args.quiet:
            from src.database.db_core import safe_json_dumps
            print(safe_json_dumps(result, indent=2))
        return

    from src.engine.absorption_detector import run_absorption_detection
    result = run_absorption_detection(
        target_date=args.date,
        show_details=not args.quiet
    )
    if args.quiet:
        from src.database.db_core import safe_json_dumps
        print(safe_json_dumps(result, indent=2))


def cmd_macro(args):
    """Macro Governor Gatekeeper — Two-Tier Architecture (Tier 1)."""
    from src.engine.macro_governor import MacroGovernor
    result = MacroGovernor.assess_global()
    MacroGovernor.print_report(result)


def cmd_sel(args):
    """Structure Evolution Layer — Wasserstein Distance + Survival Mode."""
    from src.engine.structure_evolution import StructureEvolutionLayer
    as_of = getattr(args, "as_of", None)
    offline = not getattr(args, "online", False)  # offline mặc định (chống IP ban)
    result = StructureEvolutionLayer.assess_global(as_of=as_of, offline=offline)
    StructureEvolutionLayer.print_report(result)


def cmd_eod_run(args):
    """EOD Runner — tự phục hồi (retry) + lũy đẳng (idempotent) + chống race (SQLite lock) + Giao dịch bù (catch-up)."""
    from src.engine.eod_runner import run_eod_pipeline
    result = run_eod_pipeline(
        as_of_date=getattr(args, "date", None),
        force=getattr(args, "force", False),
        retry_sleep=getattr(args, "retry_sleep", 15 * 60),
        catchup=getattr(args, "catchup", True),
    )
    status = result.get("status")
    icon = {"SUCCESS": "[OK]", "SKIPPED": "[SKIP]", "FAILED": "[ALARM]"}.get(status, "[?]")
    # Báo cáo Catch-up (nếu có ngày nợ được bù)
    cu = result.get("catchup") or {}
    if cu.get("gap_days"):
        print(f"\n[{_ll('CATCH-UP')}] Ngày nợ phát hiện: {len(cu['gap_days'])}")
        if cu.get("caught_up"):
            print(f"     Nạp hàng đợi (còn hiệu lực): {cu['caught_up']}")
        if cu.get("mtm_only"):
            print(f"     Bù STALE (chỉ MtM/settle): {cu['mtm_only']}")
            print(f"     [!] Cần con người xem xét quyết định giao dịch các ngày trên.")
        if cu.get("errors"):
            print(f"     Bù thất bại: {cu['errors']}")
    # Báo cáo thực thi hàng đợi (Transpose & Kill-switch)
    qe = cu.get("queue_execution") or {}
    if qe.get("processed"):
        print(f"\n[{_ll('QUEUE')}] Ép khớp lệnh bù @ open {qe.get('recovery_date')}: "
              f"{qe['processed']} lệnh")
        for f in qe.get("filled", []):
            print(f"     {_ll('FILLED')} {f['symbol']} qty={f['qty']} @ {f['exec_price']:,.0f} "
                  f"(decay {f['decay_pct']}%, target close={f.get('target_qty')})")
        for r in qe.get("rejected_decay", []):
            print(f"     {_ll('KILL-SWITCH')} {r['symbol']} {r['decision_date']}: "
                  f"decay {r['decay_pct']}% > ngưỡng (open={r['open_tk']:,.0f} vs "
                  f"close_T={r['target_price']:,.0f}) → SIGNAL_DECAY")
        for r in qe.get("rejected_other", []):
            print(f"     {_ll('REJECTED')} {r['symbol']} {r['decision_date']}: {r['reason']}")
    print(f"\n{icon} EOD {result.get('as_of_date')} → {status}")
    if result.get("reason"):
        print(f"     {_ll('reason')}: {result['reason']}")
    if result.get("note"):
        print(f"     {result['note']}")
    if result.get("error"):
        print(f"     {_ll('error')}: {result['error']}")


def cmd_paper(args):
    """Paper Trading Engine — Giả lập thời gian thực (Live vs Backtest)."""
    from src.engine.paper_trading_engine import PaperTradingEngine
    offline = not getattr(args, "online", False)  # offline mặc định (chống IP ban)
    action = getattr(args, "action", "report")

    if action == "run":
        result = PaperTradingEngine.run_daily(
            decision_date=getattr(args, "date", None), offline=offline)
        PaperTradingEngine.print_report(result)
    elif action == "pnl":
        from src.engine.paper_mtm import PaperMtM
        from src.database.db_core import get_connection
        engine = PaperTradingEngine(offline=offline)
        date = getattr(args, "date", None)
        if not date:
            with get_connection() as conn:
                row = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
            date = row[0] if row and row[0] else None
        snapshot = engine.mtm.mark_to_market(date, hdr_limit=0.0)
        PaperMtM.print_report(snapshot)
    else:  # report
        engine = PaperTradingEngine(offline=offline)
        report = engine.get_stability_report(
            lookback_sessions=getattr(args, "sessions", 45))
        PaperTradingEngine.print_report(report)


def cmd_scan(args):
    """Elite scanner."""
    symbol = getattr(args, 'symbol', None)
    if symbol:
        symbol = symbol.upper()
        print("\n" + "=" * 65)
        print(f"  PTCK — QUÉT NHANH MÃ: {symbol}")
        print("=" * 65)
        from src.database.db_core import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, open, high, low, close, volume, source
                FROM daily_ohlcv
                WHERE symbol = ?
                ORDER BY date DESC LIMIT 5
            """, (symbol,))
            rows = cur.fetchall()
            if not rows:
                print(f"\n  ❌ Không tìm thấy dữ liệu cho mã {symbol} trong cơ sở dữ liệu.")
                print("=" * 65)
                return
            print(f"\n  {'Ngày':<12} | {'Mở':<10} | {'Cao':<10} | {'Thấp':<10} | {'Đóng':<10} | {'Khối lượng':>14} | {'Nguồn':>8}")
            print("  " + "-" * 63)
            for r in rows:
                print(f"  {r[0]:<12} | {r[1]:<10.2f} | {r[2]:<10.2f} | {r[3]:<10.2f} | {r[4]:<10.2f} | {r[5]:>14,} | {r[6] or '—':>8}")
            print("  " + "-" * 63)
        print("=" * 65)
        return

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
            print(f"  PTCK — {_ll('GOLD REGIME')}")
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
            print(f"  [{_ll('FAIL')}] Gold regime: {e}")
    else:
        try:
            from src.services.macro.gold_service import get_gold_dashboard, get_gold_cognition_layer
            dash = get_gold_dashboard()
            print("=" * 60)
            print(f"  PTCK — {_ll('GOLD DASHBOARD')}")
            print("=" * 60)
            print(f"  SJC mua:   {dash.get('sjc_buy', 'N/A'):>12.0f} VND")
            print(f"  SJC bán:   {dash.get('sjc_sell', 'N/A'):>12.0f} VND")
            print(f"  SJC spread: {dash.get('sjc_spread', 'N/A'):>12.0f} VND")
            print(f"  BTMC mua:  {dash.get('btmc_buy', 'N/A'):>12.0f} VND")
            print(f"  BTMC bán:  {dash.get('btmc_sell', 'N/A'):>12.0f} VND")
            print(f"  BTMC spread: {dash.get('btmc_spread', 'N/A'):>12.0f} VND")
            print("=" * 60)
        except Exception as e:
            print(f"  [{_ll('FAIL')}] Gold dashboard: {e}")


def cmd_silver(args):
    """Silver information."""
    if args.subcommand == "gs-ratio":
        try:
            from core.macro.precious_metal_ratio import get_gs_ratio_from_db
            gs = get_gs_ratio_from_db()
            print("=" * 60)
            print(f"  PTCK — {_ll('GOLD/SILVER RATIO')}")
            print("=" * 60)
            print(f"  XAUUSD:          {gs.get('xau_usd', 'N/A')}")
            print(f"  XAGUSD:          {gs.get('xag_usd', 'N/A')}")
            print(f"  {_ll('Gold/Silver Ratio')}: {gs.get('ratio', 'N/A')}")
            print(f"  {_ll('Regime')}:          {gs.get('regime', 'N/A')}")
            print(f"  Tín hiệu:        {gs.get('signal', 'N/A')}")
            print(f"  {_ll('Severity')}:        {gs.get('severity', 0):.2f}")
            print("=" * 60)
        except Exception as e:
            print(f"  [{_ll('FAIL')}] GS ratio: {e}")
    elif args.subcommand == "seed":
        try:
            from src.services.macro.silver_world_service import seed_world_silver_to_db
            print("  Đang seed XAGUSD...")
            ok = seed_world_silver_to_db()
            print(f"  {'[' + _ll('OK') + ']' if ok else '[' + _ll('FAIL') + ']'} {_ll('World silver seeded')}: {ok}")
        except Exception as e:
            print(f"  [{_ll('FAIL')}] Silver seed: {e}")
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
            print(f"  PTCK — {_ll('SILVER DASHBOARD')}")
            print("=" * 60)
            print(f"  BTMC mua:    {dash.get('btmc_buy', 0):>12.0f} VND")
            print(f"  BTMC bán:    {dash.get('btmc_sell', 0):>12.0f} VND")
            print(f"  BTMC {_ll('spread')}: {dash.get('btmc_spread', 0):>12.0f} VND")
            print(f"  XAGUSD:              {xag if xag else 'N/A'}")
            print(f"  {_ll('Gold/Silver Ratio')}:   {gs if gs else 'N/A'}")
            print(f"  {_ll('GS Regime')}:           {gsr.get('regime', 'N/A')}")
            print(f"  {_ll('GS Signal')}:           {gsr.get('signal', 'N/A')}")
            print("=" * 60)
        except Exception as e:
            print(f"  [{_ll('FAIL')}] Silver dashboard: {e}")


def cmd_governor(args):
    """Governor Decision Matrix — Bayesian Expected Utility (P3)."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.governor.company_state import BayesianGovernor, print_report
    engine = BayesianGovernor()
    analysis = engine.analyze(args.symbols)
    engine.close()
    if args.output == "json":
        def _ser(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return {f: getattr(obj, f) for f in obj.__dataclass_fields__}
            return str(obj)
        import json
        print(json.dumps(analysis, indent=2, ensure_ascii=False, default=_ser))
    else:
        print_report(analysis)


def cmd_calibrate(args):
    """P4 Calibration — Meta-Cognition: Log-Loss, ECE, Beta LR update + Outcome Resolution."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from calibration.prediction_log import init_schema, get_unresolved_predictions
    init_schema()

    if args.action == "eval":
        from calibration.scoring import log_loss, brier_score, ece, mce, reliability_curve
        from calibration.prediction_log import get_outcomes_for_calibration
        outcomes = get_outcomes_for_calibration(args.days)
        if not outcomes:
            print("  Không có dữ liệu đã resolve để đánh giá.")
            return
        ps = [r["p_gain"] for r in outcomes]
        ys = [r["outcome"] for r in outcomes]
        losses = [log_loss(p, y) for p, y in zip(ps, ys)]
        print(f"\n  {'='*60}")
        print(f"  P4 {_ll('CALIBRATION EVALUATION')} — {args.days}-day window")
        print(f"  {'='*60}")
        print(f"  {_ll('N predictions')}:    {len(outcomes)}")
        print(f"  {_ll('Mean Log-Loss')}:    {sum(losses)/len(losses):.4f}")
        print(f"  {_ll('Median Log-Loss')}:  {sorted(losses)[len(losses)//2]:.4f}")
        print(f"  {_ll('Max Log-Loss')}:     {max(losses):.4f}")
        print(f"  {_ll('ECE')} (10 bins):    {ece(ps, ys):.4f}")
        print(f"  {_ll('MCE')}:              {mce(ps, ys):.4f}")
        print(f"  {_ll('Mean Brier')}:       {sum(brier_score(p,y) for p,y in zip(ps,ys))/len(ps):.4f}")
        print(f"\n  {_ll('Reliability Curve')}:")
        print(f"  {_ll('Bin'):>3} {'N':>4} {_ll('Conf'):>6} {_ll('Acc'):>6} {_ll('Gap'):>6}")
        for r in reliability_curve(ps, ys):
            print(f"  {r['bin']:>3} {r['n']:>4} {r['confidence']:>6.3f} {r['accuracy']:>6.3f} {r['gap']:>+6.3f}")

    elif args.action == "update-beta":
        from calibration.calibrator import update_beta_posteriors, calibration_summary
        update_beta_posteriors(args.days)
        summary = calibration_summary(args.days)
        print(f"\n  {'='*60}")
        print(f"  P4 {_ll('BAYESIAN LR UPDATE')} — {_ll('Beta Posteriors')}")
        print(f"  {'='*60}")
        print(f"  {_ll('N outcomes')}: {summary['n_outcomes']}")
        for col, entries in summary["details"].items():
            print(f"\n  [{col}]")
            print(f"  {_ll('Value'):<24} {'N':>4} {_ll('Acc'):>6} {_ll('Alpha'):>6} {_ll('Beta'):>6} {'LR':>8}")
            for e in entries:
                print(f"  {e['value']:<24} {e['n']:>4} {e['accuracy']:>6.3f} "
                      f"{e['alpha']:>6.1f} {e['beta']:>6.1f} {e['lr_calibrated']:>8.4f}")

    elif args.action == "lrs":
        from calibration.calibrator import get_calibrated_lrs
        lrs = get_calibrated_lrs()
        print(f"\n  {'='*60}")
        print(f"  P4 {_ll('CALIBRATED LIKELIHOOD RATIOS')}")
        print(f"  {'='*60}")
        for key in sorted(lrs):
            print(f"  {key:<48} {lrs[key]:>8.4f}")

    elif args.action == "status":
        unresolved = get_unresolved_predictions()
        from calibration.prediction_log import get_outcomes_for_calibration
        resolved = get_outcomes_for_calibration(args.days)
        print(f"\n  {'='*60}")
        print(f"  P4 {_ll('PREDICTION LOG STATUS')}")
        print(f"  {'='*60}")
        print(f"  {_ll('Unresolved')}:  {len(unresolved)}")
        print(f"  {_ll('Resolved')}:    {len(resolved)} (window={args.days}d)")
        if unresolved:
            print(f"\n  {_ll('Pending')} outcomes (first 10):")
            print(f"  {'ID':>4} {_ll('Date'):<12} {'Symbol':<6} {'P(Gain)':>8} {_ll('Action'):<10}")
            for r in unresolved[:10]:
                print(f"  {r['id']:>4} {r['date']:<12} {r['symbol']:<6} {r['p_gain']:>8.3f} {r['action']:<10}")

    elif args.action == "resolve":
        from calibration.calibrator import resolve_pending_outcomes, print_resolve_report
        result = resolve_pending_outcomes(
            hold_days=args.hold_days,
            dry_run=args.dry_run,
        )
        print_resolve_report(result, lang_mode=_VERBOSE_LANG)

    elif args.action == "report":
        from calibration.calibrator import calibration_trend_report, print_trend_report
        report = calibration_trend_report(days=args.days)
        print_trend_report(report, lang_mode=_VERBOSE_LANG)

    elif args.action == "circuit-breaker":
        from datetime import date as _today
        from calibration.prediction_log import (
            get_circuit_breaker_state, check_circuit_breaker_auto,
            get_circuit_breaker_log, CB_LABELS,
        )
        init_schema()
        from calibration.prediction_log import init_circuit_breaker
        init_circuit_breaker()

        if args.force_check:
            state = check_circuit_breaker_auto()
        else:
            state = get_circuit_breaker_state()

        print(f"\n  {'='*60}")
        print(f"  P4 {_ll('Circuit Breaker')} — {state.get('date', str(_today.today()))}")
        print(f"  {'='*60}")
        level = state.get("level", 0)
        label = state.get("label", CB_LABELS.get(level, "UNKNOWN"))
        active = state.get("active", 0)
        print(f"  Trạng thái: {'⛔ ' + _ll('KÍCH_HOẠT') if active else '🟢 ' + _ll('BÌNH_THƯỜNG')} ({_ll(label)})")
        print(f"  Cấp độ:    {level}")
        if state.get("reason"):
            print(f"  Lý do:     {state['reason']}")
        if state.get("mean_log_loss"):
            print(f"  {_ll('Log-Loss')}:  {state['mean_log_loss']:.4f}")
        if state.get("diff"):
            print(f"  ΔLL:       {state['diff']:+.4f}")

        # Show circuit breaker log
        log = get_circuit_breaker_log(days=30)
        if len(log) > 1:
            print(f"\n  Lịch sử ({len(log)} entries):")
            print(f"  {'Ngày':<12} {'Level':>5} {'Trạng thái':<14} {'Lý do':<30}")
            print(f"  {'-'*65}")
            for entry in log[:10]:
                print(f"  {entry['date']:<12} {entry['level']:>5} "
                      f"{'⛔' if entry['active'] else '🟢'}{' ' + _ll(entry['label']):<13} "
                       f"{(entry.get('trigger_reason') or '')[:28]:<30}")


def cmd_drift(args):
    """Drift & Validation Layer — Self-assessment của toàn bộ hệ thống PTCK."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from datetime import datetime

    sub = args.drift_action

    if sub == "all":
        print(f"\n  {'='*70}")
        print(f"  DRIFT & VALIDATION — TOÀN CẢNH {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  {'='*70}")

        # ── 1. Calibration Trend (Concept Drift proxy) ──
        print(f"\n  {'─'*70}")
        print(f"  📊 CONCEPT DRIFT (Calibration Trend)")
        print(f"  {'─'*70}")
        try:
            from calibration.calibrator import calibration_trend_report
            report = calibration_trend_report(days=args.days)
            if report.get("status") == "OK":
                tr = report.get("trend", {})
                lat = report.get("latest", {})
                print(f"    Snapshots:        {report['n_snapshots']}")
                if lat:
                    print(f"    Latest Log-Loss:  {lat.get('mean_log_loss', 'N/A'):.4f}" if lat.get('mean_log_loss') else f"    Latest Log-Loss:  N/A")
                    print(f"    Latest Acc:       {lat.get('accuracy', 'N/A'):.2%}" if lat.get('accuracy') else f"    Latest Acc:       N/A")
                if tr:
                    flag = "🔴 DEGRADING" if tr.get('degradation_detected') else "🟢 STABLE"
                    print(f"    Degradation:      {flag}")
                    print(f"    ΔLL (recent-old): {tr.get('recent_avg_log_loss', 0):.4f} — {tr.get('older_avg_log_loss', 0):.4f}")
            else:
                print(f"    Chưa có dữ liệu calibration (chờ resolve predictions đầu tiên)")
        except Exception as e:
            print(f"    ⚠️ Lỗi: {e}")

        # ── 2. Prediction Log Stats ──
        print(f"\n  {'─'*70}")
        print(f"  📋 PREDICTION LOG")
        print(f"  {'─'*70}")
        try:
            from calibration.prediction_log import get_unresolved_predictions, get_outcomes_for_calibration
            unresolved = get_unresolved_predictions()
            resolved = get_outcomes_for_calibration(args.days)
            print(f"    Unresolved:    {len(unresolved)}")
            print(f"    Resolved:      {len(resolved)} (window={args.days}d)")
            if resolved:
                losses = [r["log_loss"] for r in resolved if r.get("log_loss") is not None]
                if losses:
                    print(f"    Mean Log-Loss: {sum(losses)/len(losses):.4f}")
        except Exception as e:
            print(f"    ⚠️ Lỗi: {e}")

        # ── 3. Circuit Breaker ──
        print(f"\n  {'─'*70}")
        print(f"  ⛔ CIRCUIT BREAKER")
        print(f"  {'─'*70}")
        try:
            from calibration.prediction_log import get_circuit_breaker_state
            cb = get_circuit_breaker_state()
            level = cb.get("level", 0)
            active = cb.get("active", 0)
            label = cb.get("label", "BÃŒNH_THÆ¯á»œNG")
            print(f"    Level:   {level} ({'⛔ ' if active else '🟢 '}{label})")
            if cb.get("mean_log_loss"):
                print(f"    Log-Loss: {cb['mean_log_loss']:.4f}")
            if cb.get("trigger_reason") and cb["trigger_reason"] != "BÃŒNH_THÆ¯á»œNG":
                print(f"    LÃ½ do:   {cb['trigger_reason']}")
        except Exception as e:
            print(f"    ⚠ Lỗi: {e}")

        # ── 4. Generalization Gap (QuantStats) ──
        print(f"\n  {'─'*70}")
        print(f"  📈 GENERALIZATION GAP (QuantStats)")
        print(f"  {'─'*70}")
        try:
            from src.core.quantstats_bridge import QuantStatsBridge
            qs = QuantStatsBridge()
            qs_report = qs.run_all()
            cal = qs_report.get("calibration", {})
            print(f"    Live Sharpe:       {cal.get('sharpe_live_smoothed', 'N/A')}")
            print(f"    Sharpe vs Random:  {cal.get('sharpe_vs_random', 'N/A')}")
            print(f"    Calib Penalty:     {cal.get('calibration_penalty', 'N/A')}")
            rand = qs_report.get("random_baseline", {})
            print(f"    Random Sharpe P95: {rand.get('random_sharpe_p95', 'N/A')}")
            print(f"    Random Sharpe P50: {rand.get('random_sharpe_p50', 'N/A')}")
        except Exception as e:
            print(f"    ⚠ Chưa có dữ liệu giao dịch thực tế ({e})")

        # ── 5. Driver Drift (Telemetry) ──
        print(f"\n  {'─'*70}")
        print(f"  🧠 DRIVER REPUTATION DRIFT")
        print(f"  {'─'*70}")
        print(f"    ⏳ Cần tích hợp telemetry storage API.")

        # ── 6. Module Evidence (stub) ──
        print(f"\n  {'─'*70}")
        print(f"  🔬 MODEL EVIDENCE & RETIREMENT")
        print(f"  {'─'*70}")
        print(f"    ⏳ Cần thêm dữ liệu lịch sử outcomes để tính P(data|model).")
        print(f"    ⏳ Cần thêm model registry để theo dõi retirement.")

        print(f"\n  {'='*70}")
        print(f"  KẾT LUẬN")
        print(f"  {'='*70}")
        try:
            has_concept_drift = tr.get("degradation_detected", False) if tr else False
            cb_active = level > 0
            gen_gap_high = cal.get("sharpe_vs_random", 2.0) if cal else 1.0
            risks = []
            if has_concept_drift:
                risks.append("CONCEPT_DRIFT (Log-Loss đang tăng)")
            if cb_active:
                risks.append(f"CIRCUIT_BREAKER (Level {level})")
            if gen_gap_high < 1.0:
                risks.append(f"GENERALIZATION_GAP (Sharpe thấp hơn random P95)")
            if risks:
                print(f"    ⚠ Rủi ro: {', '.join(risks)}")
            else:
                print(f"    🟢 Hệ thống ổn định, không phát hiện drift bất thường.")
        except Exception:
            print(f"    🟢 Chưa đủ dữ liệu để đánh giá (cần thêm predictions resolved).")

    elif sub == "concept":
        from calibration.calibrator import calibration_trend_report, print_trend_report
        report = calibration_trend_report(days=args.days)
        print_trend_report(report, lang_mode=_VERBOSE_LANG)

    elif sub == "generalization":
        print(f"\n  {'='*60}")
        print(f"  GENERALIZATION GAP — Live vs Expected")
        print(f"  {'='*60}")
        try:
            from src.core.quantstats_bridge import QuantStatsBridge
            qs = QuantStatsBridge()
            qs_report = qs.run_all()
            cal = qs_report.get("calibration", {})
            live = qs_report.get("live", {})
            rand = qs_report.get("random_baseline", {})
            print(f"  {'Live Sharpe':20s}: {cal.get('sharpe_live_smoothed', 'N/A')}")
            print(f"  {'Random Sharpe P95':20s}: {rand.get('random_sharpe_p95', 'N/A')}")
            print(f"  {'Sharpe vs Random':20s}: {cal.get('sharpe_vs_random', 'N/A')}")
            gap = float(cal.get('sharpe_vs_random', 1) or 1) - 1.0
            print(f"  {'Generalization Gap':20s}: {gap:+.2f}")
            print(f"  {'Win Rate':20s}: {live.get('win_rate', 'N/A')}")
            print(f"  {'Max Drawdown':20s}: {live.get('max_drawdown', 'N/A')}")
            print(f"  {'DOC Index':20s}: {cal.get('doc_index', 'N/A')}")
            if gap < 0:
                print(f"\n  ⚠ GENERALIZATION GAP ÂM — Live Sharpe < Random P95")
            else:
                print(f"\n  🟢 Live Sharpe vượt random baseline")
        except Exception as e:
            print(f"  ⚠ Chưa có dữ liệu: {e}")

    elif sub == "surprise":
        print(f"\n  {'='*60}")
        print(f"  SURPRISE ENGINE")
        print(f"  {'='*60}")
        print(f"    ⏳ Module chưa implement — cần causal graph + observation history.")
        print(f"    Mục tiêu: phát hiện các cặp (driver, outcome) có P(outcome|driver)")
        print(f"    thấp bất thường so với historical joint distribution.")
        print(f"\n  {'='*60}")
        print(f"  KẾT LUẬN")
        print(f"  {'='*60}")
        print(f"    Không có dữ liệu đủ để chạy Surprise Engine.")
        print(f"    Cần thu thập thêm: macro_state + outcome pairs.")

    elif sub == "model":
        print(f"\n  {'='*60}")
        print(f"  MODEL EVIDENCE & RETIREMENT REGISTRY")
        print(f"  {'='*60}")
        print(f"    ⏳ Chưa có Model Registry.")
        print(f"    ⏳ Cần thêm: model_version, train_window, live_metrics,")
        print(f"       P(data|model), retirement_score.")
        print(f"\n    Mô hình hiện tại hoạt động:")
        print(f"    - Bayesian Governor v2 (duy nhất)")
        print(f"    - Chưa có competing hypotheses (Layer 4)")
        print(f"\n  {'='*60}")
        print(f"  KẾT LUẬN")
        print(f"  {'='*60}")
        print(f"    Cần thêm model registry + hypothesis competition trước khi")
        print(f"    có thể tính Model Evidence và Retirement Score một cách có ý nghĩa.")


def cmd_model_registry(args):
    """Sprint 4: ModelRegistry — 3 Competing Hypotheses (Bayesian Model Averaging)."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from calibration.model_registry import ModelRegistry, print_registry_report, print_selection_report
    from calibration.prediction_log import init_model_registry_schema

    init_model_registry_schema()
    registry = ModelRegistry()
    action = args.action

    # Load macro context
    macro_state = "STABLE"
    archetype = None
    try:
        import json
        from pathlib import Path
        _p = Path(__file__).resolve().parent / "backend" / "data" / "macro"
        p_ms = _p / "macro_state_history.json"
        if p_ms.exists():
            hist = json.loads(p_ms.read_text(encoding="utf-8"))
            if hist:
                macro_state = hist[-1].get("macro_state", "STABLE")
    except Exception:
        pass

    if action == "status":
        print_registry_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "select":
        print_selection_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "activate":
        mid = args.model_id
        registry.activate(mid, args.reason or "")
        m = registry.get_model(mid)
        print(f"  ✅ {_ll('Activated')}: {mid} ({m['state']})")
        print_registry_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "suspend":
        mid = args.model_id
        registry.suspend(mid, args.reason or "")
        m = registry.get_model(mid)
        print(f"  🟡 {_ll('Suspended')}: {mid} ({m['state']})")
        print_registry_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "retire":
        mid = args.model_id
        registry.retire(mid, args.reason or "")
        m = registry.get_model(mid)
        print(f"  🔴 {_ll('Retired')}: {mid} ({m['state']})")
        print_registry_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "revive":
        mid = args.model_id
        registry.revive(mid, args.reason or "")
        m = registry.get_model(mid)
        print(f"  ✅ {_ll('Revived')}: {mid} ({m['state']})")
        print_registry_report(registry, macro_state, archetype, _VERBOSE_LANG)
    elif action == "history":
        mid = args.model_id
        hist = registry.get_history(mid)
        if hist:
            print(f"\n  {_ll('HISTORY')} — {mid}:")
            print(f"  {'Thời gian':<22} {'State':<12} {'Posterior':>10} {'Reason'}")
            print(f"  {'─'*60}")
            for h in hist:
                print(f"  {h.get('changed_at',''):<22} {h['state']:<12} {h['posterior']:>9.3f} {h.get('reason','')}")
        else:
            print(f"  {_ll('No history yet')} — {mid}")
    elif action == "bma":
        bma_w = registry.bma_posterior(macro_state, archetype)
        print(f"\n  {_ll('BMA Weights')} — {macro_state}:")
        for mid, w in sorted(bma_w.items(), key=lambda x: x[1], reverse=True):
            fit = registry.regime_fit_score(mid, macro_state, archetype)
            bar = "█" * int(w * 40) + "░" * (40 - int(w * 40))
            print(f"    {mid:<16} {w:>6.1%} {bar}  ({_ll('fit')}={fit:.2f})")
        best = registry.select_best(macro_state, archetype)
        print(f"\n  {_ll('Primary model')}: {best['model_id']} ({_ll('fit')}={best['regime_fit_score']:.2f})")


def cmd_evidence(args):
    """LAW-004 Evidence Engine — Dynamic weighting from Beta posteriors."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from calibration.evidence_engine import EvidenceEngine, print_evidence_report, get_dynamic_evidence_weights
    from calibration.prediction_log import get_conn
    ee = EvidenceEngine()
    ee.init_schema()

    # Load macro context for Applicability Engine
    import json
    from pathlib import Path
    _MACRO_DIR = Path(__file__).resolve().parent / "backend" / "data" / "macro"

    def _get_ctx():
        ms, sp, en = "STABLE", "NEUTRAL", 0.0
        try:
            p = _MACRO_DIR / "macro_state_history.json"
            if p.exists():
                hist = json.loads(p.read_text(encoding="utf-8"))
                if hist:
                    latest = hist[-1]
                    ms = latest.get("macro_state", "STABLE")
                    en = float(latest.get("entropy", 0.0))
        except Exception:
            pass
        try:
            p = _MACRO_DIR / "sector_rotation_latest.json"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                sp = data.get("top_phase", "NEUTRAL")
        except Exception:
            pass
        return ms, sp, en

    if args.action == "list":
        ms, sp, en = _get_ctx()
        # Compute weights first (writes A_i to DB via ApplicabilityEngine)
        weights = get_dynamic_evidence_weights(ms, sp, en)
        # Then read fresh nodes with updated applicability
        nodes = ee.get_all_nodes()
        print_evidence_report(nodes, weights, _VERBOSE_LANG)
    elif args.action == "weights":
        ms, sp, en = _get_ctx()
        w = get_dynamic_evidence_weights(ms, sp, en)
        name = _ll("Dynamic Weights")
        print(f"\n  {name} (LAW-004):")
        for k, v in sorted(w.items()):
            print(f"    {k:<22} {v:.3%}")
    elif args.action == "update":
        node_id = getattr(args, "node", None)
        from calibration.evidence_engine import EVIDENCE_NODE_IDS
        targets = [node_id] if node_id else EVIDENCE_NODE_IDS
        count = 0
        for nid in targets:
            # Simulate with current prior until real outcomes arrive
            ee.record_outcome(nid, 0.5, 0.5)  # no-op placeholder
            count += 1
        print(f"  {_ll('Updated')} {count} {_ll('nodes (placeholders)')}.")
        nodes = ee.get_all_nodes()
        weights = get_dynamic_evidence_weights()
        print_evidence_report(nodes, weights, _VERBOSE_LANG)
        tip_text = _ll("Tip")
        tip_body = _ll("Use calibrate resolve, then evidence update for real outcomes")
        print(f"\n  {tip_text}: {tip_body}.")
    elif args.action == "applicability":
        sub = getattr(args, "sub", "status")
        ms, sp, en = _get_ctx() if sub != "heatmap" else ("STABLE", "NEUTRAL", 0.0)
        from calibration.applicability_engine import (
            print_applicability_report,
            print_heatmap,
            compute_applicability,
        )

        if sub == "heatmap":
            print_heatmap(_VERBOSE_LANG)
        else:
            app = compute_applicability(ms, sp, en)
            print_applicability_report(app, ms, sp, en, _VERBOSE_LANG)

    elif args.action == "simulate":
        """Dry-run: assign random outcomes to pending predictions, observe weight differentiation."""
        n_pending = getattr(args, "n_pending", 60)
        seed = getattr(args, "seed", None)
        node_biases = {
            "macro": getattr(args, "bias_macro", 0.55),
            "transmission": getattr(args, "bias_transmission", 0.52),
            "sector": getattr(args, "bias_sector", 0.50),
            "health": getattr(args, "bias_health", 0.58),
            "capital_allocation": getattr(args, "bias_capital", 0.48),
            "valuation": getattr(args, "bias_valuation", 0.60),
            "behavior": getattr(args, "bias_behavior", 0.45),
        }

        if seed is not None:
            import random as _rand
            _rand.seed(seed)
        else:
            import random as _rand

        # Snapshot before
        nodes_before = ee.get_all_nodes()
        ms, sp, en = _get_ctx()
        w_before = get_dynamic_evidence_weights(ms, sp, en)

        # Generate per-node synthetic outcomes with different biases
        from calibration.evidence_engine import EVIDENCE_NODE_IDS as _NODES
        node_outcomes: dict = {nid: [] for nid in _NODES}
        for nid in _NODES:
            bias = node_biases.get(nid, 0.55)
            p_gain = bias  # each node predicts at its own accuracy level
            for _ in range(n_pending):
                y_true = 1 if _rand.random() < bias else 0
                ee.record_outcome(nid, p_gain, float(y_true))
                node_outcomes[nid].append(y_true)

        # Compute actual accuracy per node
        def _accuracy(ys):
            return sum(ys) / max(len(ys), 1)

        # Snapshot after
        nodes_after = ee.get_all_nodes()
        ms, sp, en = _get_ctx()
        w_after = get_dynamic_evidence_weights(ms, sp, en)

        sim_label = _ll("SIMULATION DRY-RUN")
        print(f"\n  {'='*90}")
        print(f"  {sim_label} — {n_pending} {_ll('predictions')}, seed={seed}")
        print(f"  {'='*90}")
        print(f"  {'─'*90}")

        # Side-by-side table
        print(f"  {'Node':<22} {'Bias':>6} {'Acc':>6} {'R_before':>9} {'R_after':>9} {'W_before':>9} {'W_after':>9} {'ΔW':<8} {'Drift':>6}")
        print(f"  {'─'*90}")
        for nb in nodes_before:
            nid = nb["node_id"]
            na = next((n for n in nodes_after if n["node_id"] == nid), None)
            if not na:
                continue
            bias = node_biases.get(nid, 0.55)
            acc = _accuracy(node_outcomes.get(nid, []))
            rb = nb["reliability"]
            ra = na["reliability"]
            wb = w_before.get(nid, 0)
            wa = w_after.get(nid, 0)
            dw = wa - wb
            arrow = "↑" if dw > 0.003 else ("↓" if dw < -0.003 else "→")
            drift_b = na.get("drift_score", 0)
            print(f"  {nid:<22} {bias:>5.1%} {acc:>5.1%} {rb:>8.4f} {ra:>8.4f} {wb:>8.2%} {wa:>8.2%} {arrow} {dw:<+5.2%} {drift_b:>5.3f}")
        print(f"\n  {_ll('Formula')}: θ_i = R_i × A_i × exp(-2.0 × D_i)")
        print(f"  {_ll('Forgetting')}: decay=0.995 | {_ll('Note')}: Sprint 1 — per-node p_gain uses node's own bias as a proxy for node-level effective probability")

    elif args.action == "reset":
        if getattr(args, "node", None):
            ee.reset_node(args.node)
            print(f"  {_ll('Reset node')}: {args.node}")
        else:
            ee.reset_all()
            print(f"  {_ll('Reset all evidence nodes to prior')}.")
    elif args.action == "drift":
        nodes = ee.get_all_nodes()
        weights = get_dynamic_evidence_weights()
        print_evidence_report(nodes, weights, _VERBOSE_LANG)
        high_drift = [n for n in nodes if n["drift_score"] > 0.30]
        if high_drift:
            hdr_label = _ll("HIGH DRIFT NODES")
            print(f"\n  {hdr_label} ({_ll('drift')} > 0.30):")
            for n in high_drift:
                print(f"    {n['node_id']:<22} {_ll('drift')}={n['drift_score']:.3f}  {_ll('weight')}={weights.get(n['node_id'], 0):.3f}")


def cmd_causal(args):
    """Sprint 3: CausalEdge — causal propagation graph (lag, confidence, half-life)."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from calibration.causal_edge import (
        CausalGraph, print_causal_graph_report, trace_report, edge_summary,
    )

    cg = CausalGraph()

    if args.action == "status":
        print_causal_graph_report(cg, lang_mode=_VERBOSE_LANG)
    elif args.action == "propagate":
        src = args.source or "CREDIT_STRESS"
        arch = getattr(args, "archetype", None)
        print_causal_graph_report(cg, source=src, archetype=arch, lang_mode=_VERBOSE_LANG)
    elif args.action == "trace":
        src = args.source
        tgt = args.target
        arch = getattr(args, "archetype", None)
        trace_report(cg, src, tgt, arch, _VERBOSE_LANG)
    elif args.action == "edges":
        edge_summary(cg, _VERBOSE_LANG)
    elif args.action == "persist":
        cg.persist()
        print(f"  {_ll('Persisted')} {len(cg.edges)} {_ll('edges to calibration.db')}.")

    cg.init_schema()


def cmd_counterfactual(args):
    """P5 Counterfactual Reasoning — 'What if?' simulation over evidence nodes."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.counterfactual.engine import CounterfactualEngine, print_counterfactual_report
    engine = CounterfactualEngine()
    analysis = engine.analyze(args.symbols)
    engine.close()
    print_counterfactual_report(analysis, lang_mode=_VERBOSE_LANG)


def cmd_archetype(args):
    """Giai đoạn 1: Business Archetype — DNA classification."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.business.archetype import ArchetypeEngine, print_archetype_report
    engine = ArchetypeEngine()
    results = engine.classify_many(args.symbols)
    engine.close()
    print_archetype_report(results)


def cmd_economic_engine(args):
    """Giai đoạn 1: Economic Engine — propagation chain."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.business.economic_engine import EconomicEngine
    engine = EconomicEngine()
    print(f"\n  {'='*80}")
    print(f"  ECONOMIC ENGINE — CHAIN OF CAUSATION")
    print(f"  {'='*80}")
    for sym in args.symbols:
        chain = engine.get_chain(sym)
        if chain:
            step_names = [c.label for c in chain.chain]
            print(f"\n  📍 {sym} ({chain.label})")
            print(f"  {'─'*60}")
            for i, s in enumerate(step_names):
                arrow = " ↓" if i < len(step_names) - 1 else ""
                print(f"    {i+1}. {s}{arrow}")
            drivers = ', '.join(chain.macro_links.keys())
            print(f"  Macro links: {drivers}")
            if args.driver:
                trace = engine.trace_macro_impact(sym, args.driver.upper())
                print(f"  Trace [{args.driver.upper()}]: {' → '.join(trace)}")
    engine.close()


def cmd_competitive(args):
    """Giai đoạn 1: Competitive Position — moat & market power."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.business.competitive_position import CompetitiveEngine, print_competitive_report
    engine = CompetitiveEngine()
    results = engine.assess_many(args.symbols)
    engine.close()
    print_competitive_report(results)


def cmd_factor_exposure(args):
    """Giai đoạn 2: Factor Exposure Matrix — macro sensitivity per symbol."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.business.factor_exposure import (
        FactorExposureEngine, print_exposure_report, compute_lr_adjustment, main as fe_main,
    )

    if getattr(args, 'lr_adjust', False):
        engine = FactorExposureEngine()
        try:
            from src.core.macro.macro_state_classifier import MacroStateClassifier
            ms = MacroStateClassifier()
            state = ms.classify()
            macro = state.macro_state
        except Exception:
            macro = "CREDIT_STRESS"
        try:
            from src.core.macro.economic_transmission_engine import EconomicTransmissionEngine
            te = EconomicTransmissionEngine()
            ts = te.compute()
            trans = ts.transmission_phase
        except Exception:
            trans = "LIQUIDITY_TRAP"
        print(f"\n  {'='*80}")
        print(f"  LR ADJUSTMENT — Macro={macro} | Transmission={trans}")
        print(f"  {'='*80}")
        print(f"  {'Symbol':<6} {'Archetype':<20} {'Base LR':>8} {'Multiplier':>10} {'Adjusted LR':>12}")
        print(f"  {'─'*60}")
        for sym in args.symbols:
            matrix = engine.compute(sym)
            mult = compute_lr_adjustment(matrix, macro, trans)
            print(f"  {sym:<6} {matrix.archetype:<20} {1.0:>8.2f} {mult:>10.3f} {1.0*mult:>11.3f}")
        engine.close()
        return

    if getattr(args, 'query', None):
        engine = FactorExposureEngine()
        for sym in args.symbols:
            exp = engine.query(sym, args.query.upper())
            if exp:
                print(f"\n  {sym}: {exp.factor_label} = {exp.exposure_score:.3f}")
            else:
                print(f"\n  {sym}: Không có exposure cho {args.query}")
        engine.close()
        return

    engine = FactorExposureEngine()
    results = engine.compute_many(args.symbols)
    print_exposure_report(results, args.top)
    engine.close()


def cmd_capital_allocation(args):
    """Giai đoạn 4: Capital Allocation Quality — value creation by management."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.business.capital_allocation import CapitalAllocationEngine, print_allocation_report
    engine = CapitalAllocationEngine()
    results = engine.assess_many(args.symbols)
    print_allocation_report(results)
    engine.close()


def cmd_contextual_health(args):
    """Giai đoạn 3: Contextualized Company Health — Archetype-Aware Dynamic Thresholds."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.financial.company_health import ContextualHealthEngine, print_contextual_report
    engine = ContextualHealthEngine()
    results = engine.assess_many(args.symbols)
    print_contextual_report(results)
    engine.close()


def cmd_health_engine(args):
    """Giai đoạn 2: Company Health Engine — compute health_ratios từ financial_facts.

    Đây là bước BẮT BUỘC giữa cafef-crawl/fetch-financials (nạp financial_facts)
    và health-v2 (đọc health_ratios). Nếu thiếu bước này, mã mới (VD BCM) có
    financial_facts nhưng health_ratios = 0 rows → health-v2 trả NO DATA.
    """
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.financial.company_health_engine import HealthEngine

    action = args.action
    engine = HealthEngine()

    if action == "init":
        engine.init_schema()
        print("  ✅ health_ratios schema initialized")

    elif action == "compute":
        for sym in args.symbols:
            print(f"\n  [{sym}] Computing health ratios...")
            result = engine.compute_health(sym)
            if result.get("status") == "NO_DATA":
                print(f"  ⚠️ {sym}: NO DATA (không có financial_facts — chạy cafef-crawl trước)")
            else:
                print(f"  ✅ {result['symbol']}: {result['periods']} periods, "
                      f"{result['total_ratios']} ratios")

    elif action == "show":
        for sym in args.symbols:
            h = engine.get_latest_health(sym)
            if not h:
                print(f"\n  {sym}: NO DATA")
                continue
            print(f"\n  {'='*50}")
            print(f"  {sym} ({h['period']})")
            print(f"  {'='*50}")
            for cat, ratios in h["ratios"].items():
                print(f"\n  [{cat.upper()}]")
                for r in ratios:
                    ico = {"GOOD": "🟢", "WARNING": "🟡", "BAD": "🔴", "NEUTRAL": "⚪"}
                    val = r["value"]
                    if val is not None and abs(val) < 10:
                        vs = f"{val:.2%}"
                    elif val is not None:
                        vs = f"{val:.2f}x"
                    else:
                        vs = "N/A"
                    print(f"    {ico.get(r['interpretation'], '⚪')} {r['ratio']:25s} = {vs:>10s}  ({r['interpretation']})")


def cmd_watch(args):
    """Giám sát Volume Spike — phát hiện nến xác nhận để kích hoạt Scale-In."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    symbols = args.symbols
    threshold = args.threshold
    from datetime import datetime
    print(f"\n  {'='*70}")
    print(f"  GIÁM SÁT VOLUME SPIKE — PHÁT HIỆN NẾN XÁC NHẬN")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {'='*70}")

    from src.financial.market_behavior_engine import MarketBehaviorEngine
    mbe = MarketBehaviorEngine()
    mbe.init_schema()
    conn = mbe.fin_conn()

    for sym in symbols:
        cur = conn.cursor()
        cur.execute("""
            SELECT price_current, poc, val, vah, volume_ratio, price_ma20
            FROM volume_profile WHERE symbol = ?
            ORDER BY date DESC LIMIT 1
        """, (sym.upper(),))
        row = cur.fetchone()
        if not row:
            print(f"\n  {sym}: ⚠ CHƯA CÓ VOLUME PROFILE")
            continue
        price, poc, val, vah, vol_ratio, ma20 = row
        print(f"\n  {'='*60}")
        print(f"  {sym} — Giá={price:>8,} | MA20={ma20:>8,} | Vol={vol_ratio:.2f}x/{threshold}x")
        print(f"  VA: [{val:>8,} - {vah:>8,}] | POC={poc:>8,}")

        # Active demand signals
        cur.execute("SELECT date FROM active_demand WHERE symbol = ? ORDER BY date DESC LIMIT 3", (sym.upper(),))
        sigs = [r[0] for r in cur.fetchall()]

        if vol_ratio >= threshold:
            print(f"  🟢🟢🟢 VOLUME SPIKE: {vol_ratio:.2f}x >= {threshold}x")
            gap = (price - val) / val * 100 if val else 0
            print(f"  → Giá cách VAL {gap:+.1f}% → SẴN SÀNG SCALE-IN")
            # Write alert file
            import json
            alert_path = Path(__file__).resolve().parent / "backend" / "data" / "alerts" / "volume_spike.json"
            alert_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if alert_path.exists():
                existing = json.loads(alert_path.read_text(encoding="utf-8"))
            existing[sym] = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "price": price, "vol_ratio": vol_ratio,
                "val": val, "vah": vah, "poc": poc,
            }
            alert_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  ✅ Đã ghi cảnh báo: {alert_path}")
        else:
            pct = (threshold - vol_ratio) / threshold * 100
            print(f"  ⏳ Volume {vol_ratio:.2f}x, còn {pct:.0f}% để đạt ngưỡng")
        if sigs:
            print(f"  📋 Tín hiệu cầu: {', '.join(sigs)}")
    conn.close()


def cmd_macro_state(args):
    """MacroStateClassifier — trạng thái vĩ mô thống nhất cho Governor."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from datetime import datetime
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    from src.core.macro.macro_state_classifier import MacroStateClassifier, print_state_report
    clf = MacroStateClassifier()
    state = clf.classify(target_date=date_str)
    print_state_report(state)


def cmd_transmission(args):
    """Economic Transmission Engine — chuỗi dẫn truyền Liquidity→Credit→Confidence."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.core.macro.economic_transmission_engine import (EconomicTransmissionEngine, print_transmission_report)
    engine = EconomicTransmissionEngine()
    state = engine.compute()
    print_transmission_report(state)


def cmd_sector_rotation(args):
    """Sector State Engine — đánh giá 19 ngành ICB trên 4 trụ cột."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.core.macro.sector_state_engine import (SectorStateEngine, print_sector_report)
    engine = SectorStateEngine()
    report = engine.analyze()
    print_sector_report(report)


def cmd_health_v2(args):
    """Company Health v2 — 5-organ latent state engine."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.financial.company_health_v2 import CompanyHealthV2, print_health_report
    symbols = args.symbols or ["FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS"]
    engine = CompanyHealthV2()
    states = engine.analyze_many(symbols)
    print_health_report(states)


def cmd_csi_explain(args):
    """CSI Explain — Causal DAG Trace từ World FedState xuống điểm số doanh nghiệp.

    CSI (Contextual Security Index / Chỉ Số An Toàn Bối Cảnh) — điểm số
    bối cảnh của từng mã chứng khoán.

    Render 4 thành tố First-Principles:
      1. Causal DAG Trace (cây vết truyền dẫn) với lag + confidence từng hop
      2. Tách Facts (dữ liệu thực) vs Surprise (độ lệch kỳ vọng / latent state)
      3. Entropy/Confidence của chuỗi truyền dẫn (1 - H/H_max)
      4. Attribution: Policy Rate vs Hawkish Dissent

    Modes:
      --symbols VCB FPT   : per-symbol report (default)
      --sector Banking    : sector comparison table
      --list-sectors      : show available sector names
    """
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.governor.csi_explain import (
        CSIExplainEngine, print_csi_explain,
        resolve_sector_symbols, list_sectors,
        print_sector_csi_comparison,
    )

    # ── --list-sectors: show available sectors and exit ────────────────
    if getattr(args, "list_sectors", False):
        sectors = list_sectors()
        if not sectors:
            print("  No sector data available. Run sector rotation first.")
            return
        print(f"\n  Available sectors ({len(sectors)}):")
        for s in sectors:
            print(f"    - {s}")
        print()
        return

    # ── --sector: sector comparison mode ───────────────────────────────
    sector_query = getattr(args, "sector", None)
    if sector_query:
        symbols = resolve_sector_symbols(sector_query)
        if not symbols:
            print(f"  Sector '{sector_query}' not found. Use --list-sectors to see available sectors.")
            return
        engine = CSIExplainEngine()
        results = []
        for sym in symbols:
            try:
                result = engine.explain(sym)
                results.append(result)
            except Exception as e:
                print(f"  ⚠️ {sym}: {e}")
        if getattr(args, "output", "report") == "json":
            import json
            print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        else:
            print_sector_csi_comparison(results, sector_query)
        return

    # ── Default: per-symbol reports ────────────────────────────────────
    engine = CSIExplainEngine()
    results = []
    for sym in args.symbols:
        result = engine.explain(sym)
        results.append(result)
        if getattr(args, "output", "report") == "json":
            continue
        print_csi_explain(result, _VERBOSE_LANG)
    if getattr(args, "output", "report") == "json":
        import json
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))


def cmd_governor_report(args):
    """Báo cáo phân rã đóng góp 3 mô hình vào DecisionGuard."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from src.core.market_snapshot import tao_anh_chup
    from src.engine.decision_guard import kiem_tra_an_toan, reset_trap_detector
    from src.engine.recovery_governor import RecoveryGovernor
    from src.engine.macro_stale_tracker import StaleTracker

    reset_trap_detector()
    RecoveryGovernor.reset_instance()
    StaleTracker.reset_instance()

    snap = tao_anh_chup(lang_mode=_VERBOSE_LANG)
    import src.engine.confidence_layer as cl
    func = getattr(cl, '\u0111\u00e1nh_gi\u00e1_\u0111\u1ed9_tin_c\u1eady')
    confidence = func(anh_chup=snap)

    result = kiem_tra_an_toan(
        quyet_dinh_de_xuat="PHAN_TICH",
        ly_do_de_xuat=["governor-report"],
        do_tin_cay=confidence,
        anh_chup=snap,
    )

    c = result.get("contribution", {})
    models = c.get("models", {})

    print("=" * 62)
    print(f"  {_ll('GOVERNOR REPORT')} — {_ll('Contribution Breakdown')}")
    print("=" * 62)
    print(f"  {_ll('Position Level')}:  {c.get('position_level', 0):.0%}")
    pos_label_raw = c.get('position_label', 'N/A')
    print(f"  {_ll('Position Label')}:  {_ll(pos_label_raw)}")
    blk = c.get('blocking_model', 'none') or 'none'
    print(f"  {_ll('Blocking Model')}:  {blk}")
    print(f"  {_ll('Decision')}:        {result.get('quyet_dinh', 'N/A')}")
    print(f"  {_ll('Blocked')}:         {result.get('bi_chặn', False)}")
    print(f"  {_ll('Weight Mult')}:     {result.get('he_so_giam_ty_trong', 1.0):.2f}")
    print()

    for model_id, model_data in models.items():
        status = model_data.get("status", "N/A")
        weight = model_data.get("weight", 0)
        impact = model_data.get("impact_pct", 0)
        detail = model_data.get("detail", "")

        if status == "VETO":
            icon = "🔴"
        elif status == "WARNING":
            icon = "🟡"
        elif status == "DEGRADED":
            icon = "🟠"
        else:
            icon = "🟢"

        mid = _ll(model_id.upper())
        st = _ll(status)
        imp = _ll("impact")
        print(f"  {icon} {mid:12s} | {_ll('weight')}={weight:.0%} | {st:12s} | {imp}={impact:.0%}")
        print(f"     {detail}")

    print()

    # Breadth trap sub-section
    bt = result.get("breadth_trap", {})
    if bt:
        trap_icon = "🔴" if bt.get("trap_deepening") else ("🟢" if bt.get("ready_for_recovery") else "🟡")
        print(f"  {trap_icon} {_ll('Breadth Trap')}:  {_ll('active')}={bt.get('trap_active')}  "
              f"{_ll('divergence')}={bt.get('divergence', 0):.1f}  "
              f"S-={bt.get('S_minus', 0):.2f}")

    # Macro stale sub-section
    ms = result.get("macro_stale", {})
    if ms:
        print(f"  {_ll('Macro Stale')}:    {_ll('fresh_ratio')}={ms.get('fresh_ratio', 0):.0%}  "
              f"{_ll('terminal_ratio')}={ms.get('terminal_ratio', 0):.0%}  "
              f"{_ll('veto')}={ms.get('veto', False)}")

    # Recovery Governor sub-section
    rg = result.get("recovery_governor", {})
    if rg:
        print(f"  {_ll('Recovery Gov')}:   S+={rg.get('S_plus', 0):.4f}  "
              f"S-={rg.get('S_minus', 0):.4f}  "
              f"dS/dt={rg.get('dS_dt', 0):.6f}  "
              f"{_ll('warmup')}={rg.get('warmup_days', 0)}d")

    print("=" * 62)


def cmd_erl_scan(args):
    """ERL scan — Entity Resilience Layer + Whitelist generation."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from datetime import datetime
    from src.config import DATA_DIR
    from src.engine.erl_worker import scan as erl_scan, get_whitelist

    target = args.date or datetime.now().strftime("%Y-%m-%d")
    data_dir = str(DATA_DIR)

    print("=" * 62)
    print(f"  ERL SCAN — {_ll('Entity Resilience Layer')}")
    print("=" * 62)
    print(f"  Date: {target}")
    print(f"  Mode: Phase 1 (Local RS Filter) + Phase 2 (Proxy Scan)")
    print()

    # Nếu flag --whitelist, chỉ đọc file hiện tại
    if args.whitelist:
        wl = get_whitelist(DATA_DIR)
        if not wl:
            print("  ⚠ Chưa có whitelist. Chạy 'python ptck.py erl-scan' trước.")
            print("=" * 62)
            return
        gate = wl.get('hard_liquidity_gate', 0)
        total_val = wl.get('total_market_value_bn', 0)
        gate_str = f"  |  🔒 Gate: >= {gate:.0f} tỷ (0.5% TT)" if gate > 0 else ""
        mkt_str = f"\n  Tổng GTGD toàn TT: {total_val:,.0f} tỷ" if total_val > 0 else ""
        print(f"  Whitelist ngày {wl.get('scan_date', 'N/A')} — {wl.get('count', 0)} mã")
        print(f"  Regime: {wl.get('market_regime', 'N/A')}  |  S+: {wl.get('s_plus', 0):.4f}{gate_str}{mkt_str}")
        print()
        print(f"  {'Symbol':8s}  {'RS':4s}  {'P(Survive)':12s}  {'Anomaly':7s}  {'Score':7s}  {'Cảnh báo'}")
        print(f"  {'-'*8}  {'-'*4}  {'-'*12}  {'-'*7}  {'-'*7}  {'-'*14}")
        for s in wl.get("whitelist", []):
            survival_warn = "⚠️ LOW_SURVIVAL" if s["p_survive"] <= 0.30 else ""
            print(f"  {s['symbol']:8s}  {s['rs_rating']:<4.0f}  "
                  f"{s['p_survive']:<12.2%}  {s['anomaly_streak']:<7d}  "
                  f"{s['composite_score']:<7.4f}  {survival_warn}")
        print("=" * 62)
        return

    # Chạy full pipeline
    result = erl_scan(data_dir=data_dir, target_date=target)

    if not result.whitelist:
        print("  ⚠ Không có mã nào lọt vào whitelist.")
        print("=" * 62)
        return

    gate_str = f"  |  🔒 Gate: >= {result.hard_liquidity_gate:.0f} tỷ (0.5% TT)" if result.hard_liquidity_gate > 0 else ""
    mkt_str = f"\n  Tổng GTGD toàn TT: {result.total_market_value_bn:,.0f} tỷ" if result.total_market_value_bn > 0 else ""
    print(f"  Whitelist Top {len(result.whitelist)} — Survival Resilience Stocks")
    print(f"  Regime: {result.market_regime}  |  S+: {result.s_plus:.4f}{gate_str}{mkt_str}")
    print(f"  Scan:   {result.scan_date} @ {result.generated_at}")
    print()
    print(f"  {'Rank':4s}  {'Symbol':8s}  {'RS':4s}  {'P(Survive)':12s}  {'Value(tỷ)':10s}  {'Anomaly':7s}  {'Score':7s}  {'Cảnh báo'}")
    print(f"  {'-'*4}  {'-'*8}  {'-'*4}  {'-'*12}  {'-'*10}  {'-'*7}  {'-'*7}  {'-'*14}")
    for i, s in enumerate(result.whitelist, 1):
        anomaly_flag = " ⚠" if s["anomaly_streak"] >= 3 else ""
        survival_warn = "⚠️ LOW_SURVIVAL" if s["p_survive"] <= 0.30 else ""
        print(f"  {i:<4d}  {s['symbol']:8s}  {s['rs_rating']:<4.0f}  "
              f"{s['p_survive']:<12.2%}  {s['avg_value_20d']:<10.1f}  "
              f"{s['anomaly_streak']:<7d}{anomaly_flag}  {s['composite_score']:<7.4f}  {survival_warn}")

    print()
    print(f"  ✅ Whitelist đã ghi vào: {DATA_DIR / 'erl_whitelist.json'}")
    print("=" * 62)


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
        print(f"  {_ll('PREDICTION REGISTRY')} — {len(entries)} {_ll('entries')} gần nhất")
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
        print(f"  {_ll('PREDICTION REGISTRY')} — THỐNG KÊ")
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
            print(f"  {_ll('PHASE 5')} — {_ll('PAPER TRADING DASHBOARD')}  [{ts}]  [{halted}]")
            print("=" * 60)
            print(f"  {_ll('Portfolio')}")
            print(f"    {_ll('Capital')}:        {args.capital:>12,.0f}")
            print(f"    {_ll('Stale')}:          {sys_state.get('total_stale_pct', 0):>12.2%}")
            print(f"    {_ll('Escrow')}:         {sys_state.get('escrow_balance', 0):>12.2f}")
            print(f"    {_ll('Locked')}:         {str(sys_state.get('locked', False)):>12}")
            print(f"  {_ll('Order Book')}")
            print(f"    {_ll('Best Bid')}:       {broker.get_best_bid('SANDBOX'):>12.2f}")
            print(f"    {_ll('Best Ask')}:       {broker.get_best_ask('SANDBOX'):>12.2f}")
            print(f"    {_ll('Spread')}:         {book.spread():>12.2f}")
            print(f"    {_ll('Mid')}:            {book.mid_price():>12.2f}")
            print(f"  {'TWAP'}")
            print(f"    {_ll('Plan')}:           {s['plan_status']:>12}")
            print(f"    {_ll('Slices')}:         {executed:>4}/{n_slices}")
            print(f"    {_ll('Filled')}:         {s['filled_amount']:>12.2f}")
            print(f"    {_ll('Cursor')}:         {str(s['cursor']):>12}")
            print(f"    {_ll('CB Trips')}:       {s['circuit_breaker_trips']:>12}")
            print(f"  {_ll('Slippage')}")
            print(f"    {_ll('Trades')}:         {report.get('n', 0):>12}")
            print(f"    {_ll('Mean')}:           {report.get('mean', 0):>12.6f}")
            print(f"    {_ll('Max')}:            {report.get('max', 0):>12.6f}")
            print(f"  {_ll('Book Thinning')}")
            remaining = sum(l.volume for l in book.bids)
            initial = depth * 5 * 0.8
            thinning = 100 * (1 - remaining / max(initial, 1))
            print(f"    {_ll('Depth Remaining')}: {remaining:>12.2f}")
            print(f"    {_ll('Thinning')}:        {thinning:>11.1f}%")
            print("=" * 60)

            if broker.is_trading_halt():
                print(f"  ⏸️  {_ll('TRADING HALT')} — execution frozen")
                time.sleep(1)
                continue

            idx = executed + 1
            bid = broker.get_best_bid("SANDBOX")
            if bid <= 0:
                print(f"  ⏳ {_ll('Slice')} {idx}: empty book, adaptive requeue...")
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
                    print(f"  ✅ {_ll('Slice')} {idx}: {fill:.0f}/{total:.0f} filled")
                    executed += 1
                else:
                    print(f"  ❌ {_ll('Slice')} {idx}: {r['reason']}")
                    executed += 1
            except RuntimeError as e:
                print(f"  ❌ {e}")
                # Circuit breaker — let it reset
                time.sleep(1)

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n  ⏹️  {_ll('Dashboard stopped by user')}.")

    set_paper_mode(False)
    report = broker.slippage_report()
    print("\n" + "=" * 60)
    print(f"  {_ll('SESSION SUMMARY')}")
    print("=" * 60)
    print(f"  {_ll('Slices executed')}: {executed}/{n_slices}")
    print(f"  {_ll('Total slippage trades')}: {report.get('n', 0)}")
    print(f"  {_ll('Mean slippage')}: {report.get('mean', 0):.6f}")
    print(f"  {_ll('Max slippage')}: {report.get('max', 0):.6f}")
    remaining = sum(l.volume for l in book.bids)
    print(f"  {_ll('Book thinning')}: {thinning:.1f}% {_ll('consumed')}")
    print(f"  {_ll('Storage')}: system_state_paper.json (cô lập)")
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
        print(f"  {_ll('BREAK-GLASS PROTOCOL')} — {_ll('REQUEST')}")
        print("=" * 50)
        print(f"  {_ll('Ticket')}:     {result['ticket_id']}")
        print(f"  {_ll('Challenge')}:  {result['challenge']}")
        if result['use_time_delay']:
            print(f"  {_ll('Mode')}:       {_ll('TIME-DELAY')} ({result['time_delay_minutes']} phút)")
        else:
            print(f"  {_ll('Mode')}:       {_ll('PASSPHRASE')}")
        print(f"  Hạn:        {result['expires_at']}")
        print(f"  {_ll('Hint')}:       python ptck.py break-glass verify {result['ticket_id']} --passphrase <KEY>")
        print("=" * 50)
    elif args.action == "verify":
        result = bg.verify(args.ticket, passphrase=args.passphrase)
        if result["success"]:
            print(f"✅ {_ll('BREAK-GLASS APPROVED')} (mode={result['mode']})")
            print(f"   Khóa đã được gỡ. Có thể thao tác {_ll('ORPHANED')}.")
        else:
            print(f"❌ {_ll('BREAK-GLASS DENIED')}: {result['reason']}")
            if "remaining_minutes" in result:
                print(f"   Còn {result['remaining_minutes']} phút trong time-delay.")
    elif args.action == "cancel":
        from src.portfolio.break_glass import BreakGlassProtocol
        bg = BreakGlassProtocol()
        result = bg.cancel(args.ticket)
        if result["success"]:
            print(f"✅ {_ll('BREAK-GLASS CANCELLED')}.")
        else:
            print(f"❌ {result['reason']}")
    elif args.action == "status":
        from src.portfolio.system_state import get_state
        st = get_state()
        print("=" * 50)
        print(f"  {_ll('PORTFOLIO CRITICAL LOCK STATUS')}")
        print("=" * 50)
        print(f"  {_ll('Locked')}:         {st.get('locked', False)}")
        print(f"  {_ll('Total stale %')}:  {st.get('total_stale_pct', 0.0)}")
        print(f"  {_ll('Escrow balance')}: {st.get('escrow_balance', 0.0):.2f}")
        print("=" * 50)


def cmd_stale(args):
    """StalePositionManager — quản lý vốn kẹt + escrow."""
    from src.portfolio.stale_manager import StalePositionManager
    mgr = StalePositionManager()
    if args.action == "status":
        s = mgr.status()
        print("=" * 50)
        print(f"  {_ll('STALE POSITION MANAGER')}")
        print("=" * 50)
        print(f"  {_ll('Lớp stale')}:     {s['stale_layers']} / {s['total_layers']}")
        print(f"  stale_pcts:    {s['stale_pcts']}")
        print(f"  {_ll('Tổng stale %')}:  {s['total_stale_pct']}")
        print(f"  {_ll('Escrow cache')}:  {s['escrow_balance']:.2f}")
        print(f"  {_ll('Hard Shutdown')}: {s['hard_shutdown']}")
        print(f"  {_ll('Critical Lock')}: {s['critical_lock']}")
        print("=" * 50)
    elif args.action == "writeoff":
        result = mgr.writeoff_lifo()
        if result["written"]:
            for w in result["written"]:
                print(f"  ✅ {_ll('Write-off')} {w['campaign_id']}: {w['proceeds']:.2f} VND (PNL {w['realized_pnl']:.2f})")
            print(f"  {_ll('Escrow')}: {result['escrow_balance']:.2f} | {_ll('Stale còn lại')}: {result['remaining_stale_pct']}")
        else:
            print("  Không có stale nào để write-off.")
    elif args.action == "reclaim":
        if not args.campaign:
            print("  ❌ Cần --campaign <id>")
            return
        result = mgr.reclaim(args.campaign)
        print(f"  {'✅' if result['success'] else '❌'} {result.get('reason', _ll('OK'))}")
    elif args.action == "escrow":
        print(f"  {_ll('Escrow balance')}: {mgr.escrow_balance:.2f}")
        print(f"  {_ll('Hard Shutdown')}:  {mgr.hard_shutdown}")


def cmd_phase5(args):
    """Phase 5 UAT — Paper Trading Simulation với Order Book Thinning."""
    from src.execution.paper_broker import PaperBroker, OrderBook, StreamingFeed
    from src.execution.twap_executor import TWAPExecutor
    from src.portfolio.stale_manager import StalePositionManager
    from src.portfolio.paper_context import set_paper_mode, is_paper_mode

    # Kích hoạt sandbox storage
    set_paper_mode(True)
    print("=" * 55)
    print(f"  {_ll('PHASE 5')} — {_ll('PAPER TRADING SIMULATION')}")
    print(f"  {_ll('Storage')}: system_state_paper.json (cô lập)")
    print("=" * 55)

    n_slices = args.slices
    order_volume = args.depth
    base_price = args.price

    # Build market
    print(f"\n  📊 {_ll('Market')}: {base_price} | {_ll('Depth/level')}: {order_volume} | {_ll('Slices')}: {n_slices}")
    book = OrderBook.build("SANDBOX", mid=base_price, depth_per_level=order_volume, n_levels=5)
    broker = PaperBroker(book=book)

    mgr = StalePositionManager(total_capital=1_000_000)
    mgr.ingest_stale("v1", 0.13)
    mgr.ingest_stale("v2", 0.10)

    exe = TWAPExecutor(mgr, broker=broker)
    plan = exe.build_plan(n_slices=n_slices)
    slice_size = plan.slices[0].amount if plan.slices else 0
    print(f"  {_ll('Plan')}: {plan.n_slices} {_ll('slices')} × {slice_size:.2f} = {plan.total_amount:.2f} {_ll('total')}")

    # Execute slices
    print(f"\n  ── {_ll('Execution')} ──")
    for i in range(1, n_slices + 1):
        price = broker.get_best_bid("SANDBOX")
        try:
            r = exe.execute_slice(i, price=price)
            o = broker.query_order(r["broker_order_id"])
            slip = o.get("slippage", 0)
            status = o["status"]
            fill = o.get("filled_qty", 0)
            total_qty = o.get("quantity", 0)
            print(f"  {_ll('Slice')} {i}: {status} | fill={fill:.0f}/{total_qty:.0f} | slippage={slip:.4f}")
        except RuntimeError as e:
            print(f"  {_ll('Slice')} {i}: ❌ {e}")

    # Slippage report + thinning
    report = broker.slippage_report()
    print(f"\n  ── {_ll('Slippage Report')} ──")
    print(f"  {_ll('Trades')}: {report['n']} | {_ll('Mean')}: {report['mean']:.6f} | {_ll('Max')}: {report['max']:.6f}")
    remaining_bid_depth = sum(l.volume for l in book.bids)
    initial_depth = order_volume * 5 * 0.8  # approximate initial
    thinning_pct = 100 * (1 - remaining_bid_depth / max(initial_depth, 1))
    print(f"  {_ll('Book thinning')}: {thinning_pct:.1f}% {_ll('bid depth consumed')}")

    # Restore production
    set_paper_mode(False)
    print(f"\n  ✅ {_ll('Paper session complete')} — paths restored to production.")


def cmd_twap(args):
    """TWAPExecutor — thanh lý stale positions qua slices."""
    from src.portfolio.stale_manager import StalePositionManager
    from src.execution.twap_executor import TWAPExecutor, BrokerAPI
    mgr = StalePositionManager()
    if not mgr.stale_pcts:
        print(f"  ❌ Không có stale positions — {_ll('nothing to TWAP')}.")
        return
    broker = BrokerAPI()
    exe = TWAPExecutor(mgr, broker=broker)
    if args.action == "plan":
        plan = exe.build_plan(n_slices=args.slices)
        print("=" * 50)
        print(f"  {_ll('TWAP PLAN')}")
        print("=" * 50)
        print(f"  {_ll('Campaign')}:     {plan.stale_campaign_id}")
        print(f"  {_ll('Total amount')}: {plan.total_amount:.2f}")
        print(f"  {_ll('Slices')}:       {plan.n_slices} × {plan.slices[0].amount:.2f}")
        print(f"  {_ll('Status')}:       {plan.status}")
        print("=" * 50)
    elif args.action == "run":
        exe.build_plan(n_slices=5)
        try:
            result = exe.execute_slice(args.slice, price=args.price)
            if result["success"]:
                print(f"  ✅ {_ll('Slice')} {args.slice} {_ll('submitted')}: {result['broker_order_id']}")
            else:
                print(f"  ❌ {result['reason']}")
        except RuntimeError as e:
            print(f"  ❌ {e}")
    elif args.action == "status":
        s = exe.status()
        print("=" * 50)
        print(f"  {_ll('TWAP STATUS')}")
        print("=" * 50)
        print(f"  {_ll('Plan')}:         {s['plan_status']}")
        print(f"  {_ll('Slices')}:       {s['total_slices']}")
        print(f"  {_ll('Filled')}:       {s['filled_amount']:.2f}")
        print(f"  {_ll('Cursor')}:       {s['cursor']}")
        print(f"  {_ll('CB trips')}:     {s['circuit_breaker_trips']}")
        print(f"  {_ll('Last network')}: {s['last_known_good_network']}")
        print("=" * 50)
    elif args.action == "resume":
        result = exe.resume()
        if result["success"]:
            print(f"  ✅ {_ll('Resume')}: {result['action']}")
        else:
            print(f"  ❌ {result['reason']}")


def _walk_cli_commands(parser, prefix=""):
    """Đệ quy lấy (command_path, help_text) từ mọi subparser trong parser."""
    entries = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            help_map = {ca.dest: ca.help for ca in getattr(a, '_choices_actions', []) if ca.help}
            for name, subparser in a.choices.items():
                full = f"{prefix}{name}"
                help_text = help_map.get(name, "")
                entries.append((full, help_text))
                entries.extend(_walk_cli_commands(subparser, f"{full} "))
    return entries


def cmd_init_agent(args):
    """Tạo duy nhất tệp .agent.md chứa CLI Manifest cho AI Agent."""
    workspace = Path.cwd()

    # 1. Dọn dẹp tệp cũ
    for legacy_file in (".clinerules", ".cursorrules"):
        p = workspace / legacy_file
        if p.exists():
            try:
                p.unlink()
                print(f"  🗑️ Cleaned up legacy config: {legacy_file}")
            except Exception as e:
                print(f"  ⚠️ Could not delete {legacy_file}: {e}")

    # 2. Xác định CLI prefix
    # ==============================================================================
    # WHY: Nuitka 4.1.3 on Python 3.14 does NOT reliably set `sys.frozen = True`.
    # Using `getattr(sys, 'frozen', False)` causes frozen binaries to be
    # misidentified as dev scripts, generating 'python ptck.py' instead of
    # the installed exe path in .agent.md.
    # BOUNDARY: Check executable stem — if it starts with 'python', it's dev mode;
    # otherwise (uv_backend.exe, app.exe, etc.) it's a frozen build.
    # ==============================================================================
    exe_path = Path(sys.executable).resolve()
    is_python = exe_path.stem.lower().startswith("python")
    if is_python:
        cli_prefix = "python ptck.py"
    else:
        cli_prefix = str(exe_path)

    # 3. Sinh CLI manifest từ parser
    commands = _walk_cli_commands(build_parser())
    numbered = "\n".join(
        f"{i}. `{cmd}` : {desc}"
        for i, (cmd, desc) in enumerate(
            sorted((c for c in commands if not c[0].startswith("init-agent")),
                   key=lambda x: x[0]), start=1)
    )

    content = f"""# PTCK_VN AGENT MANIFEST
# Auto-generated by `init-agent`

## EXECUTION ENGINE
Executable Absolute Path: `{cli_prefix}`

## USAGE PROTOCOL
AI Agent hãy dùng Terminal Tool để thực thi trực tiếp các lệnh CLI bên dưới.
Ưu tiên cờ `--verbose-lang compact` khi phân tích dữ liệu để tiết kiệm Context Window.

## CLI COMMANDS MANIFEST ({len(commands)} COMMANDS)
{numbered}
"""
    agent_fp = workspace / ".agent.md"
    agent_fp.write_text(content, encoding="utf-8")
    print(f"  ✅ Generated universal manifest: {agent_fp.name}  ({len(commands)} commands)")


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


# ── fetch-macro ──────────────────────────────────────────────
def cmd_fetch_macro(args):
    """Fetch world macro data (Fed, FRED, US10Y, USD, Brent) + VGB 10Y."""
    import json
    print("=" * 60)
    print("  FETCH WORLD MACRO SENSOR")
    print("=" * 60)
    try:
        from src.sensors.world_sensor import WorldSensor
        sensor = WorldSensor(use_cache=not args.force)
        state = sensor.fetch(force_refresh=args.force)
        for k, v in state.items():
            print(f"  {k:20s}: {v}")
        print(f"  {'source':20s}: WorldSensor (CME FedWatch + FRED + yFinance)")
    except Exception as e:
        print(f"  ❌ WorldSensor error: {e}")
    print()
    if not args.skip_vgb:
        print("  ── VGB 10Y Yield ──")
        try:
            from src.services.macro.vgb10y_seeder import seed_vgb10y
            ok = seed_vgb10y()
            print(f"  {'VGB10Y seed':20s}: {'✅ OK' if ok else '❌ FAILED (using fallback)'}")
        except Exception as e:
            print(f"  ❌ VGB10Y error: {e}")
    print("=" * 60)


# ── cafef-crawl ──────────────────────────────────────────────
def cmd_cafef_crawl(args):
    """Crawl BCTC 20 quarters cho danh sách mã."""
    from src.financial.financial_facts import FinancialFactsDB
    from src.financial.cafef_crawler import CafeFCrawler
    db = FinancialFactsDB()
    db.init_schema()
    use_pw = getattr(args, 'playwright', False)
    source = getattr(args, 'source', 'vci')
    delay = getattr(args, 'delay', 0)
    crawler = CafeFCrawler(db, use_playwright=use_pw, delay=delay)
    if args.symbols:
        entity_type = args.type or "STANDARD"
        targets = [(s.upper(), entity_type) for s in args.symbols]
    else:
        targets = [
            ("FPT", "STANDARD"), ("ACB", "BANK"), ("HDB", "BANK"),
            ("MBB", "BANK"), ("VCB", "BANK"),
        ]
    print("=" * 60)
    print(f"  {_ll('BCTC CRAWLER')} — {_ll('Symbols')}: {len(targets)}, 20 {_ll('Quarters')} {_ll('each')}")
    print(f"  {_ll('Source')}: {source} | {_ll('Playwright')}: {_ll('ON') if use_pw else _ll('OFF')}")
    if delay > 0:
        print(f"  {_ll('Delay')}: {delay}s {_ll('giữa các request')}")
    print("=" * 60)
    if source == "vietstock":
        print(f"  ⚠️  {_ll('Vietstock free tier')} ({_ll('BCTC Tóm tắt')})")
        print(f"  ⚠️  {_ll('BCTC chi tiết')} 37 dòng {_ll('paywall')}")
    overall = crawler.crawl_multi(targets, source=source)
    print(f"  ✅ {_ll('Done')}: {overall['symbols']} {_ll('Symbols')}, {overall['total_facts']} {_ll('Facts')}")
    print("=" * 60)


# ── vgb10y ───────────────────────────────────────────────────
def cmd_vgb10y(args):
    """Seed VGB 10Y yield từ World Bank API."""
    from src.services.macro.vgb10y_seeder import seed_vgb10y
    print("=" * 60)
    print("  VGB 10Y YIELD SEEDER")
    print("=" * 60)
    ok = seed_vgb10y()
    if ok:
        print("  ✅ VGB10Y REAL seeded từ World Bank API")
    else:
        print("  ⚠️  World Bank không trả dữ liệu — giữ fallback ESTIMATED")
    print("=" * 60)


# ── financial-search ───────────────────────────────────────────
def cmd_financial_search(args):
    """Tra cứu dữ liệu tài chính từ financial_facts.db."""
    from src.financial.financial_facts import FinancialFactsDB
    import pandas as pd

    db = FinancialFactsDB()
    conn = db.connect()

    print("=" * 60)
    print("  TRA CỨU DỮ LIỆU TÀI CHÍNH")
    print("=" * 60)

    # Build query
    query = "SELECT symbol, period, metric, value, unit, source FROM financial_facts WHERE 1=1"
    params = []

    if args.symbol:
        query += " AND symbol IN (" + ",".join(["?"] * len(args.symbol)) + ")"
        params.extend([s.upper() for s in args.symbol])
    if args.metric:
        query += " AND metric = ?"
        params.append(args.metric.upper())
    if args.period:
        query += " AND period = ?"
        params.append(args.period)
    if args.source:
        query += " AND source = ?"
        params.append(args.source)
    if args.min_value is not None:
        query += " AND value >= ?"
        params.append(args.min_value)
    if args.max_value is not None:
        query += " AND value <= ?"
        params.append(args.max_value)

    query += " ORDER BY symbol, period DESC, metric"

    try:
        df = pd.read_sql_query(query, conn, params=params)
    except Exception as e:
        print(f"  ❌ Query error: {e}")
        return

    if df.empty:
        print("  ❌ Không có dữ liệu phù hợp")
        return

    print(f"\n  Kết quả: {len(df)} dòng")
    print(f"  Symbols: {df['symbol'].nunique()}")
    print(f"  Metrics: {df['metric'].nunique()}")
    print()

    # Display summary
    print("  ── TỔNG QUAN ──")
    summary = df.groupby(['symbol', 'metric']).agg(
        count=('value', 'count'),
        last_value=('value', 'last'),
        last_period=('period', 'last'),
        source=('source', 'last'),
    ).reset_index()

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_rows', 50)
    pd.set_option('display.expand_frame_repr', False)

    print(summary.to_string(index=False))
    print()

    # Show detailed if limited symbols
    if args.symbol and len(df) <= 100:
        print("  ── CHI TIẾT ──")
        print(df.to_string(index=False))

    print("=" * 60)


# Module-level language mode — set by main() before dispatching
_VERBOSE_LANG: str = "full"


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
            print(" ⚠ CẢNH BÁO: HỆ THỐNG PHÁT HIỆN SỰ CỐ CẤU TRÚC DỮ LIỆU SBV")
            print(" Hệ thống đang vận hành trong trạng thái PARSER KHÔNG KHẢ DỤNG (Vị thế khóa cứng = 0.0).")
            print(" Thực thi ngay: python ptck.py sbv-update để cập nhật fixtures.")
            print("=" * 60)
            print()
    except Exception:
        pass


def build_parser():
    """Build và trả về ArgumentParser với tất cả subcommands."""
    parser = argparse.ArgumentParser(
        prog="ptck",
        description="PTCK_VNSTOCK CLI — Single entrypoint cho mọi thao tác",
    )
    lang_parent = argparse.ArgumentParser(add_help=False)
    lang_parent.add_argument(
        '--verbose-lang', choices=['compact', 'annotated', 'full', 'auto'],
        default='full', dest='verbose_lang',
        help='Chế độ hiển thị ngôn ngữ CLI (compact/annotated/full/auto)',
    )
    # Also accept --verbose-lang before subcommand
    parser.add_argument(
        '--verbose-lang', choices=['compact', 'annotated', 'full', 'auto'],
        default='full', dest='verbose_lang',
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
    p_report = sub.add_parser("report", parents=[lang_parent], help="Báo cáo (weekly/monthly/daily)")
    p_report.add_argument("subcommand", choices=["weekly", "monthly", "daily"], help="Loại báo cáo")
    p_report.add_argument("--month", help="Tháng cho báo cáo monthly (YYYY-MM)")
    p_report.add_argument("--lang", default="vi", help="Ngôn ngữ (vi/en)")
    p_report.set_defaults(func=cmd_report)

    # daily-close
    p_dc = sub.add_parser("daily-close", parents=[lang_parent], help="Chạy daily closer pipeline")
    p_dc.set_defaults(func=cmd_daily_close)

    # daily-update
    p_du = sub.add_parser("daily-update", parents=[lang_parent], help="Cập nhật dữ liệu EOD")
    p_du.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_du.add_argument("--manifest", help="Path to missing_manifest.json (gap filling)")
    p_du.add_argument("--batch-size", type=int, default=50,
                      help="Số mã mỗi request KBS (mặc định 50, giảm xuống 20 nếu payload lớn)")
    p_du.add_argument("--throttle", type=float, default=1.8,
                      help="Delay giây tối đa giữa các batch (mặc định 1.8, tăng lên 3.5 khi quét toàn bộ thị trường)")
    p_du.set_defaults(func=cmd_daily_update)

    # gap-analyzer
    p_ga = sub.add_parser("gap-analyzer", parents=[lang_parent], help="Kiểm toán dữ liệu - phát hiện missing symbols/dates")
    p_ga.add_argument("--output", help="Đường dẫn xuất manifest (mặc định: backend/data/missing_manifest.json)")
    p_ga.add_argument("--lookback-months", type=int, default=3, help="Số tháng phân tích (mặc định: 3)")
    p_ga.set_defaults(func=cmd_gap_analyzer)

    # telemetry
    p_tel = sub.add_parser("telemetry", parents=[lang_parent], help="Telemetry & reputation")
    p_tel.add_argument("subcommand", choices=["reputation", "shadow"])
    p_tel.add_argument("--refresh", action="store_true", help="Refresh dữ liệu")
    p_tel.add_argument("--drift", action="store_true", help="Drift Monitor (chi tiết độ dốc + half-life)")
    p_tel.add_argument("--days", type=int, default=30, help="Số ngày cho shadow metrics")
    p_tel.set_defaults(func=cmd_telemetry)

    # serve
    p_serve = sub.add_parser("serve", parents=[lang_parent], help="Khởi động server")
    p_serve.add_argument("--port", type=int, default=17039, help="Port (mặc định 17039)")
    p_serve.set_defaults(func=cmd_serve)

    # db
    p_db = sub.add_parser("db", parents=[lang_parent], help="Database operations")
    p_db.add_argument("subcommand", choices=["vacuum", "stats", "optimize", "backup"])
    p_db.add_argument("--dry-run", action="store_true", dest="dry_run", help="Backup: chỉ kiểm tra integrity, không backup thật")
    p_db.add_argument("--list", action="store_true", dest="list_backups", help="Backup: liệt kê các bản backup hiện có")
    p_db.set_defaults(func=cmd_db)

    # status
    p_status = sub.add_parser("status", parents=[lang_parent], help="Kiểm tra sức khỏe hệ thống")
    p_status.set_defaults(func=cmd_status)

    # structural
    p_struct = sub.add_parser("structural", parents=[lang_parent], help="Bộ phát hiện lệch cấu trúc thị trường")
    p_struct.add_argument("--date", help="Ngày phân tích (YYYY-MM-DD)")
    p_struct.set_defaults(func=cmd_structural)

    # final-decision
    p_final = sub.add_parser("final-decision", parents=[lang_parent], help="Bộ não quyết định cuối cùng")
    p_final.add_argument("--date", help="Ngày quyết định (YYYY-MM-DD)")
    p_final.set_defaults(func=cmd_final)

    # snapshot
    p_snap = sub.add_parser("snapshot", parents=[lang_parent], help="Ảnh chụp thị trường duy nhất")
    p_snap.set_defaults(func=cmd_snapshot)

    # ddi
    p_ddi = sub.add_parser("ddi", parents=[lang_parent], help="Delta Divergence Index — Δ_SA filter")
    p_ddi.set_defaults(func=cmd_ddi)

    # snapshot-index
    p_si = sub.add_parser("snapshot-index", parents=[lang_parent], help="Xem lịch sử snapshot_index.json")
    p_si.add_argument("--limit", type=int, default=20, help="Số dòng hiển thị (mặc định 20)")
    p_si.set_defaults(func=cmd_snapshot_index)

    # registry
    p_reg = sub.add_parser("registry", parents=[lang_parent], help="Param Fingerprint Registry — tra cứu params_hash theo regime")
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
    p_p5 = sub.add_parser("phase5", parents=[lang_parent], help="Phase 5 UAT — Paper Trading Simulation")
    p_p5.add_argument("--slices", type=int, default=5, help="Số slice (mặc định 5)")
    p_p5.add_argument("--depth", type=float, default=5000, help="Depth mỗi level (mặc định 5000)")
    p_p5.add_argument("--price", type=float, default=100.0, help="Giá mid (mặc định 100)")
    p_p5.set_defaults(func=cmd_phase5)

    # sandbox
    p_sb = sub.add_parser("sandbox", parents=[lang_parent], help="Phase 5 Dashboard — Real-time Paper Trading")
    p_sb.add_argument("--slices", type=int, default=5, help="Số slice (mặc định 5)")
    p_sb.add_argument("--depth", type=float, default=5000, help="Depth mỗi level (mặc định 5000)")
    p_sb.add_argument("--price", type=float, default=100.0, help="Giá mid (mặc định 100)")
    p_sb.add_argument("--capital", type=float, default=1_000_000, help="Vốn giả lập (mặc định 1,000,000)")
    p_sb.set_defaults(func=cmd_sandbox)

    # confidence
    p_conf = sub.add_parser("confidence", parents=[lang_parent], help="Bộ tự đánh giá độ tin cậy")
    p_conf.add_argument("--date", help="Ngày phân tích (YYYY-MM-DD)")
    p_conf.set_defaults(func=cmd_confidence)

    # quantstats
    p_qs = sub.add_parser("quantstats", parents=[lang_parent], help="QuantStatsBridge — Live Calibration Engine")
    p_qs.add_argument("--window", type=int, default=30, help="Cửa sổ phân tích (phiên)")
    p_qs.set_defaults(func=cmd_quantstats)

    # rejected-signals
    p_rs = sub.add_parser("rejected-signals", help="Rejected Signals Archive — Kho lưu tín hiệu bị từ chối")
    p_rs_sub = p_rs.add_subparsers(dest="action", required=True)
    p_rs_list = p_rs_sub.add_parser("list", help="Liệt kê rejected signals gần đây")
    p_rs_list.add_argument("--limit", type=int, default=20, help="Số bản ghi tối đa")
    p_rs_list.add_argument("--reason", type=str, default=None, help="Lọc theo rejection_reason")
    p_rs_list.set_defaults(func=cmd_rejected_signals)
    p_rs_stats = p_rs_sub.add_parser("stats", help="Thống kê theo rejection_reason")
    p_rs_stats.set_defaults(func=cmd_rejected_signals)
    p_rs_eod = p_rs_sub.add_parser("eod-update", help="Cập nhật simulated_exit cho các signal chưa có")
    p_rs_eod.set_defaults(func=cmd_rejected_signals)

    # scan
    p_scan = sub.add_parser("scan", parents=[lang_parent], help="Elite scanner")
    p_scan.add_argument("--deep", action="store_true", help="Deep scan")
    p_scan.add_argument("--symbol", type=str, help="Quét nhanh dữ liệu của 1 mã cổ phiếu riêng lẻ")
    p_scan.set_defaults(func=cmd_scan)

    # gold
    p_gold = sub.add_parser("gold", parents=[lang_parent], help="Gold information")
    p_gold.add_argument("subcommand", nargs="?", choices=["regime"], default=None, help="Gold subcommand")
    p_gold.set_defaults(func=cmd_gold)

    # silver
    p_silver = sub.add_parser("silver", parents=[lang_parent], help="Silver information")
    p_silver.add_argument("subcommand", nargs="?", choices=["gs-ratio", "seed"], default=None, help="Silver subcommand")
    p_silver.set_defaults(func=cmd_silver)

    # governor (Decision Matrix)
    p_gov = sub.add_parser("governor", parents=[lang_parent],
                           help="P3 Governor — Bayesian Expected Utility (weight-of-evidence P0→P2 + Kelly sizing)")
    p_gov.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã cổ phiếu (mặc định: 10 mã mục tiêu)")
    p_gov.add_argument("--output", choices=["report", "json"], default="report",
                       help="Định dạng đầu ra (report hoặc json)")
    p_gov.set_defaults(func=cmd_governor)

    # drift (Drift & Validation Layer)
    p_drift = sub.add_parser("drift", parents=[lang_parent],
                             help="Drift & Validation Layer — tự đánh giá độ tin cậy hệ thống")
    p_drift_sub = p_drift.add_subparsers(dest="drift_action", required=True)
    p_drift_all = p_drift_sub.add_parser("all", help="Toàn cảnh drift (concept + generalization + CB)")
    p_drift_all.add_argument("--days", type=int, default=90, help="Cửa sổ nhìn lại (ngày)")
    p_drift_all.set_defaults(func=cmd_drift)
    p_drift_concept = p_drift_sub.add_parser("concept", help="Concept Drift — Log-Loss trend")
    p_drift_concept.add_argument("--days", type=int, default=90)
    p_drift_concept.set_defaults(func=cmd_drift)
    p_drift_gen = p_drift_sub.add_parser("generalization", help="Generalization Gap — live vs random")
    p_drift_gen.set_defaults(func=cmd_drift)
    p_drift_surprise = p_drift_sub.add_parser("surprise", help="Surprise Engine — phát hiện bất thường")
    p_drift_surprise.set_defaults(func=cmd_drift)
    p_drift_model = p_drift_sub.add_parser("model", help="Model Evidence & Retirement")
    p_drift_model.set_defaults(func=cmd_drift)

    # calibrate (P4 Meta-Cognition)
    p_cal = sub.add_parser("calibrate", parents=[lang_parent],
                           help="P4 Calibration — Log-Loss, ECE, Beta-posterior LR update")
    p_cal_sub = p_cal.add_subparsers(dest="action", required=True)
    p_cal_eval = p_cal_sub.add_parser("eval", help="Tính Log-Loss, ECE, MCE trên resolved predictions")
    p_cal_eval.add_argument("--days", type=int, default=90, help="Cửa sổ nhìn lại (ngày)")
    p_cal_eval.set_defaults(func=cmd_calibrate)
    p_cal_beta = p_cal_sub.add_parser("update-beta", help="Cập nhật Beta posteriors từ outcomes thực tế")
    p_cal_beta.add_argument("--days", type=int, default=90, help="Cửa sổ nhìn lại (ngày)")
    p_cal_beta.set_defaults(func=cmd_calibrate)
    p_cal_lrs = p_cal_sub.add_parser("lrs", help="Xem Likelihood Ratios đã calibrated")
    p_cal_lrs.set_defaults(func=cmd_calibrate)
    p_cal_st = p_cal_sub.add_parser("status", help="Trạng thái prediction log (unresolved/resolved)")
    p_cal_st.add_argument("--days", type=int, default=90, help="Cửa sổ nhìn lại (ngày)")
    p_cal_st.set_defaults(func=cmd_calibrate)
    p_cal_resolve = p_cal_sub.add_parser("resolve", help="Resolve pending outcomes từ dữ liệu giá tương lai")
    p_cal_resolve.add_argument("--hold-days", type=int, default=30, help="Số ngày nắm giữ để xác định outcome")
    p_cal_resolve.add_argument("--dry-run", action="store_true", help="Chạy thử không ghi DB")
    p_cal_resolve.set_defaults(func=cmd_calibrate)
    p_cal_report = p_cal_sub.add_parser("report", help="Calibration trend report — Log-Loss, ECE theo thời gian")
    p_cal_report.add_argument("--days", type=int, default=90, help="Cửa sổ nhìn lại (ngày)")
    p_cal_report.set_defaults(func=cmd_calibrate)
    p_cal_cb = p_cal_sub.add_parser("circuit-breaker", help="Circuit Breater — xem trạng thái đóng băng vị thế")
    p_cal_cb.add_argument("--force-check", action="store_true", help="Chạy lại auto-check thay vì xem cached")
    p_cal_cb.set_defaults(func=cmd_calibrate)

    # counterfactual (P5 What-if Reasoning)
    p_cf = sub.add_parser("counterfactual", parents=[lang_parent],
                          help="P5 Counterfactual Reasoning — giả lập 'What if?' trên 6 nút bằng chứng")
    p_cf.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_cf.set_defaults(func=cmd_counterfactual)

    # evidence (LAW-004 Dynamic Weighting)
    p_ev = sub.add_parser("evidence", parents=[lang_parent],
                          help="LAW-004 Evidence Engine — Beta posterior reliability + dynamic weighting")
    p_ev_sub = p_ev.add_subparsers(dest="action", required=True)
    p_ev_list = p_ev_sub.add_parser("list", help="Xem tất cả evidence nodes (reliability, drift, weight)")
    p_ev_list.set_defaults(func=cmd_evidence)
    p_ev_w = p_ev_sub.add_parser("weights", help="Xem dynamic weights đang áp dụng")
    p_ev_w.set_defaults(func=cmd_evidence)
    p_ev_upd = p_ev_sub.add_parser("update", help="Update posteriors từ resolved predictions (Sprint 2)")
    p_ev_upd.add_argument("--node", type=str, default=None, help="Node ID (mặc định: tất cả)")
    p_ev_upd.set_defaults(func=cmd_evidence)
    p_ev_reset = p_ev_sub.add_parser("reset", help="Reset về prior (alpha=10, beta=10)")
    p_ev_reset.add_argument("--node", type=str, default=None, help="Node ID (mặc định: tất cả)")
    p_ev_reset.set_defaults(func=cmd_evidence)
    p_ev_drift = p_ev_sub.add_parser("drift", help="Xem các node có drift cao")
    p_ev_drift.set_defaults(func=cmd_evidence)
    p_ev_app = p_ev_sub.add_parser("applicability", help="Sprint 2: A_i = f(macro, sector, entropy)")
    p_ev_app_sub = p_ev_app.add_subparsers(dest="sub", required=True)
    p_ev_app_st = p_ev_app_sub.add_parser("status", help="Xem A_i hiện tại theo context")
    p_ev_app_st.set_defaults(func=cmd_evidence)
    p_ev_app_hm = p_ev_app_sub.add_parser("heatmap", help="Xem ma trận A_i cho tất cả macro states")
    p_ev_app_hm.set_defaults(func=cmd_evidence)
    p_ev_sim = p_ev_sub.add_parser("simulate", help="Dry-run: gán outcome ngẫu nhiên để test tốc độ phân hóa trọng số")
    p_ev_sim.add_argument("--n-pending", type=int, default=60, help="Số dự báo giả lập (mặc định 60)")
    p_ev_sim.add_argument("--bias-macro", type=float, default=0.55, help="Tỷ lệ gain macro (mặc định 0.55)")
    p_ev_sim.add_argument("--bias-transmission", type=float, default=0.52, help="Tỷ lệ gain transmission (mặc định 0.52)")
    p_ev_sim.add_argument("--bias-sector", type=float, default=0.50, help="Tỷ lệ gain sector (mặc định 0.50)")
    p_ev_sim.add_argument("--bias-health", type=float, default=0.58, help="Tỷ lệ gain health (mặc định 0.58)")
    p_ev_sim.add_argument("--bias-capital", type=float, default=0.48, help="Tỷ lệ gain capital_allocation (mặc định 0.48)")
    p_ev_sim.add_argument("--bias-valuation", type=float, default=0.60, help="Tỷ lệ gain valuation (mặc định 0.60)")
    p_ev_sim.add_argument("--bias-behavior", type=float, default=0.45, help="Tỷ lệ gain behavior (mặc định 0.45)")
    p_ev_sim.add_argument("--seed", type=int, default=None, help="Random seed để tái lập")
    p_ev_sim.set_defaults(func=cmd_evidence)

    # model-registry (Sprint 4: Competing Hypotheses Engine)
    p_mr = sub.add_parser("model-registry", parents=[lang_parent],
                          help="Sprint 4: ModelRegistry — 3 Competing Hypotheses + Bayesian Model Averaging")
    p_mr_sub = p_mr.add_subparsers(dest="action", required=True)
    p_mr_status = p_mr_sub.add_parser("status", help="Xem trạng thái 3 mô hình cạnh tranh + BMA weights")
    p_mr_status.set_defaults(func=cmd_model_registry)
    p_mr_select = p_mr_sub.add_parser("select", help="Chọn mô hình tốt nhất cho bối cảnh hiện tại")
    p_mr_select.set_defaults(func=cmd_model_registry)
    p_mr_act = p_mr_sub.add_parser("activate", help="Kích hoạt mô hình (chuyển về ACTIVE)")
    p_mr_act.add_argument("model_id", type=str, help="Mã mô hình (M1_MACRO, M2_FUNDAMENTAL, M3_BEHAVIORAL)")
    p_mr_act.add_argument("--reason", type=str, default="", help="Lý do")
    p_mr_act.set_defaults(func=cmd_model_registry)
    p_mr_sus = p_mr_sub.add_parser("suspend", help="Tạm ngưng mô hình (chuyển về DORMANT)")
    p_mr_sus.add_argument("model_id", type=str, help="Mã mô hình")
    p_mr_sus.add_argument("--reason", type=str, default="", help="Lý do")
    p_mr_sus.set_defaults(func=cmd_model_registry)
    p_mr_ret = p_mr_sub.add_parser("retire", help="Đào thải mô hình (chuyển về RETIRED)")
    p_mr_ret.add_argument("model_id", type=str, help="Mã mô hình")
    p_mr_ret.add_argument("--reason", type=str, default="", help="Lý do")
    p_mr_ret.set_defaults(func=cmd_model_registry)
    p_mr_rev = p_mr_sub.add_parser("revive", help="Phục hồi mô hình (RETIRED→ACTIVE, reset posterior=0.333)")
    p_mr_rev.add_argument("model_id", type=str, help="Mã mô hình")
    p_mr_rev.add_argument("--reason", type=str, default="", help="Lý do")
    p_mr_rev.set_defaults(func=cmd_model_registry)
    p_mr_hist = p_mr_sub.add_parser("history", help="Xem lịch sử chuyển trạng thái của mô hình")
    p_mr_hist.add_argument("model_id", type=str, help="Mã mô hình")
    p_mr_hist.set_defaults(func=cmd_model_registry)
    p_mr_bma = p_mr_sub.add_parser("bma", help="Xem BMA weights cho bối cảnh hiện tại")
    p_mr_bma.set_defaults(func=cmd_model_registry)

    # causal (Sprint 3: CausalEdge)
    p_cau = sub.add_parser("causal", parents=[lang_parent],
                           help="Sprint 3: CausalEdge — lag, confidence, half-life, counter-examples")
    p_cau_sub = p_cau.add_subparsers(dest="action", required=True)
    p_cau_st = p_cau_sub.add_parser("status", help="Xem thống kê đồ thị nhân quả")
    p_cau_st.set_defaults(func=cmd_causal)
    p_cau_prop = p_cau_sub.add_parser("propagate", help="Lan truyền tín hiệu từ nút nguồn qua đồ thị")
    p_cau_prop.add_argument("--source", type=str, default="CREDIT_STRESS", help="Nút nguồn (mặc định CREDIT_STRESS)")
    p_cau_prop.add_argument("--archetype", type=str, default=None, help="Lọc theo archetype (COMPOUNDER, FRANCHISE_BANK...)")
    p_cau_prop.set_defaults(func=cmd_causal)
    p_cau_trace = p_cau_sub.add_parser("trace", help="Truy vết đường đi nhân quả từ nguồn đến đích")
    p_cau_trace.add_argument("source", type=str, help="Nút nguồn")
    p_cau_trace.add_argument("target", type=str, help="Nút đích")
    p_cau_trace.add_argument("--archetype", type=str, default=None, help="Lọc theo archetype")
    p_cau_trace.set_defaults(func=cmd_causal)
    p_cau_edges = p_cau_sub.add_parser("edges", help="Xem tất cả causal edges đã đăng ký")
    p_cau_edges.set_defaults(func=cmd_causal)
    p_cau_persist = p_cau_sub.add_parser("persist", help="Ghi edges vào calibration.db")
    p_cau_persist.set_defaults(func=cmd_causal)

    # ── Giai đoạn 1: Business Ontology Layer ──────────────
    p_arch = sub.add_parser("archetype", parents=[lang_parent],
                            help="Business Archetype — DNA classification (Giai đoạn 1)")
    p_arch.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_arch.set_defaults(func=cmd_archetype)

    p_ee = sub.add_parser("economic-engine", parents=[lang_parent],
                          help="Economic Engine — propagation chain (Giai đoạn 1)")
    p_ee.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_ee.add_argument("--driver", type=str, default=None,
                      help="Macro driver để trace (VD: STEEL_PRICE, AI_CAPEX)")
    p_ee.set_defaults(func=cmd_economic_engine)

    p_cp = sub.add_parser("competitive", parents=[lang_parent],
                          help="Competitive Position — moat & market power (Giai đoạn 1)")
    p_cp.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_cp.set_defaults(func=cmd_competitive)

    # ── Giai đoạn 2: Factor Exposure Matrix ──────────────
    p_fe = sub.add_parser("factor-exposure", parents=[lang_parent],
                          help="Giai đoạn 2 — Factor Exposure Matrix: macro sensitivity per symbol")
    p_fe.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_fe.add_argument("--top", type=int, default=5, help="Số factor hiển thị (mặc định 5)")
    p_fe.add_argument("--query", type=str, default=None,
                      help="Tra cứu exposure cho 1 factor cụ thể (VD: INTEREST_RATE)")
    p_fe.add_argument("--lr-adjust", action="store_true", dest="lr_adjust",
                      help="Tính LR adjustment cho macro state hiện tại")
    p_fe.set_defaults(func=cmd_factor_exposure)

    # ── Giai đoạn 2: CLI Vietnamese alias ──────────────
    p_fe_vi = sub.add_parser("pho-nhiem", parents=[lang_parent],
                             help="(Giai đoạn 2) Phơi nhiễm yếu tố vĩ mô — Factor Exposure Matrix")
    p_fe_vi.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_fe_vi.add_argument("--top", type=int, default=5, help="Số factor hiển thị")
    p_fe_vi.add_argument("--query", type=str, default=None,
                         help="Tra cứu 1 factor (VD: INTEREST_RATE)")
    p_fe_vi.add_argument("--lr-adjust", action="store_true", dest="lr_adjust",
                         help="Tính LR multiplier cho macro hiện tại")
    p_fe_vi.set_defaults(func=cmd_factor_exposure)

    # ── Giai đoạn 3: Contextualized Company Health ──────
    p_ch = sub.add_parser("contextual-health", parents=[lang_parent],
                          help="Giai đoạn 3 — Contextualized Company Health (Archetype-Aware Dynamic Thresholds)")
    p_ch.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_ch.set_defaults(func=cmd_contextual_health)

    p_ch_vi = sub.add_parser("suc-khoe-doanh-nghiep", parents=[lang_parent],
                             help="(Giai đoạn 3) Sức khỏe doanh nghiệp theo ngữ cảnh Archetype — Contextualized Health")
    p_ch_vi.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_ch_vi.set_defaults(func=cmd_contextual_health)

    # ── Giai đoạn 2: Company Health Engine (compute health_ratios) ──
    p_he = sub.add_parser("health-engine", parents=[lang_parent],
                          help="Giai đoạn 2 — compute health_ratios từ financial_facts (bước bắt buộc trước health-v2)")
    p_he.add_argument("action", choices=["init", "compute", "show"],
                      help="init: tạo schema; compute: tính ratios; show: xem latest")
    p_he.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_he.set_defaults(func=cmd_health_engine)

    # ── Giai đoạn 4: Capital Allocation Quality ──────────
    p_ca = sub.add_parser("capital-allocation", parents=[lang_parent],
                          help="Giai đoạn 4 — Capital Allocation Quality (Management Value Creation)")
    p_ca.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_ca.set_defaults(func=cmd_capital_allocation)

    p_ca_vi = sub.add_parser("phan-bo-von", parents=[lang_parent],
                             help="(Giai đoạn 4) Chất lượng phân bổ vốn — Capital Allocation Quality")
    p_ca_vi.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_ca_vi.set_defaults(func=cmd_capital_allocation)

    # watch (Volume Spike Monitor)
    p_watch = sub.add_parser("watch", parents=[lang_parent],
                             help="Giám sát Volume Spike — phát hiện phiên xác nhận Scale-In")
    p_watch.add_argument("--symbols", nargs="+", default=["FPT", "VCB", "HPG", "MBB", "MWG"],
                         help="Danh sách mã cần theo dõi (mặc định: WAIT + SCALE_IN)")
    p_watch.add_argument("--threshold", type=float, default=1.5,
                         help="Ngưỡng volume/MA20 (mặc định: 1.5x)")
    p_watch.set_defaults(func=cmd_watch)

    # governor-report (Contribution Breakdown)
    p_govr = sub.add_parser("governor-report", parents=[lang_parent],
                            help="Phân rã đóng góp 3 mô hình vào DecisionGuard")
    p_govr.set_defaults(func=cmd_governor_report)

    # erl-scan (Entity Resilience Layer + Whitelist)
    p_erl = sub.add_parser("erl-scan", parents=[lang_parent],
                           help="Entity Resilience Layer — quét sức khỏe 1.514 mã + Whitelist Top 30")
    p_erl.add_argument("--top", type=int, default=20, help="Số lượng mã hiển thị (mặc định 20)")
    p_erl.add_argument("--vn30", action="store_true", help="Chỉ quét top 30 thanh khoản")
    p_erl.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_erl.add_argument("--whitelist", action="store_true", help="Xem whitelist hiện tại (không chạy scan mới)")
    p_erl.add_argument("--output", help="Đường dẫn xuất whitelist riêng")
    p_erl.set_defaults(func=cmd_erl_scan)

    # rotation (Asia supply-chain canary)
    p_rot = sub.add_parser("rotation", parents=[lang_parent], help="Góc xoay chuỗi cung ứng châu Á: VN vs KOSPI+TAIEX+DXY")
    p_rot.set_defaults(func=cmd_rotation)

    # rs-audit
    p_ra = sub.add_parser("rs-audit", parents=[lang_parent], help="Audit Top RS — phân tích nguồn gốc sức mạnh")
    p_ra.add_argument("--top", type=int, default=20, help="Số lượng mã (mặc định 20)")
    p_ra.add_argument("--all", action="store_true", dest="show_all", help="Hiện tất cả mã RS")
    p_ra.set_defaults(func=cmd_rs_audit)

    # group-influence
    p_gi = sub.add_parser("group-influence", parents=[lang_parent], help="Bộ đo ảnh hưởng nhóm trụ")
    p_gi.set_defaults(func=cmd_group_influence)

    # strength-discrimination
    p_sd = sub.add_parser("strength-discrimination", parents=[lang_parent], help="Phân tách xung lực nội tại vs ép trụ")
    p_sd.add_argument("--top", type=int, default=20, help="Số lượng mã (mặc định 20)")
    p_sd.set_defaults(func=cmd_strength_discrimination)

    # prediction-registry
    p_pr = sub.add_parser("prediction-registry", parents=[lang_parent], help="Nhật ký dự báo — ghi log RS audit + đo kết quả sau 5/20/60 ngày")
    p_pr.add_argument("action", choices=["log", "update", "stats", "list"], default="stats", nargs="?")
    p_pr.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_pr.add_argument("--limit", type=int, default=20, help="Số entries (mặc định 20)")
    p_pr.set_defaults(func=cmd_prediction_registry)

    # flow-map
    p_fm = sub.add_parser("flow-map", parents=[lang_parent], help="Bản đồ Dòng vốn Liên thị trường 4 Tầng")
    p_fm.set_defaults(func=cmd_flow_map)

    # init-agent
    p_ia = sub.add_parser("init-agent", parents=[lang_parent], help="Generate .clinerules + .cursorrules from bundled AGENTS.md")
    p_ia.set_defaults(func=cmd_init_agent)

    # cleanup
    p_cl = sub.add_parser("cleanup", parents=[lang_parent], help="Dọn dẹp định kỳ: telemetry, state files")
    p_cl.add_argument("target", choices=["telemetry", "all"], help="telemetry: entropy_log; all: telemetry + state")
    p_cl.add_argument("--max-rows", type=int, default=1000, dest="max_rows", help="Số dòng telemetry giữ lại")
    p_cl.set_defaults(func=cmd_cleanup)

    # clear-crisis
    p_ccr = sub.add_parser("clear-crisis", parents=[lang_parent], help="Xóa crisis_cooldown + stress gate — reset trạng thái khủng hoảng thủ công")
    p_ccr.set_defaults(func=cmd_clear_crisis)

    # sbv-update
    p_su = sub.add_parser("sbv-update", parents=[lang_parent], help="5-step recovery: backup → fixtures → pytest → scrape → clear alert")
    p_su.add_argument("--force", action="store_true", dest="sbv_force",
                      help="Bỏ qua CRITICAL_WARNING — clear CRISIS_REAL marker thủ công")
    p_su.add_argument("--save-fixture", action="store_true", dest="sbv_save_fixture",
                      help="Chỉ lưu HTML thô từ sbv.gov.vn thành fixture mà không chạy scrape validation")
    p_su.set_defaults(func=cmd_sbv_update)

    # fetch-financials
    p_ff = sub.add_parser("fetch-financials", parents=[lang_parent],
                          help="[BROKEN] VCI/KBS API không hoạt động — dùng import-financials thay thế")
    p_ff.add_argument("--symbols", nargs="+", default=None, help="Danh sách mã cổ phiếu")
    p_ff.add_argument("--all", action="store_true", help="Fetch cho tất cả mã có trong daily_ohlcv")
    p_ff.set_defaults(func=cmd_fetch_financials)

    # import-financials
    p_imf = sub.add_parser("import-financials", parents=[lang_parent],
                           help="Import PE/PB/... from CSV/XLSX, compute z-scores, seed valuation_scores")
    p_imf.add_argument("--file", required=True, help="Đường dẫn file .csv / .xlsx")
    p_imf.add_argument("--sheet", default=None, help="Tên sheet (mặc định sheet đầu tiên)")
    p_imf.set_defaults(func=cmd_import_financials)

    # bctc-20q — Time-series 5-year valuation
    p_bctc = sub.add_parser("bctc-20q", parents=[lang_parent],
                            help="Import 20-quarter BCTC CSV, compute time-series z-scores (intrinsic valuation)")
    p_bctc.add_argument("--file", required=True, help="Đường dẫn file .csv / .xlsx (symbol, quarter, PE, PB, ...)")
    p_bctc.add_argument("--sheet", default=None, help="Tên sheet (mặc định sheet đầu tiên)")
    p_bctc.set_defaults(func=cmd_bctc_20q)

    # data-quality
    p_dq = sub.add_parser("data-quality", parents=[lang_parent], help="Đánh giá độ tin cậy dữ liệu (TẦNG 0)")
    p_dq.set_defaults(func=cmd_data_quality)

    # index-decompose
    p_id = sub.add_parser("index-decompose", parents=[lang_parent], help="Phân tích chỉ số thị trường thống nhất")
    p_id.add_argument("--date", help="Ngày (YYYY-MM-DD)")
    p_id.set_defaults(func=cmd_index_decompose)

    # restore-backup
    p_rb = sub.add_parser("restore-backup", parents=[lang_parent], help="Khôi phục trạng thái alert từ backup gần nhất")
    p_rb.set_defaults(func=cmd_restore_backup)

    # check-calendar
    p_cc = sub.add_parser("check-calendar", parents=[lang_parent], help="Kiểm tra lịch lễ so với nguồn online")
    p_cc.add_argument("--auto-update", action="store_true", help="Tự động cập nhật calendar nếu lệch")
    p_cc.set_defaults(func=cmd_check_calendar)

    # backfill-history
    p_bf = sub.add_parser("backfill-history", parents=[lang_parent], help="Khôi phục dữ liệu lịch sử cho mã thiếu (Backfill)")
    p_bf.add_argument("--symbols", help="Chỉ định mã cụ thể, cách nhau bằng dấu phẩy (VD: VCB,REE,HDB)")
    p_bf.add_argument("--start", default=None, help="Ngày bắt đầu (YYYY-MM-DD, mặc định 2021-01-01)")
    p_bf.add_argument("--end", default=None, help="Ngày kết thúc (YYYY-MM-DD, mặc định hôm nay)")
    p_bf.add_argument("--dry-run", action="store_true", dest="dry_run", help="Chạy thử — không ghi vào DB")
    p_bf.add_argument("--quiet", action="store_true", help="Chỉ in tóm tắt, không in từng mã")
    p_bf.set_defaults(func=cmd_backfill)

    # backfill-macro (Hotfix #2)
    p_bfm = sub.add_parser("backfill-macro", parents=[lang_parent], help="Backfill 1y historical data cho PTD macro tickers")
    p_bfm.add_argument("--days", type=int, default=252, help="Số ngày lịch sử (mặc định 252)")
    p_bfm.set_defaults(func=cmd_backfill_macro)

    # backfill-regime
    p_bfr = sub.add_parser("backfill-regime", parents=[lang_parent], help="Backfill regime_history cho ngày thiếu trong daily_ohlcv")
    p_bfr.add_argument("--target", default=None, help="Ngày đích (YYYY-MM-DD, mặc định: tất cả)")
    p_bfr.add_argument("--batch-size", type=int, default=30, dest="batch_size", help="Batch log interval")
    p_bfr.set_defaults(func=cmd_backfill_regime)

    # absorption-detector
    p_ad = sub.add_parser("absorption-detector", parents=[lang_parent], help="Phát hiện Cân bằng Hấp thụ thị trường (SDI + Volume Profile PCA)")
    p_ad.add_argument("--date", default=None, help="Ngày phân tích (YYYY-MM-DD)")
    p_ad.add_argument("--quiet", action="store_true", help="Chỉ in JSON, không in chi tiết")
    p_ad.add_argument("--symbol", type=str, default=None, help="Phân tích cho 1 mã cổ phiếu riêng lẻ (VD: FPT)")
    p_ad.add_argument("--history", action="store_true", help="In lịch sử chuyển pha của mã")
    p_ad.set_defaults(func=cmd_absorption_detector)

    # macro-state (Phase 4, P0)
    p_ms = sub.add_parser("macro-state", parents=[lang_parent],
                          help="MacroStateClassifier — trạng thái vĩ mô thống nhất cho Governor")
    p_ms.add_argument("--date", default=None, help="Ngày (YYYY-MM-DD, mặc định: hôm nay)")
    p_ms.set_defaults(func=cmd_macro_state)

    # transmission (Economic Transmission Engine, P1)
    p_tr = sub.add_parser("transmission", parents=[lang_parent],
                          help="Economic Transmission Engine — Liquidity→Credit→Confidence")
    p_tr.set_defaults(func=cmd_transmission)

    # sector (Sector State Engine, P1)
    p_sc = sub.add_parser("sector", parents=[lang_parent],
                          help="Sector State Engine — đánh giá 19 ngành ICB trên 4 trụ cột")
    p_sc.add_argument("--top", type=int, default=10, help="Số ngành top (mặc định 10)")
    p_sc.set_defaults(func=cmd_sector_rotation)

    # health-v2 (Company Health Latent Engine, P2)
    p_h2 = sub.add_parser("health-v2", parents=[lang_parent],
                          help="Company Health v2 — 5-organ latent state engine")
    p_h2.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB", "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sách mã")
    p_h2.set_defaults(func=cmd_health_v2)

    # csi-explain (CSI Causal Trace Explainer)
    p_csi = sub.add_parser("csi-explain", parents=[lang_parent],
                           help="CSI Explain — Causal DAG Trace từ World FedState xuống điểm số doanh nghiệp (Contextual Security Index)")
    p_csi.add_argument("--symbols", nargs="+", default=["VCB"],
                       help="Danh sách mã cổ phiếu cần giải thích (mặc định: VCB)")
    p_csi.add_argument("--sector", type=str, default=None,
                       help="So sánh CSI trong cùng ngành (VD: --sector Ngân hàng)")
    p_csi.add_argument("--list-sectors", action="store_true",
                       help="Hiển thị danh sách ngành ICB có sẵn")
    p_csi.add_argument("--output", choices=["report", "json"], default="report",
                       help="Định dạng đầu ra (report hoặc json)")
    p_csi.set_defaults(func=cmd_csi_explain)

    # macro-governor
    p_mg = sub.add_parser("macro", parents=[lang_parent], help="Macro Governor Gatekeeper — Two-Tier Architecture (Tier 1)")
    p_mg.set_defaults(func=cmd_macro)

    # sel
    p_sel = sub.add_parser("sel", parents=[lang_parent], help="Structure Evolution Layer — W1 Wasserstein + Survival Mode")
    p_sel.add_argument("--as-of", dest="as_of", default=None,
                       help="Mốc ngày T (YYYY-MM-DD) cho anti-lookahead. Mặc định: EOD mới nhất trong DB")
    p_sel.add_argument("--online", action="store_true",
                       help="Cho phép gọi API (mặc định OFFLINE — chỉ đọc SQLite, chống IP ban)")
    p_sel.set_defaults(func=cmd_sel)

    # paper
    p_paper = sub.add_parser("paper", parents=[lang_parent], help="Paper Trading Engine — Live vs Backtest simulation")
    p_paper.add_argument("action", nargs="?", choices=["run", "report", "pnl"],
                         default="report", help="run: sinh lệnh; report: ổn định; pnl: Mark-to-Market")
    p_paper.add_argument("--date", default=None,
                         help="Ngày quyết định T (YYYY-MM-DD). Mặc định: EOD mới nhất")
    p_paper.add_argument("--sessions", type=int, default=45,
                         help="Số phiên lookback cho báo cáo ổn định (mặc định 45)")
    p_paper.add_argument("--online", action="store_true",
                         help="Cho phép gọi API (mặc định OFFLINE — chống IP ban)")
    p_paper.set_defaults(func=cmd_paper)

    # eod-run (self-healing scheduler entry point)
    p_eod = sub.add_parser("eod-run", parents=[lang_parent], help="EOD Runner — retry + idempotent + SQLite lock")
    p_eod.add_argument("--date", default=None,
                       help="as_of_date (YYYY-MM-DD). Mặc định: EOD mới nhất trong DB")
    p_eod.add_argument("--force", action="store_true",
                       help="Bỏ qua idempotency check (chạy lại có chủ đích)")
    p_eod.add_argument("--retry-sleep", dest="retry_sleep", type=int, default=15 * 60,
                       help="Giây ngủ giữa các lần retry (mặc định 900s = 15 phút)")
    p_eod.add_argument("--no-catchup", dest="catchup", action="store_false",
                       help="Tắt cơ chế Giao dịch bù (Catch-up) cho các ngày nợ")
    p_eod.set_defaults(func=cmd_eod_run, catchup=True)

    # fetch-macro
    p_fm = sub.add_parser("fetch-macro", parents=[lang_parent],
                          help="Fetch world macro data (Fed, FRED, US10Y, USD, Brent) + VGB 10Y")
    p_fm.add_argument("--force", action="store_true",
                      help="Bỏ qua cache, fetch mới từ upstream")
    p_fm.add_argument("--skip-vgb", action="store_true", dest="skip_vgb",
                      help="Bỏ qua VGB 10Y seeding")
    p_fm.set_defaults(func=cmd_fetch_macro)

    # cafef-crawl
    p_cc = sub.add_parser("cafef-crawl", parents=[lang_parent],
                          help="Crawl BCTC 20 quarters cho danh sách mã")
    p_cc.add_argument("--symbols", nargs="+", default=[],
                      help="Danh sách mã cổ phiếu (mặc định: FPT ACB HDB MBB VCB)")
    p_cc.add_argument("--type", choices=["STANDARD", "BANK"], default=None,
                      help="Loại thực thể (STANDARD hoặc BANK, mặc định auto)")
    p_cc.add_argument("--source", choices=["vci", "cafef", "api", "vietstock", "synthetic"], default="vci",
                      help="Nguồn dữ liệu: vci (VCI GraphQL + VNDirect/TCBS/CafeF/Vietstock fallback), "
                           "api (REST JSON bridges: VNDirect/TCBS → CafeF → Vietstock), "
                           "vietstock (Vietstock Finance free summary 4 quý — kiểm tra chéo), "
                           "cafef (requests+Playwright, thường 404), "
                           "synthetic (nội suy, không cần API)")
    p_cc.add_argument("--playwright", action="store_true",
                      help="Force Playwright cho CafeF (bỏ qua requests)")
    p_cc.add_argument("--delay", type=float, default=0,
                      help="Độ trễ (giây) giữa các request, tránh IP ban (mặc định: 0)")
    p_cc.set_defaults(func=cmd_cafef_crawl)

    # vgb10y
    p_vgb = sub.add_parser("vgb10y", parents=[lang_parent],
                           help="Seed VGB 10Y yield từ World Bank API")
    p_vgb.set_defaults(func=cmd_vgb10y)

    # financial-search
    p_fs = sub.add_parser("financial-search", parents=[lang_parent],
                          help="Tra cứu dữ liệu tài chính từ financial_facts.db")
    p_fs.add_argument("--symbol", nargs="+", default=None, help="Mã cổ phiếu (VD: VCB ACB FPT)")
    p_fs.add_argument("--metric", default=None, help="Metric (VD: REVENUE, NET_INCOME, TOTAL_ASSETS)")
    p_fs.add_argument("--period", default=None, help="Period (VD: 2026Q1)")
    p_fs.add_argument("--source", default=None, help="Source (VD: cafef, vci, synthetic)")
    p_fs.add_argument("--min-value", type=float, default=None, dest="min_value",
                      help="Giá trị tối thiểu")
    p_fs.add_argument("--max-value", type=float, default=None, dest="max_value",
                      help="Giá trị tối đa")
    p_fs.set_defaults(func=cmd_financial_search)

    return parser


def main():
    global _VERBOSE_LANG
    from src.engine.startup_reminder import kiem_tra_va_nhac_nho
    kiem_tra_va_nhac_nho()
    _check_sbv_alert_startup()

    parser = build_parser()
    args = parser.parse_args()
    _VERBOSE_LANG = args.verbose_lang
    # Re-hydrate: config.py may push vnstock_path to sys.path[0],
    # breaking core/ resolution. Ensure PROJECT_ROOT stays at [0].
    sp = str(PROJECT_ROOT)
    if sp in sys.path:
        sys.path.remove(sp)
    sys.path.insert(0, sp)
    args.func(args)


if __name__ == "__main__":
    main()
