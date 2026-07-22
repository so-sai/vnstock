"""
Actionable Intelligence Service (Phase 12).
Compresses all engines → simple, actionable decisions for the user.
No new analysis — just orchestration + narrative compression.
"""
import logging
import sys
from pathlib import Path


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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from datetime import datetime

import pandas as pd

from src.database.db_core import get_connection
from src.engine.breakout_continuation import get_breakout_market_context, scan_breakout_opportunities
from src.engine.liquidity_wave import get_market_liquidity_health, scan_liquidity_waves
from src.engine.regime_engine import detect_regime
from src.engine.sector_rotation_graph import get_rotation_beta
from src.portfolio.decision_tensor_v2 import compute_v2
from src.portfolio.exposure_engine import get_portfolio_heat
from src.portfolio.memory_engine import get_risk_path_window
from src.portfolio.portfolio_engine import get_open_positions

logger = logging.getLogger(__name__)


def get_opportunity_queue(top_n: int = 5) -> dict:
    breakout_opps = scan_breakout_opportunities(min_score=40, top_n=top_n * 2)
    liquidity_waves = scan_liquidity_waves(top_n=top_n * 2)
    regime = detect_regime()
    regime_status = regime.get("status", "RANGING")
    breakout_ctx = get_breakout_market_context()

    wave_symbols = {w["symbol"] for w in liquidity_waves}
    breakout_symbols = {b["symbol"] for b in breakout_opps}

    combined = {}

    for b in breakout_opps:
        sym = b["symbol"]
        liquidity = next((w for w in liquidity_waves if w["symbol"] == sym), None)
        chase_score = liquidity["retail_chase_score"] if liquidity else 0
        liq_ratio = liquidity["vol_ratio"] if liquidity else 0
        total = b["breakout_score"] * 0.5 + chase_score * 100 * 0.3 + (b["continuation_prob"] * 100) * 0.2
        combined[sym] = {
            "symbol": sym,
            "total_score": round(total, 1),
            "breakout_score": b["breakout_score"],
            "breakout_level": b["breakout_level"],
            "continuation_prob": b["continuation_prob"],
            "vol_ratio": liq_ratio,
            "retail_chase": "EXTREME" if chase_score > 0.8 else "HIGH" if chase_score > 0.5 else "MODERATE",
            "reason": _build_opportunity_reason(b, liquidity, regime_status),
            "action": _get_opportunity_action(b),
        }

    ranked = sorted(combined.values(), key=lambda x: x["total_score"], reverse=True)[:top_n]

    return {
        "opportunities": ranked,
        "market_context": {
            "regime": regime_status,
            "breakout_context": breakout_ctx.get("breakout_context", "NEUTRAL"),
            "total_candidates": len(combined),
        },
        "updated_at": datetime.now().isoformat(),
    }


def _build_opportunity_reason(breakout: dict, liquidity: dict | None, regime: str) -> str:
    parts = []
    bl = breakout.get("breakout_level", "NONE")
    parts.append(f"Phá vỡ {bl}")
    if breakout.get("volume_confirm"):
        parts.append("xác nhận khối lượng")
    if liquidity:
        rs = liquidity.get("retail_chase_score", 0)
        if rs > 0.7:
            parts.append("dòng tiền bán lẻ mạnh")
        elif rs > 0.4:
            parts.append("dòng tiền vào vừa phải")
    if breakout.get("continuation_prob", 0) > 0.65:
        parts.append("xác suất tiếp diễn cao")
    return " | ".join(parts) if parts else "Đang theo dõi"


def _get_opportunity_action(breakout: dict) -> str:
    sig = breakout.get("breakout_level", "NONE")
    cp = breakout.get("continuation_prob", 0)
    vc = breakout.get("volume_confirm", False)
    if sig == "200D" and vc and cp > 0.65:
        return "STRONG_BUY"
    if sig == "50D" and vc and cp > 0.55:
        return "BUY"
    if sig == "20D" and vc:
        return "WATCH"
    return "OBSERVE"


def get_portfolio_coach() -> dict:
    decision = compute_v2()
    positions = get_open_positions()
    heat = get_portfolio_heat()
    regime = detect_regime()
    rotation = get_rotation_beta()
    liquidity = get_market_liquidity_health()
    breakout_ctx = get_breakout_market_context()

    action = decision.get("action", "HOLD")
    risk_state = decision.get("risk_state", "SAFE")
    constraint = decision.get("constraint", "ALLOWED")
    confidence = decision.get("confidence", 50)
    n_positions = len(positions)
    regime_status = regime.get("status", "RANGING")

    coach = _generate_coach_advice(action, risk_state, constraint, confidence,
                                    n_positions, heat, regime_status, rotation, liquidity, breakout_ctx)

    return {
        "decision": {
            "action": action,
            "confidence": confidence,
            "risk_state": risk_state,
            "constraint": constraint,
        },
        "coach": coach,
        "positions_count": n_positions,
        "portfolio_heat": round(heat, 1),
        "regime": regime_status,
        "updated_at": datetime.now().isoformat(),
    }


