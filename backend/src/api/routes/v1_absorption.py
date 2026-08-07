"""v1_absorption.py — API endpoint: phân tích hấp thụ nội cho từng mã cổ phiếu.

Tuân thủ:
  - Quy tắc Song ngữ API (HCI format): KHÔNG trả chuỗi thuần, trả dict localization.
  - Decoupled Architecture: gọi engine qua wrapper, không nhúng logic.
"""

import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException


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

from src.core.bilingual_schema import (
    ABSORPTION_PHASES,
    VQA_CLASSES,
    to_hci,
)

router = APIRouter()


@router.get("/symbol/{ticker}/absorption")
async def get_symbol_absorption(ticker: str):
    """Phân tích hấp thụ nội cho một mã cổ phiếu.

    Trả về VQA (Volume-Quality Analyzer), SDI (Spectral Dominance Index),
    phase, HDR unlock status — tất cả ở định dạng HCI song ngữ.
    """
    try:
        from src.engine.per_symbol_absorption import PerSymbolAbsorption

        ticker = ticker.upper()
        detector = PerSymbolAbsorption(ticker)
        raw = detector.analyze()
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        raise HTTPException(status_code=500, detail=str(e))

    if raw.get("status") not in ("OK", "LOW_DATA"):
        raise HTTPException(status_code=404, detail=f"Không có dữ liệu cho mã {ticker}")

    vqa_raw = raw.get("vqa") or {}
    vqa_class = vqa_raw.get("classification", "LOW_LIQUIDITY_NOISE")
    vqa_value = vqa_raw.get("aq", 0.0)
    sdi_value = raw.get("sdi")
    phase = raw.get("phase", "UNKNOWN")
    hdr = raw.get("hdr", 1.0)

    result = {
        "symbol": ticker,
        "target_date": raw.get("target_date", datetime.now().strftime("%Y-%m-%d")),
        "timestamp": datetime.now().isoformat(),
        "metrics": [
            to_hci("VQA", vqa_value, vqa_class, VQA_CLASSES),
        ],
        "sdi": {
            "value": sdi_value,
            "localization": {
                "vi": {
                    "name": "Spectral Dominance Index (SDI)",
                    "tooltip": "Đo lường sự thống trị của thành phần chính trong ma trận thanh khoản. SDI cao = thị trường một chiều.",  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
                },
                "en": {
                    "name": "Spectral Dominance Index (SDI)",
                    "tooltip": "Measures dominance of the principal component in the liquidity matrix. High SDI = one-sided market.",  # noqa: E501 - chuỗi nội dung dài (i18n/SQL)
                },
            },
        },
        "phase": to_hci("PHASE", phase, phase, ABSORPTION_PHASES),
        "hdr": {
            "current": hdr,
            "target": raw.get("hdr_progress", {}).get("target", 0.80),
            "localization": {
                "vi": {
                    "name": "Hệ số Giải ngân (HDR)",
                    "tooltip": "Tỷ lệ vốn được phép giải ngân. 1.0 = cash-only, 0.20 = full deployment.",
                },
                "en": {
                    "name": "HDR (Heuristic Deployment Ratio)",
                    "tooltip": "Portion of capital allowed for deployment. 1.0 = cash-only, 0.20 = full deployment.",
                },
            },
        },
        "details": raw.get("details", {}),
    }

    # Thêm distribution_warning
    if raw.get("distribution_warning"):
        result["warnings"] = [
            {
                "signal": "DISTRIBUTION_WARNING",
                "color_code": "#FF8C00",
                "localization": {
                    "vi": {"name": "Cảnh báo phân phối", "tooltip": "VQA phát hiện dấu hiệu phân phối, HDR đang bị khóa."},
                    "en": {"name": "Distribution Warning", "tooltip": "VQA detects distribution signs, HDR locked."},
                },
            }
        ]

    return result
