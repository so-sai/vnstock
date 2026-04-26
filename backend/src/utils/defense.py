import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import time

class CircuitBreaker:
    """
    Cơ chế Cầu dao tự ngắt (Circuit Breaker) để bảo vệ IP.
    Tự động tạm dừng (Cooldown) nếu nhận quá nhiều lỗi từ API.
    """
    
    _failure_log: Dict[str, float] = {}
    COOLDOWN_SECONDS: int = 60

    @classmethod
    def is_available(cls, source: str) -> bool:
        """Kiểm tra nguồn API có đang trong thời gian nghỉ (Cooldown) hay không."""
        if source in cls._failure_log:
            last_failure_time = cls._failure_log[source]
            if time.time() - last_failure_time < cls.COOLDOWN_SECONDS:
                return False
            else:
                # Hết thời gian cooldown, cho phép thử lại
                del cls._failure_log[source]
        return True

    @classmethod
    def report_failure(cls, source: str) -> None:
        """Ghi nhận lỗi từ API để kích hoạt Cooldown."""
        cls._failure_log[source] = time.time()
        print(f"🛑 [Circuit Breaker] Tripped for {source}. Cooling down for {cls.COOLDOWN_SECONDS}s.")

