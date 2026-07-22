# vnai.py (Mock for vnstock bypass)
import functools


def setup():
    """Mock vnai initialization."""
    pass


def setup_api_key(api_key):
    """Mock API key setup."""
    return True


def check_api_key_status():
    """Mock account status as Sponsor."""
    return {
        "has_api_key": True,
        "tier": "Sponsor",
        "api_key_preview": "VNS-PATCHED-ULTRA-GOLD",
        "limits": {"per_minute": 10000, "per_day": 1000000},
    }


def agg_execution(source_name="Unknown"):
    """
    Mock decorator for aggregated execution.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


def optimize_execution(source_name="Unknown"):
    """
    Mock decorator for execution optimization.
    Calls the original function directly without vnai overhead.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator
