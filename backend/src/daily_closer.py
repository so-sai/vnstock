import sys
from datetime import datetime
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
from uuid import uuid4

import src.config
from src.core.presentation.vi_localizer import render_cognitive_journal
from src.daily_updater import run_daily_update
from src.database.db_core import optimize_sqlite_engine
from src.database.timeline_manager import get_regime_history, log_regime_state
from src.engine.decision_engine import merge_decisions
from src.engine.sentinel_alert import evaluate_sentinel_status
from src.telemetry.recorder import record_decision


def create_markdown_report(verdict, target_date):
    """Lưu nhật ký tác chiến (War Journal) dưới dạng Markdown"""
    report_dir = src.config.DATA_DIR / "reports"
    if not report_dir.exists():
        report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / f"{target_date}_verdict.md"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 🛡️ NHẬT KÝ TÁC CHIẾN - {target_date}\n\n")
        f.write(f"**Thời gian thực thi:** {datetime.now().strftime('%H:%M:%S')}\n")

        # Section 1: Institutional Decision
        if "decision" in verdict:
            d = verdict["decision"]
            f.write("## 🏛️ PHÁN QUYẾT BỘ CHỈ HUY (THE BOARDROOM)\n\n")
            f.write(
                f"- **TRẠNG THÁI THỊ TRƯỜNG:** `{d.get('market_status', 'N/A')}` (Score: {d.get('regime_score', 'N/A')})\n"
            )
            f.write(f"- **MÔ HÌNH ƯU TIÊN:** `{d.get('active_model', 'N/A')}`\n")
            f.write(f"- **PHÁN QUYẾT CUỐI CÙNG:** **{d.get('consensus', 'N/A')}**\n")
            conf = d.get("confidence", 0)
            try:
                conf_txt = f"{float(conf) * 100}%"
            except TypeError, ValueError:
                conf_txt = str(conf)
            f.write(f"- **ĐỘ TIN CẬY (CONFIDENCE):** `{conf_txt}`\n\n")

            if d.get("model_b", {}).get("top_picks"):
                f.write("### 🎯 Danh sách Quan tâm (Mean Reversion Selection)\n")
                for pick in d["model_b"]["top_picks"]:
                    z = pick.get("z_score", pick.get("distance_from_ma50", "N/A"))
                    rsi = pick.get("rsi", "N/A")
                    f.write(f"- {pick.get('symbol', '?')} (Z-Score: {z}, RSI: {rsi})\n")
                f.write("\n")

        # Section 2: Decision Trajectory (Lighthouse)
        f.write("## 📡 QUỸ ĐẠO QUYẾT ĐỊNH (THE LIGHTHOUSE)\n\n")
        history = get_regime_history(limit=5)
        if not history.empty:
            f.write("| Ngày | Score | Vị thế | Gia tốc |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")
            for _, h in history.iterrows():
                f.write(f"| {h['date']} | {h['regime_score']} | `{h['status']}` | {h['breadth_velocity']:+.1f}% |\n")
            f.write("\n")

        # Section 3: Ignition Switch (Recovery)
        if "decision" in verdict:
            rec = verdict["decision"].get("recovery") or {}
            details = rec.get("details") or {}
            f.write("## 🚀 BỘ ĐÁNH LỬA (IGNITION SWITCH)\n\n")
            vel = details.get("velocity_5d", 0.0)
            try:
                vel_txt = f"{float(vel):+.1f}%"
            except TypeError, ValueError:
                vel_txt = str(vel)
            f.write(f"- **TRẠNG THÁI PHỤC HỒI:** `{rec.get('status', 'N/A')}`\n")
            f.write(f"- **GIA TỐC ĐỘ RỘNG (5D):** `{vel_txt}` (Ngưỡng: +15%)\n")
            f.write(f"- **XÁC NHẬN MA10:** `{'YES' if rec.get('ma10_reclaim') else 'NO'}`\n\n")

        # Section 4: Sentinel Details
        f.write("## 🔍 Chi tiết Trạng thái Sentinel (Model A)\n\n")
        l1 = verdict.get("layer1_mom_expansion") or {}
        l2 = verdict.get("layer2_nh10_consistency") or {}
        l3 = verdict.get("layer3_foreign_absorption") or {}
        f.write(
            f"- **Momentum Expansion (Lớp 1):** {l1.get('status', 'N/A')} ({l1.get('value', 'N/A')}/{l1.get('threshold', 'N/A')})\n"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        )
        f.write(
            f"- **NH10 Consistency (Lớp 2):** {l2.get('status', 'N/A')} ({l2.get('value', 'N/A')}/{l2.get('threshold', 'N/A')} ngày)\n"  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
        )
        f.write(f"- **Foreign Absorption (Lớp 3):** {l3.get('status', 'N/A')}\n\n")

        # Section 5: Cognitive journal (Vietnamese, from vi_localizer)
        if "decision" in verdict:
            f.write("## 🧠 NHẬT KÝ NHẬN THỨC HỆ THỐNG\n\n")
            f.write("```\n")
            try:
                f.write(render_cognitive_journal(verdict["decision"]))
            except (KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError) as e:
                f.write(f"(cognitive journal unavailable: {e})")
            f.write("\n```\n\n")

        f.write("---\n")
        f.write("*Bản báo cáo này được tạo tự động bởi PTCK_VNSTOCK Multi-Model Decision Stack.*")

    return report_path