def _generate_coach_advice(action: str, risk: str, constraint: str, confidence: int,
                            n_pos: int, heat: float, regime: str, rotation: dict,
                            liquidity: dict, breakout: dict) -> dict:
    is_locked = constraint == "BLOCKED" or risk == "LOCKED"
    is_partial = constraint == "PARTIAL"
    has_positions = n_pos > 0

    if is_locked:
        return {
            "instruction": "Dừng mọi giao dịch. Hệ thống đang khóa rủi ro.",
            "rationale": f"Chế độ {regime} | Nhiệt danh mục {heat:.0f}% | Rủi ro {risk}",
            "focus": "Bảo toàn vốn. Đợi tín hiệu hạ nhiệt.",
            "tone": "WARNING",
            "next_step": "Theo dõi nhiệt danh mục giảm dưới 4.0 hoặc chế độ thị trường chuyển khỏi rủi ro cao.",
        }

    if action == "ENTER" and not has_positions:
        liq_phase = liquidity.get("liquidity_phase", "NEUTRAL")
        rot_regime = rotation.get("rotation_regime", "NEUTRAL")
        return {
            "instruction": "Có thể mở vị thế mới. Thị trường đang ủng hộ.",
            "rationale": f"Thanh khoản đang {liq_phase} | Xoay vòng {rot_regime}",
            "focus": "Ưu tiên cổ phiếu có phá vỡ + xác nhận khối lượng.",
            "tone": "OPPORTUNITY",
            "next_step": "Chọn từ danh sách cơ hội bên dưới. Giữ tỷ trọng nhỏ nếu nhiệt > 4.",
        }

    if action == "ENTER" and has_positions:
        return {
            "instruction": "Có thể thêm vị thế mới, nhưng ưu tiên quản lý danh mục hiện tại.",
            "rationale": f"Đã có {n_pos} vị thế mở | Nhiệt {heat:.0f}% | Độ tin cậy {confidence}",
            "focus": "Kiểm tra mức cắt lỗ các vị thế hiện tại trước khi mở mới.",
            "tone": "BALANCED",
            "next_step": "Rà soát mức giảm từng vị thế. Chỉ mở mới nếu tất cả mức cắt lỗ an toàn.",
        }

    if action == "REDUCE":
        return {
            "instruction": "Cần giảm tỷ trọng. Rủi ro đang tăng.",
            "rationale": f"Nhiệt {heat:.0f}% | Rủi ro {risk} | Ràng buộc {constraint}",
            "focus": "Cắt các vị thế yếu nhất. Giữ tiền mặt linh hoạt.",
            "tone": "CAUTION",
            "next_step": "Giảm 30–50% tổng tỷ trọng. Nếu nhiệt > 7, giảm mạnh hơn.",
        }

    if action == "EXIT":
        return {
            "instruction": "Thoát toàn bộ vị thế. Hệ thống đang trong trạng thái khẩn cấp.",
            "rationale": f"Rủi ro {risk} | Nhiệt {heat:.0f}% | Không giữ vị thế qua đêm.",
            "focus": "Bảo toàn vốn là ưu tiên duy nhất.",
            "tone": "CRITICAL",
            "next_step": "Thoát ngay tất cả vị thế. Chờ tín hiệu phục hồi.",
        }

    if action == "STAND_DOWN":
        return {
            "instruction": "Đứng ngoài thị trường. Chưa có tín hiệu rõ ràng.",
            "rationale": f"Chế độ thị trường {regime} | Độ tin cậy {confidence}% | Thiếu tín hiệu động lực.",
            "focus": "Quan sát. Chờ phá vỡ có khối lượng hoặc luân chuyển rõ ràng.",
            "tone": "NEUTRAL",
            "next_step": "Theo dõi độ rộng thị trường và dòng tiền. Hành động khi có tín hiệu rõ.",
        }

    if action == "HOLD" and has_positions:
        return {
            "instruction": "Giữ vị thế hiện tại. Chưa cần thay đổi.",
            "rationale": f"Độ tin cậy {confidence}% | Nhiệt {heat:.0f}% | {n_pos} vị thế đang hoạt động.",
            "focus": "Quản lý mức cắt lỗ. Chờ phá vỡ mới để tăng tỷ trọng.",
            "tone": "NEUTRAL",
            "next_step": "Kiểm tra mức cắt lỗ mỗi ngày. Có thể vận hành bình thường.",
        }

    return {
        "instruction": "Theo dõi thị trường. Chưa có hành động khẩn cấp.",
        "rationale": f"Chế độ thị trường {regime} | {n_pos} vị thế | Nhiệt {heat:.0f}%",
        "focus": "Tiếp tục quan sát. Chờ cơ hội.",
        "tone": "NEUTRAL",
        "next_step": "Cập nhật thường xuyên. Hệ thống sẽ báo khi có tín hiệu.",
    }


