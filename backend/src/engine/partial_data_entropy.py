import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Trọng số thanh khoản 6 kỳ hạn ──
TENOR_ORDER = ["1W", "2W", "1M", "3M", "6M", "9M"]
WEIGHTS = {"1W": 0.35, "2W": 0.25, "1M": 0.20, "3M": 0.10, "6M": 0.07, "9M": 0.03}

# ── Tham số điều khiển ──
BASE = 0.15
GAMMA = 0.35
GRACE = 8.0
TTL = 24.0
H_MIN = 0.15
H_MAX = 0.50

# ── Temporal coupling parameters ──
ALPHA = 0.15
Z_THRESHOLD = 3.0
CRISIS_RATE = 15.0
EARLY_WARNING_THRESHOLD = 12.0
MIN_CONSECUTIVE = 2

# ── Đường dẫn ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CALENDAR_PATH = PROJECT_ROOT / "backend" / "src" / "config" / "weekend_holidays.json"
TELEMETRY_PATH = PROJECT_ROOT / "backend" / "data" / "telemetry" / "entropy_log.csv"
TELEMETRY_FLAG = PROJECT_ROOT / "backend" / "data" / "config" / "telemetry_enabled"
_STRESS_STATE_PATH = PROJECT_ROOT / "backend" / "data" / "probe_cache" / "micro_stress.json"

# ── I/O Health — Inline Heartbeat (nhất thể hóa nhịp đập vào payload) ──
# Không dùng file .io_healthy riêng — heartbeat nằm trong crisis_cooldown.json
# để triệt tiêu rủi ro bất đối xứng sector giữa file chỉ dấu và file cấu hình.
_INLINE_HEARTBEAT_MAX_AGE = 10.0  # seconds — quá hạn → OS write cache nghi ngờ
_CROSS_SESSION_HEARTBEAT_MAX_AGE = 3600.0  # seconds (1h) — cross-session stale threshold

# ── Half-Open Circuit Probe ──
_PROBE_COOLDOWN = 3600.0  # seconds — 60 phút giữa các lần probe
_PROBE_PATH = PROJECT_ROOT / "backend" / "data" / "probe_cache" / ".io_probe"
_last_probe_time: float = 0.0

# ── RAM buffer thuần túy — không chạm đĩa khi circuit open ──
_virtual_ram_storage: dict[str, str] = {}


