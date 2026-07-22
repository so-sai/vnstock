
import sys
from datetime import datetime
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
        if 'decision' in verdict:
            d = verdict['decision']
            f.write("## 🏛️ PHÁN QUYẾT BỘ CHỈ HUY (THE BOARDROOM)\n\n")
            f.write(f"- **TRẠNG THÁI THỊ TRƯỜNG:** `{d['market_status']}` (Score: {d['regime_score']})\n")
            f.write(f"- **MÔ HÌNH ƯU TIÊN:** `{d['active_model']}`\n")
            f.write(f"- **PHÁN QUYẾT CUỐI CÙNG:** **{d['consensus']}**\n")
            f.write(f"- **ĐỘ TIN CẬY (CONFIDENCE):** `{d['confidence'] * 100}%`\n\n")

            if d['model_b']['top_picks']:
                f.write("### 🎯 Danh sách Quan tâm (Mean Reversion Selection)\n")
                for pick in d['model_b']['top_picks']:
                    f.write(f"- {pick['symbol']} (Z-Score: {pick['z_score']}, RSI: {pick['rsi']})\n")
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
        if 'decision' in verdict:
            rec = verdict['decision']['recovery']
            f.write("## 🚀 BỘ ĐÁNH LỬA (IGNITION SWITCH)\n\n")
            f.write(f"- **TRẠNG THÁI PHỤC HỒI:** `{rec['status']}`\n")
            f.write(f"- **GIA TỐC ĐỘ RỘNG (5D):** `{rec['details']['velocity_5d']:+.1f}%` (Ngưỡng: +15%)\n")
            f.write(f"- **XÁC NHẬN MA10:** `{'YES' if rec['ma10_reclaim'] else 'NO'}`\n\n")

        # Section 4: Sentinel Details
        f.write("## 🔍 Chi tiết Trạng thái Sentinel (Model A)\n\n")
        f.write(f"- **Momentum Expansion (Lớp 1):** {verdict['layer1_mom_expansion']['status']} ({verdict['layer1_mom_expansion']['value']}/{verdict['layer1_mom_expansion']['threshold']})\n")
        f.write(f"- **NH10 Consistency (Lớp 2):** {verdict['layer2_nh10_consistency']['status']} ({verdict['layer2_nh10_consistency']['value']}/{verdict['layer2_nh10_consistency']['threshold']} ngày)\n")
        f.write(f"- **Foreign Absorption (Lớp 3):** {verdict['layer3_foreign_absorption']['status']}\n\n")

        # Section 5: Cognitive journal (Vietnamese, from vi_localizer)
        if 'decision' in verdict:
            f.write("## 🧠 NHẬT KÝ NHẬN THỨC HỆ THỐNG\n\n")
            f.write("```\n")
            f.write(render_cognitive_journal(verdict['decision']))
            f.write("\n```\n\n")

        f.write("---\n")
        f.write("*Bản báo cáo này được tạo tự động bởi PTCK_VNSTOCK Multi-Model Decision Stack.*")

    return report_path

def run_daily_closer():
    """Quy trình đóng phiên tự động (The Dragon Shield Automation)"""
    target_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"🐉 THE DRAGON SHIELD: DAILY CLOSER - {target_date}")
    # Step 0: Optimize/Init DB
    optimize_sqlite_engine()

    # Step 1: Sync Data
    run_daily_update(target_date)

    # Step 2: Run Sentinel (Model A)
    verdict = evaluate_sentinel_status()

    # Step 3: Run Decision Engine (Consensus)
    decision = merge_decisions(verdict)
    verdict['decision'] = decision

    # Step 3.1: Run Structural Detector
    try:
        from src.engine.structural_detector import detect_cau_truc
        struct = detect_cau_truc(target_date)
        print(f"  Cấu trúc: {struct.get('trang_thai', 'N/A')} ({struct.get('so_tru_ok', 0)}/3)")
    except Exception as e:
        print(f"⚠️  Structural detector skipped: {e}")

    # Step 3.2: Run Final Orchestrator
    try:
        from src.engine.orchestrator import quyet_dinh_cuoi
        final = quyet_dinh_cuoi(target_date)
        print(f"  {final.get('quyet_dinh', 'N/A')} — {', '.join(final.get('ly_do', []))}")
    except Exception as e:
        print(f"⚠️  Orchestrator skipped: {e}")

    # Step 3.3: Log to Timeline
    log_regime_state(decision)

    # Step 4: Record Decision Snapshot (Telemetry)
    try:
        _adapt_and_record_decision(decision)
    except Exception as e:
        print(f"⚠️  Telemetry snapshot skipped: {e}")

    # Step 5: Generate Report
    if verdict:
        report_file = create_markdown_report(verdict, target_date)
        print(f"\n✅ War Journal saved to: {report_file}")

    # Step 6: Update Driver Reputation Ledger (non-blocking)
    try:
        from src.telemetry.driver_reputation import update_reputation
        n = update_reputation()
        print(f"📊 Driver Reputation Ledger: {n} rows updated")
    except Exception as e:
        print(f"⚠️  Reputation update skipped: {e}")

    # Step 7: SSI iBoard Macro API Probe (đồng bộ, ~1-2s, không block pipeline đáng kể)
    try:
        from src.services.macro.ssi_probe import probe_ssi_macro_endpoint
        results = probe_ssi_macro_endpoint()
        matches = sum(1 for r in results if r.get("match"))
        print(f"📡 SSI iBoard probe: {len(results)} candidates, {matches} matches")
    except Exception as e:
        print(f"⚠️  SSI probe failed: {e}")

    print(f"\n{'='*60}")
    print("🏁 CLOSER COMPLETE. SENTINEL STANDING BY.")
    print(f"{'='*60}")

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