def get_scenario_simulation(scenario: str = "drop_5pct") -> dict:
    decision = compute_v2()
    positions = get_open_positions()
    heat_curr = get_portfolio_heat()
    risk_window = get_risk_path_window(30)
    regime_verdict = detect_regime()

    recall_idx = None
    if risk_window:
        for i, point in enumerate(risk_window):
            if point.get("drawdown_pct", 0) >= 5.0:
                recall_idx = i
                break
    last_dd = risk_window[-1]["drawdown_pct"] if risk_window and len(risk_window) > 0 else 0

    total_market_value = sum(
        p.get("current_size", 0) * (p.get("avg_cost", 0) if p.get("avg_cost") else 0)
        for p in positions
    )
    if scenario == "drop_5pct":
        loss = round(total_market_value * 0.05, 0)
        new_heat = min(10, heat_curr + 2.0)
        result_risk = "STRESS" if new_heat >= 7 else "CAUTION"
    elif scenario == "drop_10pct":
        loss = round(total_market_value * 0.10, 0)
        new_heat = min(10, heat_curr + 4.0)
        result_risk = "LOCKED"
    elif scenario == "surge_3pct":
        loss = round(total_market_value * -0.03, 0)
        new_heat = max(0, heat_curr - 1.0)
        result_risk = "SAFE"
    else:
        loss = 0
        new_heat = heat_curr
        result_risk = decision.get("risk_state", "SAFE")

    return {
        "scenario": scenario,
        "current_heat": round(heat_curr, 1),
        "projected_heat": round(new_heat, 1),
        "projected_risk": result_risk,
        "estimated_loss_vnd": loss,
        "estimated_loss_pct": round((loss / max(1, total_market_value)) * 100, 2) if total_market_value > 0 else 0,
        "last_dd_pct": round(last_dd, 2),
        "has_historical_precedent": recall_idx is not None,
        "regime": regime_verdict.get("status", "RANGING"),
        "advice": _get_scenario_advice(scenario, result_risk, loss),
    }


def _get_scenario_advice(scenario: str, risk: str, loss: float) -> str:
    if scenario == "drop_5pct":
        if risk == "STRESS":
            return f"Giảm 5% có thể đẩy nhiệt lên ngưỡng căng thẳng (lỗ ~{loss:,.0f} VND). Cân nhắc giảm tỷ trọng."
        return f"Giảm 5%: rủi ro ở mức {risk}. Tiếp tục quản lý mức cắt lỗ."
    if scenario == "drop_10pct":
        return f"Giảm 10% là kịch bản xấu nhất (lỗ ~{loss:,.0f} VND). Hệ thống sẽ khóa toàn bộ giao dịch."
    if scenario == "surge_3pct":
        return "Tăng 3%: danh mục có thể hạ nhiệt. Có thể tận dụng để mở thêm vị thế mới."
    return "Kịch bản tùy chỉnh. Đánh giá dựa trên tham số hiện tại."


