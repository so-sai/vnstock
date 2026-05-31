import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

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

from src.services.backtest_service import get_backtest_results, get_stress_test_summary

router = APIRouter()


@router.get("/")
async def run_backtest(
    model: str = Query("A", pattern="^[ABab]$"),
    start_date: str = Query("2023-01-01"),
    end_date: str = Query("2026-04-17"),
):
    """
    Chạy backtest với model A (Momentum) hoặc B (Mean Reversion).
    Trả về equity curve, portfolio stats, top picks.
    """
    try:
        return get_backtest_results(
            model=model,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stress-test")
async def stress_test_2022():
    """Tóm tắt kết quả Stress Test giai đoạn 2022."""
    try:
        return get_stress_test_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
