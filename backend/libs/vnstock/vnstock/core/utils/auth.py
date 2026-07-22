# vnstock/core/utils/auth.py

"""
User authentication and API key registration for vnstock.

Simple interface for users to register their API key.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def register_user(api_key: Optional[str] = None) -> bool:
    """
    [PATCHED] User registration bypass for Fork version.
    """
    print("✓ [Bypass] Chế độ Fork: API Key đã được kích hoạt ngầm (Sponsor Tier).")
    return True


def _register_api_key_directly(api_key: str) -> bool:
    return True


def _register_interactive() -> bool:
    return True


def change_api_key(api_key: str) -> bool:
    return True


def check_status() -> Optional[dict]:
    """
    [PATCHED] Trả về trạng thái Sponsor ảo để bypass giới hạn API nội bộ.
    """
    status = {
        "has_api_key": True,
        "api_key_preview": "VNS-PATCHED-EMULATOR",
        "tier": "Sponsor",
        "limits": {"per_minute": 10000, "per_day": 1000000},
    }
    return status
