"""v1_screener.py — Batch screener API với Reference-based Localization.

Tuân thủ:
  - NormalizedPayload pattern: localizations tách biệt, data dùng *ref.
  - Generic Pydantic model (BilingualRef, LocalizationDict).
"""
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
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

from src.core.bilingual_schema import (
    SIGNAL_V1_CLASSES, NormalizedPayload, build_normalized_payload,
)

router = APIRouter()


class ScreenerBatchItem(BaseModel):
    symbol: str
    price: float
    changePercent: float
    return6m: float
    signalV1_ref: str
    volumeRatio: float
    rsRating: int
    sector: str


@router.get("/screener/all", response_model=NormalizedPayload[ScreenerBatchItem])
async def get_screener_batch(top_n: int = Query(200, ge=1, le=500)):
    """Quét danh sách cổ phiếu — batch với Reference-based Localization.

    Trả về NormalizedPayload:
      - localizations: từ điển BilingualRef cho mỗi signal duy nhất.
      - data: mảng ScreenerBatchItem, mỗi item dùng signalV1_ref
              thay vì nhúng chuỗi localization.
    """
    try:
        from src.services.screener_service import get_screener_results

        items = get_screener_results(top_n=top_n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not items:
        return NormalizedPayload(
            localizations={},
            data=[],
        )

    return build_normalized_payload(
        items=items,
        signal_field="signalV1",
        signal_schema=SIGNAL_V1_CLASSES,
        model_class=ScreenerBatchItem,
    )
