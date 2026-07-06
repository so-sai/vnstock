"""
Sentinel Circuit Breaker — bảo vệ IP nhà cung cấp API ở mức phần cứng.

Nguyên tắc:
1. Mỗi "source" (vnstock, yfinance, v.v.) có 1 breaker riêng.
2. Khi API trả 429 / 5xx → report_failure(source) → breaker TRIP.
3. Trong thời gian COOLDOWN (12h cho vnstock theo quân lệnh Kiến trúc sư trưởng),
   mọi is_available(source) đều trả False → caller phải dùng cache/disk fallback.
4. State được persist xuống file JSON trong backend/data/ để KHÔNG bị mất khi restart
   process. Nếu process crash, breaker vẫn giữ trip cho đủ 12h.
5. Có cách force_open=False để operator mở lại thủ công (khi đã xác nhận API hồi phục).

File state: backend/data/circuit_breaker_state.json
Format:
    {
        "vnstock": {"tripped_at": 1700000000.0, "reason": "429 rate limit"},
        "yfinance": {"tripped_at": 1700000123.0, "reason": "timeout"}
    }
"""
import json
import logging
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
DATA_DIR = PROJECT_ROOT / "backend" / "data"
STATE_FILE = DATA_DIR / "circuit_breaker_state.json"


# ── Source-specific cooldowns ─────────────────────────────
# vnstock: 12h theo quân lệnh (rate limit rất gắt)
# yfinance: 30 phút (ít strict hơn, có thể recover nhanh)
# default: 5 phút
SOURCE_COOLDOWN = {
    "vnstock": 43200,      # 12 tiếng
    "yfinance": 1800,      # 30 phút
    "default": 300,        # 5 phút
}

# Mã lỗi kích hoạt trip
TRIP_HTTP_CODES = {429, 500, 502, 503, 504}
TRIP_KEYWORDS = ("rate limit", "too many requests", "quota exceeded", "ban ip", "blocked")