def _check_data_freshness() -> None:
    """Canh bao neu du lieu EOD qua han >24h (fail-safe: chi log, khong chan)."""
    try:
        import sqlite3
        from datetime import datetime

        db = src.config.DATA_DIR / "screener_cache.db"
        if not db.exists():
            print("  [WARNING] screener_cache.db khong ton tai — ket qua co the rong.")
            return
        con = sqlite3.connect(str(db))
        row = con.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
        con.close()
        if not row or not row[0]:
            print("  [WARNING] daily_ohlcv rong — ket qua co the rong.")
            return
        age_h = (datetime.now() - datetime.strptime(row[0], "%Y-%m-%d")).total_seconds() / 3600
        if age_h > 24:
            print(
                f"  [WARNING] Du lieu EOD cu: max(date)={row[0]} ({age_h:.0f}h truoc) — quyet dinh dua tren du lieu hien huu."
            )
        else:
            print(f"  Du lieu EOD tuoi: max(date)={row[0]} ({age_h:.1f}h).")
    except Exception as e:  # noqa: BLE001 - freshness check best-effort
        print(f"  [WARNING] Khong kiem tra duoc do tuoi du lieu: {e}")


def run_daily_closer(skip_sync: bool = False):
    """Quy trình đóng phiên tự động (The Dragon Shield Automation).

    skip_sync=True: bo qua Step 1 crawl mang, chi chay pipeline noi bo
    (mac dinh False — giu hanh vi cu de tuong thich nguoc).
    """
    target_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'=' * 60}")
    print(f"🐉 THE DRAGON SHIELD: DAILY CLOSER - {target_date}")
    # Step 0: Optimize/Init DB
    optimize_sqlite_engine()

    # Step 1: Sync Data (co the bo qua khi --skip-sync)
    if skip_sync:
        print("[INFO] Bo qua crawl du lieu mang (--skip-sync). Chay pipeline dong nen noi bo tren du lieu hien huu.")
        _check_data_freshness()
    else:
        run_daily_update(target_date)

    # Step 2: Run Sentinel (Model A)
    verdict = evaluate_sentinel_status()

    # Step 3: Run Decision Engine (Consensus)
    decision = merge_decisions(verdict)
    verdict["decision"] = decision

    # Step 3.1: Run Structural Detector
    try:
        from src.engine.structural_detector import detect_cau_truc

        struct = detect_cau_truc(target_date)
        print(f"  Cấu trúc: {struct.get('trang_thai', 'N/A')} ({struct.get('so_tru_ok', 0)}/3)")
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step EOD lỗi không dừng pipeline chính
        print(f"⚠️  Structural detector skipped: {e}")

    # Step 3.2: Run Final Orchestrator
    try:
        from src.engine.orchestrator import quyet_dinh_cuoi

        final = quyet_dinh_cuoi(target_date)
        print(f"  {final.get('quyet_dinh', 'N/A')} — {', '.join(final.get('ly_do', []))}")
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step EOD lỗi không dừng pipeline chính
        print(f"⚠️  Orchestrator skipped: {e}")

    # Step 3.3: Log to Timeline
    log_regime_state(decision)

    # Step 4: Record Decision Snapshot (Telemetry)
    try:
        _adapt_and_record_decision(decision)
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step EOD lỗi không dừng pipeline chính
        print(f"⚠️  Telemetry snapshot skipped: {e}")

    # Step 4.1: Refresh market_state + G4/G7 artifacts (non-blocking)
    try:
        _export_g4_g7_artifacts(decision, target_date)
    except Exception as e:  # noqa: BLE001 - batch isolation
        print(f"⚠️  G4/G7 artifacts skipped: {e}")
    try:
        from src.core.market_state_coordinator import run_coordinator

        run_coordinator()
    except Exception as e:  # noqa: BLE001 - batch isolation
        print(f"⚠️  market_state coordinator skipped: {e}")

    # Step 4.2: Paper watch EOD invalidation freeze (E3, non-blocking)
    # WHY: freeze_from_eod() không có caller nào → paper_watch_state.json đóng băng
    # tại phiên trước (25/09: HPG đã thủng 20,800 nhưng file vẫn as_of=24/09).
    try:
        from src.database.db_core import get_connection
        from src.portfolio.paper_watch_state import freeze_from_eod, load_paper_watch

        watch = load_paper_watch()
        symbols = [b.get("symbol") for b in watch.get("bids", []) if b.get("symbol")]
        closes: dict[str, float] = {}
        if symbols:
            marks = ",".join("?" * len(symbols))
            with get_connection() as conn:
                rows = conn.execute(
                    f"SELECT symbol, close FROM daily_ohlcv WHERE date = ? AND close > 0 AND symbol IN ({marks})",
                    [target_date, *symbols],
                ).fetchall()
            closes = {r["symbol"]: float(r["close"]) for r in rows}
        frozen = freeze_from_eod(closes, as_of=target_date)
        bids = frozen.get("bids", [])
        dropped = [b.get("symbol") for b in bids if b.get("status") == "DROPPED"]
        no_data = [s for s in symbols if s not in closes]
        print(
            f"  Paper watch freeze {target_date}: {len(bids)} bids | "
            f"DROPPED={dropped or 'none'}" + (f" | NO_BAR={no_data}" if no_data else "")
        )
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step lỗi không dừng pipeline
        print(f"⚠️  Paper watch freeze skipped: {e}")

    # Step 4.3: Market-wide breadth refresh (E4b, non-blocking, ~3.4s)
    # WHY: bảng market_wide_breadth_daily đóng băng tại 11/09 vì không có caller
    # và --end mặc định hardcode. Breadth là REGIME INPUT, không phải alpha.
    try:
        from src.analysis.market_wide_breadth import backfill, latest_db_date, load_bars, run_series

        _end = latest_db_date()
        if _end:
            _n = backfill(run_series(load_bars("2020-01-01", _end)))
            print(f"  Market-wide breadth: refreshed → {_end} ({_n} rows)")
        else:
            print("⚠️  Market-wide breadth skipped: daily_ohlcv rỗng")
    except Exception as e:  # noqa: BLE001 - batch isolation
        print(f"⚠️  Market-wide breadth skipped: {e}")

    # Step 5: Generate Report
    if verdict:
        report_file = create_markdown_report(verdict, target_date)
        print(f"\n✅ War Journal saved to: {report_file}")

    # Step 6: Update Driver Reputation Ledger (non-blocking)
    try:
        from src.telemetry.driver_reputation import update_reputation

        n = update_reputation()
        print(f"📊 Driver Reputation Ledger: {n} rows updated")
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step EOD lỗi không dừng pipeline chính
        print(f"⚠️  Reputation update skipped: {e}")

    # Step 7: SSI iBoard Macro API Probe (đồng bộ, ~1-2s, không block pipeline đáng kể)
    try:
        from src.services.macro.ssi_probe import probe_ssi_macro_endpoint

        results = probe_ssi_macro_endpoint()
        matches = sum(1 for r in results if r.get("match"))
        print(f"📡 SSI iBoard probe: {len(results)} candidates, {matches} matches")
    except Exception as e:  # noqa: BLE001 - batch isolation: 1 step EOD lỗi không dừng pipeline chính
        print(f"⚠️  SSI probe failed: {e}")

    print(f"\n{'=' * 60}")
    print("🏁 CLOSER COMPLETE. SENTINEL STANDING BY.")
    print(f"{'=' * 60}")


