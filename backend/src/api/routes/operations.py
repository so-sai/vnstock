import logging
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)


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

from src.core.canonical_output_adapter import localize_output

router = APIRouter()


@router.post("/daily-close", summary="Chốt phiên giao dịch (16:00)")
async def api_daily_close():
    """Thực thi daily closer: breadth + sector + flow + macro + reputation."""
    try:
        from src.daily_closer import run_daily_closer

        run_daily_closer()
        return localize_output(
            {
                "status": "success",
                "message": "CHỐT PHIÊN THÀNH CÔNG: Sổ cái SQLite đã khóa!",
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Daily close failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Daily close thất bại: {str(e)}")


@router.post("/telemetry-drift", summary="Ghi log drift data")
async def api_telemetry_drift():
    """Tính toán và ghi nhật ký độ lệch hệ thống (Drift Monitor)."""
    try:
        from src.telemetry.driver_reputation import update_reputation

        n = update_reputation()
        return localize_output(
            {
                "status": "success",
                "message": "ĐÃ GHI NHẬT KÝ DRIFT: Baseline 90 ngày ổn định!",
                "rows_updated": n,
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Telemetry drift failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Drift log thất bại: {str(e)}")