class CircuitBreaker:
    """Cầu dao tự ngắt — bảo vệ IP, persist state qua file."""

    _lock = threading.Lock()
    _memory_state: dict = {}
    _initialized = False

    @classmethod
    def _ensure_loaded(cls):
        """Lazy-load state từ file. Thread-safe."""
        if cls._initialized:
            return
        with cls._lock:
            if cls._initialized:
                return
            try:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                if STATE_FILE.exists():
                    cls._memory_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                else:
                    cls._memory_state = {}
            except Exception as e:
                logger.warning(f"[CircuitBreaker] Không đọc được state file: {e}")
                cls._memory_state = {}
            cls._initialized = True

    @classmethod
    def _persist(cls):
        """
        Ghi state xuống file bằng pattern atomic (tempfile + os.replace).
        Cross-platform: hoạt động trên cả Windows lẫn Linux mà không cần msvcrt/fcntl.

        Quy trình:
        1. Ghi JSON xuống file tạm (cùng thư mục để tránh cross-filesystem issue)
        2. Flush + fsync để chắc chắn data nằm trên disk trước khi replace
        3. os.replace() — atomic rename, không có cửa sổ "truncate nhưng chưa ghi xong"
        4. Cleanup file tạm nếu replace fail
        """
        # Nếu không có gì để ghi thì xóa file (tránh file rỗng)
        if not cls._memory_state:
            try:
                if STATE_FILE.exists():
                    STATE_FILE.unlink()
            except Exception as e:
                logger.warning(f"[CircuitBreaker] Không xóa được state file rỗng: {e}")
            return

        payload = json.dumps(cls._memory_state, ensure_ascii=False, indent=2)
        # Tạo file tạm CÙNG thư mục với STATE_FILE — đảm bảo os.replace là rename atomic
        # chứ không phải copy+delete (sẽ không atomic nếu khác filesystem)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".circuit_breaker_",
            suffix=".json.tmp",
            dir=str(STATE_FILE.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())  # ép data xuống disk trước khi replace
            os.replace(tmp_path, STATE_FILE)  # atomic rename
        except Exception as e:
            # Nếu replace fail, dọn file tạm để không rác
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
            logger.error(f"[CircuitBreaker] Không ghi được state file (atomic): {e}")

    @classmethod
    def _cooldown_for(cls, source: str) -> int:
        return SOURCE_COOLDOWN.get(source, SOURCE_COOLDOWN["default"])

    @classmethod
    def is_available(cls, source: str) -> bool:
        """True nếu source KHÔNG trong cooldown. Caller được phép gọi API."""
        cls._ensure_loaded()
        with cls._lock:
            entry = cls._memory_state.get(source)
            if not entry:
                return True
            tripped_at = entry.get("tripped_at", 0)
            if time.time() - tripped_at >= cls._cooldown_for(source):
                # Hết cooldown — auto reset, cho phép thử lại
                logger.info(f"[CircuitBreaker] {source}: cooldown hết hạn → auto-reset CLOSED")
                cls._memory_state.pop(source, None)
                cls._persist()
                return True
            return False

    @classmethod
    def time_remaining(cls, source: str) -> int:
        """Trả về số giây còn lại trong cooldown. 0 = không trong cooldown."""
        cls._ensure_loaded()
        with cls._lock:
            entry = cls._memory_state.get(source)
            if not entry:
                return 0
            tripped_at = entry.get("tripped_at", 0)
            remaining = cls._cooldown_for(source) - (time.time() - tripped_at)
            return max(0, int(remaining))

    @classmethod
    def report_failure(cls, source: str, reason: str = "unknown") -> None:
        """Kích hoạt trip. Cooldown = SOURCE_COOLDOWN[source] (mặc định 12h cho vnstock)."""
        cls._ensure_loaded()
        with cls._lock:
            cls._memory_state[source] = {
                "tripped_at": time.time(),
                "reason": reason,
                "cooldown_seconds": cls._cooldown_for(source),
            }
            cls._persist()
            cooldown = cls._cooldown_for(source)
            logger.warning(
                f"🛑 [CircuitBreaker] TRIP {source} — lý do: {reason} | "
                f"cooldown={cooldown}s (~{cooldown//3600}h{cooldown%3600//60}m)"
            )

    @classmethod
    def should_trip_on_error(cls, error_msg: str, http_code: Optional[int] = None) -> bool:
        """Phân tích exception message / http_code để quyết định có trip hay không."""
        if http_code in TRIP_HTTP_CODES:
            return True
        if not error_msg:
            return False
        msg = str(error_msg).lower()
        return any(kw in msg for kw in TRIP_KEYWORDS)

    @classmethod
    def force_close(cls, source: str) -> None:
        """Operator mở lại breaker thủ công (vd: sau khi verify API đã hồi phục)."""
        cls._ensure_loaded()
        with cls._lock:
            if source in cls._memory_state:
                cls._memory_state.pop(source, None)
                cls._persist()
                logger.info(f"[CircuitBreaker] {source}: FORCE CLOSED (manual reset)")

    @classmethod
    def status(cls) -> dict:
        """Snapshot trạng thái hiện tại — cho dashboard / health check."""
        cls._ensure_loaded()
        with cls._lock:
            out = {}
            for source, entry in cls._memory_state.items():
                remaining = cls._cooldown_for(source) - (time.time() - entry.get("tripped_at", 0))
                out[source] = {
                    "tripped": remaining > 0,
                    "remaining_seconds": max(0, int(remaining)),
                    "reason": entry.get("reason"),
                    "tripped_at": entry.get("tripped_at"),
                }
            return out


# ── Public guard helper ────────────────────────────────────
class APIBlockedError(Exception):
    """Raise khi breaker đang trip. Caller phải dùng fallback."""
    def __init__(self, source: str, remaining: int):
        self.source = source
        self.remaining = remaining
        super().__init__(
            f"🔒 Circuit OPEN for {source} — còn {remaining}s (~{remaining//3600}h{remaining%3600//60}m) "
            f"trong cooldown. Dùng cache/disk."
        )


def guarded_call(source: str, fn, *args, **kwargs):
    """
    Helper wrap một API call.
    - Trước khi gọi: check is_available(source) → fail-fast nếu breaker trip.
    - Sau khi gọi: bắt exception, phân tích → report_failure nếu match trip pattern.
    - Re-raise exception gốc để caller xử lý fallback.
    """
    if not CircuitBreaker.is_available(source):
        remaining = CircuitBreaker.time_remaining(source)
        raise APIBlockedError(source, remaining)

    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        http_code = getattr(e, "status_code", None) or getattr(e, "code", None)
        if CircuitBreaker.should_trip_on_error(str(e), http_code):
            CircuitBreaker.report_failure(source, reason=f"{type(e).__name__}: {str(e)[:200]}")
        raise
