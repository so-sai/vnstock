import math, json, time, logging
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

# ── Đường dẫn ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CALENDAR_PATH = PROJECT_ROOT / "backend" / "src" / "config" / "weekend_holidays.json"
TELEMETRY_PATH = PROJECT_ROOT / "backend" / "data" / "telemetry" / "entropy_log.csv"
TELEMETRY_FLAG = PROJECT_ROOT / "backend" / "data" / "config" / "telemetry_enabled"


def _read_calendar_safe() -> list[str]:
    """Đọc file calendar với retry 3 lần, fallback về []."""
    for attempt in range(3):
        try:
            data = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
            return data.get("holidays", [])
        except (IOError, json.JSONDecodeError, FileNotFoundError):
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
    except Exception:
        pass
    return GRACE


def calculate_entropy_penalty(
    stale_hours: dict[str, float] | None = None,
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

    weighted_sum = 0.0
    for t in TENOR_ORDER:
        s = stale_hours.get(t, 24.0)
        t_eff = s if not _today_is_holiday() else 0.0
        lam = math.exp(-max(0.0, t_eff - GRACE) / TTL)
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
                w.writerow(["timestamp", "H_t", "stale_1W", "stale_2W", "stale_1M",
                            "stale_3M", "stale_6M", "stale_9M"])
            row = [datetime.now().isoformat(), round(H, 4)]
            if stale_hours:
                row += [round(stale_hours.get(t, 0.0), 1) for t in TENOR_ORDER]
            else:
                row += [0.0] * 6
            w.writerow(row)
    except Exception as e:
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


def _scrape_holidays() -> list[str]:
    """Scrape danh sách ngày lễ Việt Nam từ timeanddate.com."""
    import requests
    from lxml import html as lx

    url = "https://www.timeanddate.com/holidays/vietnam/"
    try:
        resp = requests.get(url, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        resp.raise_for_status()
        tree = lx.fromstring(resp.content)
        holidays = []
        for row in tree.xpath("//table[@id='holidays-table']//tbody//tr"):
            cells = row.xpath(".//td")
            if len(cells) >= 3:
                date_str = (cells[0].text_content() or "").strip()
                name = (cells[2].text_content() or "").strip()
                if date_str and "2026" in date_str:
                    holidays.append(date_str.strip())
        return sorted(set(holidays))
    except Exception as e:
        logger.warning(f"Không scrape được lịch lễ: {e}")
        return []