def _export_g4_g7_artifacts(board: dict, target_date: str) -> None:
    """Persist same-day confidence (G4) + governor target alloc (G7) for nine_gates."""
    import json

    conf_score = float(board.get("confidence", 0.0)) * 100.0
    try:
        cg_path = Path(str(src.config.DATA_DIR)) / "output" / "danh_gia_tin_cay.json"
        if cg_path.exists():
            cg = json.loads(cg_path.read_text(encoding="utf-8"))
            if str(cg.get("ngày", "")) == target_date and cg.get("điểm_tin_cậy") is not None:
                conf_score = float(cg["điểm_tin_cậy"]) * 100.0
    except OSError, ValueError, TypeError, KeyError:
        pass

    conf_path = Path(str(src.config.DATA_DIR)) / "confidence.json"
    conf_path.write_text(
        json.dumps(
            {
                "date": target_date,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "score": round(conf_score, 2),
                "confidence": round(conf_score, 2),
                "source": "daily_closer",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Standing paper/live stance: ENTRY_LOCK + cash until 9/9 gates PASS.
    final_alloc = 0.0
    try:
        fd_path = Path(str(src.config.DATA_DIR)) / "output" / "final_decision.json"
        if fd_path.exists():
            fd = json.loads(fd_path.read_text(encoding="utf-8"))
            if str(fd.get("ngay", "")) == target_date:
                final_alloc = float(fd.get("he_so_giam_ty_trong") or 0.0)
    except OSError, ValueError, TypeError, KeyError:
        final_alloc = 0.0

    gov_path = Path(str(src.config.DATA_DIR)) / "governor_decision.json"
    gov_path.write_text(
        json.dumps(
            {
                "date": target_date,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "target_allocation_pct": final_alloc,
                "consensus": board.get("consensus", ""),
                "entry_lock": True,
                "cash_pct": 100.0 if final_alloc <= 0 else round(100.0 - final_alloc, 2),
                "source": "daily_closer",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  G4/G7 artifacts: confidence={conf_score:.1f}%; target_alloc={final_alloc}%")


def _adapt_and_record_decision(board: dict):
    """Adapt boardroom decision dict to record_decision() format and persist."""
    consensus = board.get("consensus", "HOLD")
    action_map = {
        "CONVICTION BUY": "BUY",
        "HOLD / CAUTIOUS": "HOLD",
        "CAUTIOUS BUY (PULLBACK)": "BUY",
        "WAIT FOR NICHES": "HOLD",
        "CASH / STANDBY": "STAND_DOWN",
        "PILOT BUY (OVERSOLD REBOUND)": "BUY",
        "PILOT ABORT (EXIT IMMEDIATELY)": "SELL",
        "CASH (PROTECT CAPITAL)": "STAND_DOWN",
    }
    action = action_map.get(consensus, "HOLD")

    status = board.get("market_status", "UNKNOWN")
    risk_map = {"TRENDING": "SAFE", "RANGING": "CAUTION", "CRISIS": "STRESS", "UNKNOWN": "NORMAL"}
    risk_state = risk_map.get(status, "NORMAL")

    decision_dict = {
        "decision_id": str(uuid4()),
        "timestamp": board.get("timestamp", datetime.now().isoformat()),
        "action": action,
        "risk_state": risk_state,
        "confidence": board.get("confidence", 0.5) * 100,
        "engine_scores": {
            "regime": board.get("regime_score", 0),
            "breadth_pct": board.get("details", {}).get("breadth_pct", 0),
            "breadth_velocity": board.get("breadth_velocity", 0),
            "t_score": board.get("details", {}).get("t_score", 0),
            "v_score": board.get("details", {}).get("v_score", 0),
            "recovery_active": 1 if board.get("recovery", {}).get("is_recovery") else 0,
        },
    }
    did = record_decision(decision_dict)
    if did:
        print(f"📡 Telemetry snapshot recorded: {did} | {action} | conf={decision_dict['confidence']:.0f}")


if __name__ == "__main__":
    run_daily_closer()
