import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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

from src.services.portfolio_service import (
    get_portfolio_summary,
    add_position,
    remove_position,
    update_cash,
    update_position,
)
from src.portfolio.portfolio_engine import get_open_positions, get_portfolio_summary as get_engine_summary
from src.portfolio.exposure_engine import get_portfolio_heat
from src.portfolio.memory_engine import get_risk_path_window
from src.portfolio.decision_tensor import compute as compute_decision
from src.portfolio.decision_tensor_v2 import (
    compute_v2 as compute_decision_v2,
    log_override,
    log_confirm,
    get_decision_history,
)

router = APIRouter()


class PositionInput(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    fee_paid: float = 0.0015


class CashInput(BaseModel):
    amount: float


class PositionUpdate(BaseModel):
    quantity: int | None = None
    entry_price: float | None = None


@router.get("/")
async def get_portfolio():
    """Lấy tổng quan danh mục + P&L + cảnh báo stop-loss."""
    try:
        return get_portfolio_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/position")
async def add_new_position(pos: PositionInput):
    """Thêm vị thế mới."""
    try:
        return add_position(
            symbol=pos.symbol,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            fee_paid=pos.fee_paid,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/position/{symbol}")
async def delete_position(symbol: str):
    """Xóa vị thế."""
    try:
        return remove_position(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/cash")
async def update_portfolio_cash(cash: CashInput):
    """Cập nhật số dư tiền mặt."""
    try:
        return update_cash(cash.amount)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/position/{symbol}")
async def update_portfolio_position(symbol: str, data: PositionUpdate):
    """Cập nhật vị thế (số lượng hoặc giá vốn)."""
    try:
        return update_position(
            symbol=symbol,
            quantity=data.quantity,
            entry_price=data.entry_price,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/observatory/summary")
async def observatory_summary():
    """Portfolio Observatory: engine-level telemetry + active holdings + heat."""
    try:
        summary = get_engine_summary()
        holdings = get_open_positions()
        heat = get_portfolio_heat()
        nav = summary.get("latest_snapshot", {}).get("total_nav", 0)
        return {
            "open_positions": summary.get("open_positions", 0),
            "total_shares": summary.get("total_shares", 0),
            "market_value_vnd": round(summary.get("market_value_vnd", 0), 0),
            "portfolio_heat_pct": round(heat, 2),
            "net_exposure_pct": round(
                (summary.get("market_value_vnd", 0) / nav * 100) if nav else 0, 2
            ),
            "total_nav": round(nav, 0),
            "telemetry": summary.get("latest_snapshot", {}),
            "holdings": [
                {
                    "id": h["id"],
                    "symbol": h["symbol"],
                    "status": h["status"],
                    "regime_at_entry": h.get("regime_at_entry", ""),
                    "entry_date": h.get("entry_date", ""),
                    "avg_cost": round(h.get("avg_cost", 0), 0),
                    "current_size": h.get("current_size", 0),
                    "stop_loss_price": round(h.get("stop_loss_price", 0), 0),
                    "conviction_score": round(h.get("conviction_score", 0.0), 2),
                    "initial_risk_pct": round(h.get("initial_risk_pct", 0.0), 2),
                    "thesis_source": h.get("thesis_source", ""),
                    "thesis_notes": h.get("thesis_notes", ""),
                }
                for h in holdings
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/observatory/risk-path")
async def observatory_risk_path(days: int = 30):
    """Portfolio Observatory: portfolio risk path EKG data."""
    try:
        return get_risk_path_window(days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/observatory/decision")
async def observatory_decision():
    """Phase 10 — Decision Tensor: compresses 5 engine layers into 1 action vector."""
    try:
        return compute_decision()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class OverrideInput(BaseModel):
    decision_id: str
    override_action: str
    override_reason: str = ""


class ConfirmInput(BaseModel):
    decision_id: str


@router.get("/observatory/decision-v2")
async def observatory_decision_v2():
    """Phase 10.2 — Cognitive Decision Tensor: counterfactual + rationale tree + calibrated weights."""
    try:
        return compute_decision_v2()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/observatory/decision-override")
async def decision_override(ov: OverrideInput):
    """Log human override of a decision."""
    try:
        return log_override(ov.decision_id, ov.override_action, ov.override_reason)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/observatory/decision-confirm")
async def decision_confirm(cf: ConfirmInput):
    """Log human confirmation of a decision."""
    try:
        return log_confirm(cf.decision_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/observatory/decision-history")
async def observatory_decision_history(limit: int = 20):
    """Recent decision history for audit trail."""
    try:
        return get_decision_history(limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