def _inline_heartbeat_timestamp() -> float:
    """Đọc io_heartbeat_timestamp từ payload crisis_cooldown.json."""
    try:
        data = json.loads(_COOLDOWN_PATH.read_text(encoding="utf-8"))
        return float(data.get("io_heartbeat_timestamp", 0.0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as e:
        logger.debug("[ENTROPY] Heartbeat read failed: %s", e)
        return 0.0


def _inline_heartbeat_is_fresh(max_age: float = _INLINE_HEARTBEAT_MAX_AGE) -> bool:
    """Kiểm tra nhịp đập nhúng trong payload.

    Nếu timestamp trong file quá cũ (> max_age) so với thời gian hiện tại,
    kết luận I/O bus đã chết một chiều (write-only dead).
    """
    ts = _inline_heartbeat_timestamp()
    if ts == 0.0:
        return False
    age = time.time() - ts
    return age < max_age


def _inline_heartbeat_set(state: dict) -> dict:
    """Nhúng io_heartbeat_timestamp vào state trước khi ghi."""
    state["io_heartbeat_timestamp"] = time.time()
    return state


def _probe_disk_health() -> bool:
    """Half-Open Circuit Probe: ghi 1-byte, đọc lại, verify hash.

    Chỉ chạy tối đa 1 lần mỗi _PROBE_COOLDOWN giây.
    Nếu ghi thành công + đọc khớp → đĩa đã phục hồi.
    Nếu timeout hoặc hash mismatch → giữ nguyên FORCED_SAFETY.
    """
    global _last_probe_time
    now = time.time()
    if now - _last_probe_time < _PROBE_COOLDOWN:
        return False  # chưa đến lúc probe lại

    _last_probe_time = now
    probe_data = b"\x00"
    expected_hash = hashlib.sha256(probe_data).hexdigest()
    done = threading.Event()
    result: list[bool | Exception] = [False]

    def _probe():
        try:
            _PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _PROBE_PATH.write_bytes(probe_data)
            # Không cần fsync trên probe — chỉ cần ghi + đọc + verify hash
            readback = _PROBE_PATH.read_bytes()
            actual_hash = hashlib.sha256(readback).hexdigest()
            result[0] = actual_hash == expected_hash
        except (OSError, TypeError, ValueError) as e:
            logger.debug("[PROBE] Disk probe failed: %s", e)
            result[0] = False
        finally:
            done.set()

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(5.0)  # timeout 5 giây cho probe

    if not done.is_set():
        logger.warning("[PROBE] I/O probe timeout — circuit stays OPEN")
        return False

    if result[0]:
        logger.info("[PROBE] I/O probe SUCCESS — disk recovered, closing circuit")
        return True

    logger.warning("[PROBE] I/O probe FAILED — circuit remains OPEN")
    return False


def _atomic_write_json(path: Path, data: dict, timeout: float = 5.0):
    """Ghi JSON với atomic os.replace() + I/O timeout guard + fsync.

    - Viết vào .tmp → os.replace (atomic, cùng volume) → os.fsync (chống OS write cache)
    - Thành công → heartbeat được nhúng trong payload (không file riêng)
    - Thất bại (timeout/lỗi) → lưu vào _virtual_ram_storage (RAM thuần túy),
      raise IOError. Không ghi đè file rác, không fallback %TEMP%.
    """
    tmp = path.with_suffix(".tmp")
    result: list[Exception | None] = [None]
    done = threading.Event()

    def _write():
        try:
            payload = json.dumps(data, ensure_ascii=False)
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
            # fsync: force OS write cache flush → physical disk
            # Windows requires O_RDWR for fsync (O_RDONLY returns EBADF)
            fd = os.open(str(path), os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, TypeError, ValueError) as e:
            result[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_write, daemon=True)
    t.start()
    t.join(timeout)

    if not done.is_set():
        _virtual_ram_storage[str(path.absolute())] = json.dumps(data, ensure_ascii=False)
        raise OSError(f"I/O timeout ({timeout}s) — possible bad sector on {path}")

    if result[0] is not None:
        _virtual_ram_storage[str(path.absolute())] = json.dumps(data, ensure_ascii=False)
        raise result[0]


def _read_calendar_safe() -> list[str]:
    """Đọc file calendar với retry 3 lần, fallback về []."""
    for attempt in range(3):
        try:
            data = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
            return data.get("holidays", [])
        except OSError, json.JSONDecodeError, FileNotFoundError:
            if attempt < 2:
                time.sleep(0.1)
    return []


def _today_is_holiday() -> bool:
    """Kiểm tra hôm nay có phải ngày nghỉ (thứ Bảy, Chủ Nhật hoặc lễ đặc biệt) không."""
    now = datetime.now()
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return True
    holidays = _read_calendar_safe()
    return now.strftime("%Y-%m-%d") in holidays


def hours_since_last_scrape(tenor: str | None = None) -> float:
    """Tính t_eff: 0 nếu hôm nay là lễ, else hours_since_last_scrape.

    Trả về số giờ kể từ lần cuối tenor được cập nhật trong DB,
    hoặc giá trị mặc định nếu không có trong DB.
    """
    if _today_is_holiday():
        return 0.0
    try:
        from src.services.macro.interbank_seeder import _get_latest_from_db

        variable = f"INTERBANK_{tenor}" if tenor else "INTERBANK_ON"
        _, ts = _get_latest_from_db(variable)
        if ts:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            return max(0.0, (datetime.now() - dt).total_seconds() / 3600)
    except (ImportError, sqlite3.Error, TypeError, ValueError, KeyError) as e:
        logger.debug("[ENTROPY] Stale hours lookup failed: %s", e)
    return GRACE


def _load_stress_state() -> dict:
    key = str(_STRESS_STATE_PATH.absolute())
    if key in _virtual_ram_storage:
        return json.loads(_virtual_ram_storage[key])
    # Nếu file tồn tại nhưng heartbeat chết → I/O write-only dead
    if _STRESS_STATE_PATH.exists() and not _inline_heartbeat_is_fresh(_CROSS_SESSION_HEARTBEAT_MAX_AGE):
        if not _probe_disk_health():
            return {"consecutive_high": 0, "gate_active": True, "last_z": 99.9, "last_date": ""}
    try:
        return json.loads(_STRESS_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError:
        return {"consecutive_high": 0, "gate_active": False, "last_z": 0.0, "last_date": ""}


def _save_stress_state(state: dict):
    _STRESS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["io_heartbeat_timestamp"] = time.time()
    _atomic_write_json(_STRESS_STATE_PATH, state)


def compute_temporal_penalty(
    z_fast: float | None = None,
    is_liquidity_crisis: bool = False,
) -> float:
    """Tính Φ(Z_fast) = temporal penalty multiplier cho λ (Cross-Layer Volatility Coupling).

    Công thức:
      is_liquidity_crisis=True  → Φ = 0.0  (xóa sạch trọng số macro)
      Z_fast > Z_THRESHOLD sustained  → Φ = exp(-ALPHA * (Z_fast - Z_THRESHOLD))
      Z_fast <= Z_THRESHOLD           → Φ = 1.0  (giữ nguyên)

    Duration gate: cần MIN_CONSECUTIVE phiên liên tiếp Z_fast > Z_THRESHOLD
    để kích hoạt. Trạng thái persist qua file micro_stress.json xuyên phiên.

    Returns:
        Φ ∈ [0.0, 1.0]
    """
    if z_fast is None:
        return 1.0

    if is_liquidity_crisis:
        _save_stress_state(
            {"consecutive_high": 0, "gate_active": True, "last_z": z_fast, "last_date": datetime.now().strftime("%Y-%m-%d")}
        )
        return 0.0

    state = _load_stress_state()
    today = datetime.now().strftime("%Y-%m-%d")

    if z_fast > Z_THRESHOLD:
        if state.get("last_date") != today:
            state["consecutive_high"] = state.get("consecutive_high", 0) + 1
        state["last_z"] = z_fast
        if state["consecutive_high"] >= MIN_CONSECUTIVE:
            state["gate_active"] = True
    else:
        state["consecutive_high"] = 0
        state["gate_active"] = False
        state["last_z"] = z_fast

    state["last_date"] = today
    _save_stress_state(state)

    if state["gate_active"]:
        return math.exp(-ALPHA * (z_fast - Z_THRESHOLD))
    return 1.0


# ── Crisis Cooldown Gate (Chống Bull Trap) ──

_COOLDOWN_PATH = PROJECT_ROOT / "backend" / "data" / "probe_cache" / "crisis_cooldown.json"


def _load_cooldown_state(current_on: float = 0.0, z_fast: float = 0.0) -> dict:
    # ── Kiểm tra RAM buffer trước — dữ liệu từ phiên bị kẹt I/O ──
    key = str(_COOLDOWN_PATH.absolute())
    if key in _virtual_ram_storage:
        logger.info("[COOLDOWN] Reading from RAM buffer (I/O was dead last session)")
        return json.loads(_virtual_ram_storage[key])

    # ── Nếu file tồn tại nhưng heartbeat trong payload quá cũ → I/O write-only dead ──
    if _COOLDOWN_PATH.exists():
        if not _inline_heartbeat_is_fresh(_CROSS_SESSION_HEARTBEAT_MAX_AGE):
            if _probe_disk_health():
                logger.info("[COOLDOWN] Half-open probe OK — disk recovered, closing circuit")
            else:
                logger.warning("[COOLDOWN] I/O heartbeat dead + probe failed — FORCED_SAFETY")
                return {
                    "crisis_active": True,
                    "crisis_marker": "FORCED_SAFETY",
                    "consecutive_normal": 0,
                    "last_normal_date": "",
                    "last_crisis_date": "",
                    "io_heartbeat_timestamp": 0.0,
                }

    try:
        data = json.loads(_COOLDOWN_PATH.read_text(encoding="utf-8"))
        # Đảm bảo heartbeat field tồn tại cho lần kiểm tra sau
        data.setdefault("io_heartbeat_timestamp", time.time())
        return data
    except FileNotFoundError:
        # Mất file → kiểm tra sensor thời gian thực trước khi fallback
        if current_on >= EARLY_WARNING_THRESHOLD or z_fast > Z_THRESHOLD:
            logger.warning(
                "[COOLDOWN] File missing + ON=%.1f%% >= %.0f%% or Z=%.1f — FORCED_SAFETY",
                current_on,
                EARLY_WARNING_THRESHOLD,
                z_fast,
            )
            return {
                "crisis_active": True,
                "crisis_marker": "FORCED_SAFETY",
                "consecutive_normal": 0,
                "last_normal_date": "",
                "last_crisis_date": "",
                "io_heartbeat_timestamp": time.time(),
            }
        return {
            "crisis_active": False,
            "crisis_marker": "",
            "consecutive_normal": 0,
            "last_normal_date": "",
            "last_crisis_date": "",
            "io_heartbeat_timestamp": time.time(),
        }
    except json.JSONDecodeError, ValueError:
        logger.warning("[COOLDOWN] File hỏng — CORRUPTED_FALLBACK: crisis_active=True")
        return {
            "crisis_active": True,
            "crisis_marker": "CORRUPTED_FALLBACK",
            "consecutive_normal": 0,
            "last_normal_date": "",
            "last_crisis_date": "",
            "io_heartbeat_timestamp": 0.0,
        }


def _save_cooldown_state(state: dict):
    _COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_COOLDOWN_PATH, _inline_heartbeat_set(state))


def update_crisis_cooldown(current_on: float, z_fast: float = 0.0):
    """Cập nhật trạng thái cooldown dựa trên lãi suất ON hiện tại.

    - ON >= CRISIS_RATE (15%): reset bộ đếm, kích hoạt crisis
    - ON < CRISIS_RATE và crisis_active: tăng consecutive_normal (tối đa 1 lần/ngày)
    """
    state = _load_cooldown_state(current_on=current_on, z_fast=z_fast)
    today = datetime.now().strftime("%Y-%m-%d")

    if current_on >= CRISIS_RATE:
        state["crisis_active"] = True
        state["crisis_marker"] = "CRISIS_REAL"
        state["consecutive_normal"] = 0
        state["last_crisis_date"] = today
        state["last_normal_date"] = today  # chặn đếm trong cùng phiên
        logger.info("[COOLDOWN] Crisis active: ON=%.1f%% >= %.0f%%", current_on, CRISIS_RATE)
    elif state.get("crisis_active", False):
        if state.get("last_normal_date") != today:
            state["consecutive_normal"] = state.get("consecutive_normal", 0) + 1
            state["last_normal_date"] = today
            logger.info("[COOLDOWN] Crisis cooldown: ngày an toàn %d/3 (ON=%.1f%%)", state["consecutive_normal"], current_on)

    _save_cooldown_state(state)


def assess_crisis_unlock(current_on: float, z_fast: float, recovery_days: int) -> bool:
    """Kiểm tra 3 chốt chặn để mở khóa Crisis Mode (Chống Bull Trap).

    Công thức:
        Unlock = (ON < 15% AND consecutive_normal >= 3)
             AND (z_fast < 3.0)
             AND (recovery_days >= 2)

    Returns:
        True nếu không trong cooldown hoặc đã đủ điều kiện mở khóa.
        False nếu vẫn đang trong cooldown (he_so_giam_ty_trong = 0.0).
    """
    state = _load_cooldown_state(current_on=current_on, z_fast=z_fast)
    if not state.get("crisis_active", False):
        return True

    is_on_safe = current_on < CRISIS_RATE and state.get("consecutive_normal", 0) >= 3
    is_stress_cleared = z_fast < Z_THRESHOLD
    is_trend_stable = recovery_days >= 2

    if is_on_safe and is_stress_cleared and is_trend_stable:
        state["crisis_active"] = False
        state["consecutive_normal"] = 0
        _save_cooldown_state(state)
        logger.info("[COOLDOWN] Crisis UNLOCKED: ON=%.1f%%, Z=%.1f, recovery=%d", current_on, z_fast, recovery_days)
        return True

    logger.info(
        "[COOLDOWN] Crisis locked: consecutive=%d/3, Z=%.1f, recovery=%d",
        state.get("consecutive_normal", 0),
        z_fast,
        recovery_days,
    )
    return False


def calculate_entropy_penalty(
    stale_hours: dict[str, float] | None = None,
    z_fast: float | None = None,
    is_liquidity_crisis: bool = False,
) -> float:
    """Tính H(t) = clamp(BASE + γ·(1 - Σw_i·λ_i), 0.15, 0.50).

    Args:
        stale_hours: dict {"1W": 10.5, "2W": 10.5, ...}.
                     Nếu None, đọc từ database.

    Returns:
        H(t) in [0.15, 0.50]
    """
    if stale_hours is None:
        stale_hours = {}
        for t in TENOR_ORDER:
            stale_hours[t] = hours_since_last_scrape(t)

    phi = compute_temporal_penalty(z_fast, is_liquidity_crisis)
    weighted_sum = 0.0
    for t in TENOR_ORDER:
        s = stale_hours.get(t, 24.0)
        t_eff = s if not _today_is_holiday() else 0.0
        lam = math.exp(-max(0.0, t_eff - GRACE) / TTL) * phi
        weighted_sum += WEIGHTS[t] * lam

    H = BASE + GAMMA * (1.0 - weighted_sum)
    return max(H_MIN, min(H_MAX, H))


def log_entropy_telemetry(H: float, stale_hours: dict[str, float] | None = None):
    """Ghi H(t) vào CSV nếu telemetry được bật (opt-in)."""
    if not TELEMETRY_FLAG.exists():
        return
    try:
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not TELEMETRY_PATH.exists()
        import csv

        with open(TELEMETRY_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "H_t", "stale_1W", "stale_2W", "stale_1M", "stale_3M", "stale_6M", "stale_9M"])
            row = [datetime.now().isoformat(), round(H, 4)]
            if stale_hours:
                row += [round(stale_hours.get(t, 0.0), 1) for t in TENOR_ORDER]
            else:
                row += [0.0] * 6
            w.writerow(row)
    except (OSError, PermissionError, TypeError, ValueError) as e:
        logger.debug(f"Telemetry write failed: {e}")


def enable_telemetry():
    """Bật ghi telemetry."""
    TELEMETRY_FLAG.parent.mkdir(parents=True, exist_ok=True)
    TELEMETRY_FLAG.write_text("enabled", encoding="utf-8")
    logger.info("Telemetry enabled")


def disable_telemetry():
    """Tắt ghi telemetry."""
    if TELEMETRY_FLAG.exists():
        TELEMETRY_FLAG.unlink()
    logger.info("Telemetry disabled")


def cleanup_telemetry(max_rows: int = 1000):
    """Dọn dẹp entropy_log.csv — giữ lại max_rows dòng gần nhất.

    Chạy định kỳ qua 'python ptck.py cleanup telemetry' để tránh phình ổ đĩa.
    """
    if not TELEMETRY_PATH.exists():
        return
    try:
        import csv

        with open(TELEMETRY_PATH, newline="", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        if len(reader) <= max_rows + 1:  # +1 for header
            return
        header = reader[:1]
        tail = reader[-(max_rows):]
        with open(TELEMETRY_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerows(header + tail)
        logger.info("[GC] entropy_log.csv: %d → %d rows", len(reader), len(tail) + 1)
    except (OSError, PermissionError, TypeError, ValueError, IndexError) as e:
        logger.warning("[GC] Telemetry cleanup failed: %s", e)


def _scrape_holidays() -> list[str]:
    """Scrape danh sách ngày lễ Việt Nam từ timeanddate.com."""
    import requests
    from lxml import html as lx

    url = "https://www.timeanddate.com/holidays/vietnam/"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        tree = lx.fromstring(resp.content)
        holidays = []
        for row in tree.xpath("//table[@id='holidays-table']//tbody//tr"):
            cells = row.xpath(".//td")
            if len(cells) >= 3:
                date_str = (cells[0].text_content() or "").strip()
                (cells[2].text_content() or "").strip()
                if date_str and "2026" in date_str:
                    holidays.append(date_str.strip())
        return sorted(set(holidays))
    except (requests.RequestException, OSError, ValueError, KeyError) as e:
        logger.warning(f"Không scrape được lịch lễ: {e}")
        return []