def get_position_narrative(symbol: str) -> dict:
    positions = get_open_positions()
    pos = next((p for p in positions if p.get("symbol") == symbol), None)
    if not pos:
        return {"symbol": symbol, "status": "NOT_FOUND"}

    entry_date = pos.get("entry_date", "")
    regime_entry = pos.get("regime_at_entry", "UNKNOWN")
    conviction = pos.get("conviction_score", 0)
    current_size = pos.get("current_size", 0)
    avg_cost = pos.get("avg_cost", 0)
    sl_price = pos.get("stop_loss_price", 0)
    thesis = pos.get("thesis_notes", pos.get("thesis_source", ""))

    with get_connection() as conn:
        current_price_df = pd.read_sql(
            "SELECT close, date FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            conn, params=(symbol,)
        )
    current_price = float(current_price_df.iloc[0]["close"]) if not current_price_df.empty else avg_cost

    pnl_pct = ((current_price - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
    dist_sl = ((current_price - sl_price) / current_price) * 100 if current_price > 0 and sl_price > 0 else 0

    decision = compute_v2()
    system_action = decision.get("action", "HOLD")
    risk_state = decision.get("risk_state", "SAFE")

    if pnl_pct < -5:
        verdict = "Trong vùng nguy hiểm. Cân nhắc cắt lỗ."
    elif dist_sl < 2.5:
        verdict = "Gần stop-loss. Theo dõi chặt."
    elif pnl_pct > 10:
        verdict = "Có lãi tốt. Cân nhắc chốt lời một phần."
    elif conviction < 0.4:
        verdict = "Conviction thấp. Xem xét giảm size."
    elif system_action == "EXIT":
        verdict = "Hệ thống khuyến nghị thoát. Cân nhắc."
    elif system_action == "REDUCE":
        verdict = "Hệ thống đang giảm exposure. Xem xét giảm size."
    else:
        verdict = "Vị thế đang hoạt động bình thường."

    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "regime_at_entry": regime_entry,
        "avg_cost": round(avg_cost, 0),
        "current_price": round(current_price, 0),
        "pnl_pct": round(pnl_pct, 2),
        "distance_to_sl_pct": round(dist_sl, 2),
        "conviction_score": round(conviction, 2),
        "thesis": thesis or "Chưa có luận điểm đầu tư.",
        "system_action": system_action,
        "system_risk": risk_state,
        "verdict": verdict,
        "status": pos.get("status", "ENTERED"),
        "narrative": _build_position_story(symbol, pnl_pct, dist_sl, conviction,
                                            regime_entry, system_action, risk_state),
    }


def _build_position_story(symbol: str, pnl_pct: float, dist_sl: float,
                           conviction: float, regime_entry: str,
                           sys_action: str, sys_risk: str) -> str:
    if pnl_pct < -8:
        return f"{symbol} đang lỗ sâu {pnl_pct:.1f}%. Luận điểm entry có thể đã sai. Cần review gấp."
    if pnl_pct < -3:
        return f"{symbol} đang giảm {pnl_pct:.1f}%. Nếu conviction > 0.6 có thể giữ, nếu không nên cắt."
    if pnl_pct > 15:
        return f"{symbol} đã tăng {pnl_pct:.1f}% từ entry. Lợi nhuận tốt. Có thể chốt 50%."
    if pnl_pct > 5:
        return f"{symbol} đang đi đúng hướng (+{pnl_pct:.1f}%). Tiếp tục nắm giữ."
    if dist_sl < 2:
        return f"{symbol} chỉ còn cách stop-loss {dist_sl:.1f}%. Chuẩn bị phương án thoát."
    return f"{symbol} đang ở vùng an toàn. Tiếp tục quản lý theo kế hoạch."


def get_live_summary() -> dict:
    def _safe_call(fn, default):
        try:
            result = fn()
            return result if result is not None else default
        except Exception as e:
            logger.warning(f"get_live_summary sub-call failed: {type(e).__name__}: {e}")
            return default

    decision = _safe_call(compute_v2, {})
    regime_verdict = _safe_call(detect_regime, {})
    rotation = _safe_call(get_rotation_beta, {})
    liquidity = _safe_call(get_market_liquidity_health, {})
    breakout_ctx = _safe_call(get_breakout_market_context, {})
    positions = _safe_call(get_open_positions, [])
    coach = _safe_call(get_portfolio_coach, {})

    return {
        "regime": regime_verdict.get("status", "RANGING") if isinstance(regime_verdict, dict) else "RANGING",
        "decision": {
            "action": decision.get("action", "HOLD") if isinstance(decision, dict) else "HOLD",
            "confidence": decision.get("confidence", 50) if isinstance(decision, dict) else 50,
            "risk": decision.get("risk_state", "SAFE") if isinstance(decision, dict) else "SAFE",
            "constraint": decision.get("constraint", "ALLOWED") if isinstance(decision, dict) else "ALLOWED",
        },
        "liquidity_phase": liquidity.get("liquidity_phase", "NEUTRAL") if isinstance(liquidity, dict) else "NEUTRAL",
        "rotation_regime": rotation.get("rotation_regime", "NEUTRAL") if isinstance(rotation, dict) else "NEUTRAL",
        "breakout_context": breakout_ctx.get("breakout_context", "LOW_BREAKOUT_ACTIVITY") if isinstance(breakout_ctx, dict) else "LOW_BREAKOUT_ACTIVITY",
        "positions_count": len(positions) if isinstance(positions, list) else 0,
        "coach_instruction": coach.get("coach", {}).get("instruction", "") if isinstance(coach, dict) else "",
        "updated_at": datetime.now().isoformat(),
    }
