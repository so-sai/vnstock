"""
params_hash.py — Atomic Parameter Identity

Đảm bảo deterministic cross-platform hash của bộ tham số:
  - canonicalize_params(): round(float, 8) đệ quy
  - make_params_hash(): SHA256(sort_keys=True, ensure_ascii=True)
"""

import hashlib
import json
from typing import Any


def canonicalize_params(value: Any, precision: int = 8) -> Any:
    if isinstance(value, float):
        return round(value, precision)
    if isinstance(value, dict):
        return {k: canonicalize_params(v, precision) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize_params(v, precision) for v in value]
    return value


def make_params_hash(params: dict) -> str:
    canon = canonicalize_params(params)
    raw = json.dumps(canon, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()[:16]
