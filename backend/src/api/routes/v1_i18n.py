"""v1_i18n.py — API endpoint phục vụ dictionary auto-sync cho Frontend.

Đọc CLI_LABEL_MAP + ABBREVIATION_GLOSSARY từ canonical_output_adapter.py,
trả về JSON chứa cả nhãn ngôn ngữ lẫn tooltip giải nghĩa abbreviation.

Frontend dùng hook useDictionary() để fetch endpoint này,
cache trong localStorage, auto-refresh khi TTL hết hạn.

Endpoints:
    GET /api/v1/i18n/dictionary  — Full dictionary (labels + abbreviations)
    GET /api/v1/i18n/labels      — Chỉ labels (lightweight)
    GET /api/v1/i18n/abbreviations — Chỉ abbreviation glossary
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/i18n", tags=["i18n"])

# Cache in-memory với TTL
_cache: dict[str, Any] = {}
_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes


def _build_dictionary() -> dict[str, Any]:
    """Xây dựng dictionary từ canonical_output_adapter."""
    from src.core.canonical_output_adapter import ABBREVIATION_GLOSSARY, CLI_LABEL_MAP

    # Labels: key -> Vietnamese
    labels_vi = dict(CLI_LABEL_MAP)
    labels_en = {k: k for k in CLI_LABEL_MAP}

    # Abbreviations: structured glossary
    abbreviations = {}
    for abbr, info in ABBREVIATION_GLOSSARY.items():
        abbreviations[abbr] = {
            "vi": info.get("vi", abbr),
            "en": info.get("en", abbr),
            "detail_vi": info.get("detail_vi", ""),
            "detail_en": info.get("detail_en", ""),
            "category": info.get("category", "general"),
        }

    # Category groupings cho UI
    categories: dict[str, list[str]] = {}
    for abbr, info in ABBREVIATION_GLOSSARY.items():
        cat = info.get("category", "general")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(abbr)

    return {
        "version": int(time.time()),
        "generated_at": datetime.now().isoformat(),
        "labels": {
            "vi": labels_vi,
            "en": labels_en,
        },
        "abbreviations": abbreviations,
        "categories": categories,
        "label_count": len(labels_vi),
        "abbreviation_count": len(abbreviations),
    }


def _get_cache() -> dict[str, Any]:
    """Lấy dictionary từ cache hoặc build mới."""
    global _cache, _cache_ts
    now = time.time()
    if not _cache or (now - _cache_ts) > _CACHE_TTL:
        _cache = _build_dictionary()
        _cache_ts = now
    return _cache


@router.get("/dictionary")
async def get_full_dictionary(
    lang: str = Query("vi", description="Language: vi or en"),
) -> dict[str, Any]:
    """Full dictionary: labels + abbreviations + categories.

    Response:
        {
            "version": 1234567890,
            "generated_at": "...",
            "labels": {"vi": {...}, "en": {...}},
            "abbreviations": {"HDR": {"vi": "...", "en": "...", "detail_vi": "...", ...}},
            "categories": {"governance": ["HDR", "DOC", "IG"], ...},
            "label_count": 250,
            "abbreviation_count": 30
        }
    """
    return _get_cache()


@router.get("/labels")
async def get_labels_only(
    lang: str = Query("vi", description="Language: vi or en"),
) -> dict[str, str]:
    """Lightweight labels only. Dùng cho i18n t() function.

    Response: {"HDR": "Tỷ lệ Giảm thiểu Rủi ro", ...}
    """
    data = _get_cache()
    return data["labels"].get(lang, data["labels"]["vi"])


@router.get("/abbreviations")
async def get_abbreviations_only() -> dict[str, dict[str, str]]:
    """Abbreviation glossary only. Dùng cho tooltip system.

    Response: {"HDR": {"vi": "...", "en": "...", "detail_vi": "...", ...}}
    """
    data = _get_cache()
    return data["abbreviations"]


@router.get("/hash")
async def get_dictionary_hash() -> dict[str, str]:
    """MD5 hash của dictionary. Dùng cho frontend cache invalidation.

    Response: {"hash": "abc123", "version": 1234567890}
    """
    data = _get_cache()
    content = json.dumps(data, sort_keys=True, ensure_ascii=False)
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    return {"hash": md5, "version": str(data["version"])}
