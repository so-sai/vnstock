from vnstock.common import (
    indices,  # Standardized market constants
    viz,
)
from vnstock.common.data import Company, Finance, Fund, Listing, MSNComponents, Quote, StockComponents, Trading

# Lazy initialization to avoid circular import deadlock
_initialized = False


def _ensure_initialized():
    """Ensure common module is initialized (called on first use)."""
    global _initialized
    if _initialized:
        return

    try:
        from vnstock.core.utils.env import id_valid
        from vnstock.core.utils.upgrade import update_notice

        id_valid()
        update_notice()
        _initialized = True
    except Exception:
        _initialized = True  # Mark as initialized to avoid retry loops
