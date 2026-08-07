"""sensor_validation.py — Sensor Validation Layer (P(Crisis | Signal) + lead time).

WHY: Thế giới (KOSPI, SOX, DXY, KRW, VIX...) là Upstream Evidence cho thị trường
VN, NHƯNG việc mã hóa cứng "KOSPI giảm → S&P sập" thành luật giao dịch là một
Rule-based Trap: giai đoạn biến động cục bộ tạo hàng loạt False Alarm. Thay vì
hardcode, layer này LƯU HỒ SƠ THỐNG KÊ cho từng cảm biến:

    P(Crisis | Signal)      — xác suất có crisis VN trong horizon sau khi signal
    lead_days               — độ trễ trung bình (trung vị, min, max) từ signal
                              đến onset crisis
    false_alarm_rate        — 1 - P(Crisis | Signal)

Quyết định vị thế của Governor chỉ dựa trên bằng chứng NỘI ĐỊA đã qua kiểm định
(domestic evidence), còn các cảm biến thế giới chỉ là dữ liệu xác suất tham khảo.
Không bao giờ viết "câu chuyện lịch sử" thành quy tắc cứng.

Schema (bảng sensor_validation trong screener_cache.db):
    sensor         TEXT     — 'KOSPI', 'SOX', 'DXY', 'KRW', ...
    signal_date    TEXT     — ngày tín hiệu bắn
    signal_type    TEXT     — 'DROP_5D', 'STRESS_LEVEL', 'CRISIS_LEVEL', ...
    signal_value   REAL     — độ lớn tín hiệu (ví dụ % giảm, giá trị DXY)
    crisis_flag    INTEGER  — 1 nếu có crisis VN trong horizon_days
    lead_days      INTEGER  — số phiên từ signal đến onset crisis (NULL nếu không)
    horizon_days   INTEGER  — cửa sổ kiểm định (mặc định 20 phiên)
    PRIMARY KEY (sensor, signal_date, signal_type)
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median


# ── Sentinel v2.2 (AGENTS.md Anchor) ─────────────────────────────────
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
    for _p in [root_path / "backend" / "src", root_path / "backend", root_path]:
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection

# ── Defaults ──────────────────────────────────────────────────────────

DEFAULT_HORIZON_DAYS = 20  # ~1 tháng giao dịch — cửa sổ kiểm định crisis

# WHY: Ngưỡng tín hiệu theo ý nghĩa truyền dẫn thực tế (không phải story).
#   - DXY 106/108 trùng STRESS/CRISIS của MacroGovernor → một nguồn chân lý.
#   - KOSPI/SOX DROP_5D >5% = đổ vỡ chuỗi cung chip vòng 5 phiên — tín hiệu
#     đủ mạnh để đo, nhưng việc đo P(Crisis|Signal) mới quyết định có dùng hay không.
SENSOR_SIGNAL_DEFS: dict[str, dict] = {
    "DXY": {
        "type": "LEVEL",
        "stress": 106.0,
        "crisis": 108.0,
    },
    "KOSPI": {
        "type": "DROP_5D",
        "threshold_pct": 0.05,  # giảm >5% trong 5 phiên
    },
    "SOX": {
        "type": "DROP_5D",
        "threshold_pct": 0.05,
    },
    "KRW": {
        "type": "LEVEL",
        "stress": 1350.0,  # USDKRW stress mức (tính tương đối theo từng kỳ)
        "crisis": 1400.0,
    },
}


# ── Row container ─────────────────────────────────────────────────────


@dataclass
class SensorSignal:
    """Một quan sát tín hiệu → outcome (crisis VN hay không)."""

    sensor: str
    signal_date: str
    signal_type: str
    signal_value: float
    crisis_flag: int = 0
    lead_days: int | None = None
    horizon_days: int = DEFAULT_HORIZON_DAYS

    def to_row(self) -> tuple:
        return (
            self.sensor,
            self.signal_date,
            self.signal_type,
            self.signal_value,
            self.crisis_flag,
            self.lead_days,
            self.horizon_days,
        )


@dataclass
class SensorProfile:
    """Hồ sơ xác suất của một cảm biến — đầu ra chính của Validation Layer."""

    sensor: str
    n: int = 0
    n_crisis: int = 0
    p_crisis: float = 0.0
    false_alarm_rate: float = 1.0
    lead_days_mean: float | None = None
    lead_days_median: float | None = None
    lead_days_min: int | None = None
    lead_days_max: int | None = None
    by_type: dict[str, dict] = field(default_factory=dict)
    horizon_days: int = DEFAULT_HORIZON_DAYS

    def to_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "n": self.n,
            "n_crisis": self.n_crisis,
            "p_crisis": round(self.p_crisis, 4),
            "false_alarm_rate": round(self.false_alarm_rate, 4),
            "lead_days_mean": self.lead_days_mean,
            "lead_days_median": self.lead_days_median,
            "lead_days_min": self.lead_days_min,
            "lead_days_max": self.lead_days_max,
            "horizon_days": self.horizon_days,
            "by_type": self.by_type,
        }


# ── Schema & persistence ──────────────────────────────────────────────

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sensor_validation (
    sensor        TEXT NOT NULL,
    signal_date   TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    signal_value  REAL,
    crisis_flag   INTEGER NOT NULL DEFAULT 0,
    lead_days     INTEGER,
    horizon_days  INTEGER NOT NULL DEFAULT 20,
    PRIMARY KEY (sensor, signal_date, signal_type)
)
"""


def ensure_schema() -> None:
    """Tạo bảng sensor_validation nếu chưa có (idempotent)."""
    with get_connection() as conn:
        conn.execute(CREATE_SQL)
        conn.commit()


def record_signal(row: SensorSignal) -> None:
    """Upsert một quan sát tín hiệu vào bảng sensor_validation."""
    ensure_schema()
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sensor_validation
               (sensor, signal_date, signal_type, signal_value,
                crisis_flag, lead_days, horizon_days)
               VALUES (?,?,?,?,?,?,?)""",
            row.to_row(),
        )
        conn.commit()


def load_records(
    sensor: str,
    signal_type: str | None = None,
    horizon_days: int | None = None,
) -> list[SensorSignal]:
    """Đọc các quan sát của một cảm biến (tùy chọn lọc theo type/horizon)."""
    ensure_schema()
    sql = (
        "SELECT sensor, signal_date, signal_type, signal_value, "
        "crisis_flag, lead_days, horizon_days FROM sensor_validation "
        "WHERE sensor = ?"
    )
    params: list = [sensor]
    if signal_type:
        sql += " AND signal_type = ?"
        params.append(signal_type)
    if horizon_days:
        sql += " AND horizon_days = ?"
        params.append(horizon_days)
    sql += " ORDER BY signal_date"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        SensorSignal(
            sensor=r["sensor"],
            signal_date=r["signal_date"],
            signal_type=r["signal_type"],
            signal_value=r["signal_value"],
            crisis_flag=r["crisis_flag"],
            lead_days=r["lead_days"],
            horizon_days=r["horizon_days"],
        )
        for r in rows
    ]


# ── Statistics ─────────────────────────────────────────────────────────


def _type_stats(records: list[SensorSignal]) -> dict[str, dict]:
    """Thống kê P(Crisis|Signal) theo từng signal_type."""
    by_type: dict[str, list[SensorSignal]] = {}
    for r in records:
        by_type.setdefault(r.signal_type, []).append(r)
    out: dict[str, dict] = {}
    for t, recs in sorted(by_type.items()):
        n_c = sum(1 for r in recs if r.crisis_flag)
        p = n_c / len(recs) if recs else 0.0
        leads = [r.lead_days for r in recs if r.crisis_flag and r.lead_days is not None]
        out[t] = {
            "n": len(recs),
            "n_crisis": n_c,
            "p_crisis": round(p, 4),
            "false_alarm_rate": round(1.0 - p, 4),
            "lead_days_mean": round(sum(leads) / len(leads), 2) if leads else None,
            "lead_days_median": round(median(leads), 2) if leads else None,
        }
    return out


def compute_profile(
    sensor: str,
    signal_type: str | None = None,
    horizon_days: int | None = None,
) -> SensorProfile:
    """Tính P(Crisis | Signal) và thống kê lead time cho một cảm biến.

    WHY: P(Crisis|Signal) = n_crisis / n — ước lượng xác suất thực nghiệm có
    crisis VN trong horizon sau khi tín hiệu bắn. lead_days chỉ tính trên các
    quan sát CÓ crisis (với tín hiệu không có crisis thì lead_days = NULL).
    false_alarm_rate = 1 - P(Crisis|Signal) đo độ tin của cảm biến: càng thấp
    càng đáng tin, ngược lại phải hạ ưu tiên cảm biến đó trong Governor.
    """
    records = load_records(sensor, signal_type, horizon_days)
    hz = horizon_days or (records[0].horizon_days if records else DEFAULT_HORIZON_DAYS)

    profile = SensorProfile(sensor=sensor, horizon_days=hz)
    if not records:
        return profile

    profile.n = len(records)
    profile.n_crisis = sum(1 for r in records if r.crisis_flag)
    profile.p_crisis = profile.n_crisis / profile.n
    profile.false_alarm_rate = 1.0 - profile.p_crisis

    leads = [r.lead_days for r in records if r.crisis_flag and r.lead_days is not None]
    if leads:
        profile.lead_days_mean = round(sum(leads) / len(leads), 2)
        profile.lead_days_median = round(median(leads), 2)
        profile.lead_days_min = min(leads)
        profile.lead_days_max = max(leads)

    profile.by_type = _type_stats(records)
    return profile


def list_sensors() -> list[str]:
    """Danh sách cảm biến đã có hồ sơ trong DB."""
    ensure_schema()
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT sensor FROM sensor_validation ORDER BY sensor").fetchall()
    return [r["sensor"] for r in rows]


# ── Signal detection (Upstream Evidence → observations) ───────────────


def detect_signal_events(
    sensor: str,
    dates: list[str],
    values: list[float],
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[SensorSignal]:
    """Nhận diện các sự kiện tín hiệu từ chuỗi giá lịch sử của cảm biến.

    WHY: Đây là lớp tách tín hiệu KHỎI việc gán outcome. Hàm này chỉ đánh dấu
    thời điểm tín hiệu bắn (signal_type + signal_value); việc gán crisis_flag/
    lead_days được làm riêng bởi label_outcomes() bằng dữ liệu regime VN —
    đảm bảo không trộn lẫn nguồn bằng chứng (sensor data vs domestic crisis).

    Args:
        sensor: tên cảm biến (KOSPI/SOX/DXY/KRW)
        dates: chuỗi ngày (đã sắp xếp tăng dần, tối thiểu 6 điểm)
        values: chuỗi giá/giá trị tương ứng
        horizon_days: cửa sổ kiểm định sẽ dùng khi label

    Returns:
        Danh sách SensorSignal chưa gán outcome (crisis_flag=0, lead_days=None).
    """
    if sensor not in SENSOR_SIGNAL_DEFS:
        return []
    if len(dates) < 6 or len(dates) != len(values):
        return []

    cfg = SENSOR_SIGNAL_DEFS[sensor]
    sig_type = cfg["type"]
    events: list[SensorSignal] = []

    if sig_type == "LEVEL":
        stress = cfg.get("stress")
        crisis = cfg.get("crisis")
        for d, v in zip(dates, values):
            if crisis is not None and v >= crisis:
                events.append(
                    SensorSignal(
                        sensor=sensor,
                        signal_date=d,
                        signal_type="CRISIS_LEVEL",
                        signal_value=v,
                        horizon_days=horizon_days,
                    )
                )
            elif stress is not None and v >= stress:
                events.append(
                    SensorSignal(
                        sensor=sensor,
                        signal_date=d,
                        signal_type="STRESS_LEVEL",
                        signal_value=v,
                        horizon_days=horizon_days,
                    )
                )
    elif sig_type == "DROP_5D":
        threshold = cfg.get("threshold_pct", 0.05)
        for i in range(5, len(values)):
            prev5 = values[i - 5]
            if prev5 > 0:
                drop = (values[i] - prev5) / prev5
                if drop <= -threshold:
                    events.append(
                        SensorSignal(
                            sensor=sensor,
                            signal_date=dates[i],
                            signal_type="DROP_5D",
                            signal_value=drop,
                            horizon_days=horizon_days,
                        )
                    )
    return events


def label_outcomes(
    events: list[SensorSignal],
    crisis_dates: list[str],
) -> list[SensorSignal]:
    """Gán crisis_flag + lead_days cho từng tín hiệu dựa trên lịch crisis VN.

    WHY: lead_days = số phiên từ signal đến ngày crisis ĐẦU TIÊN trong cửa sổ
    [signal_date, signal_date + horizon]. Nếu không có crisis trong cửa sổ →
    crisis_flag=0, lead_days=None. Crisis dates là danh sách ngày VN được đánh
    dấu CRISIS (từ regime_history.status = 'CRISIS'/'CRISIS_WARNING').

    Args:
        events: tín hiệu thô từ detect_signal_events
        crisis_dates: ngày crisis VN (sắp xếp tăng dần)

    Returns:
        Các SensorSignal đã gán outcome (crisis_flag, lead_days).
    """
    from datetime import datetime

    set(crisis_dates)
    crisis_sorted = sorted(crisis_dates)
    out: list[SensorSignal] = []
    for ev in events:
        # Tìm ngày crisis đầu tiên >= signal_date và <= signal_date + horizon
        first_crisis: str | None = None
        for cd in crisis_sorted:
            if cd < ev.signal_date:
                continue
            try:
                sdt = datetime.strptime(ev.signal_date, "%Y-%m-%d")
                cdt = datetime.strptime(cd, "%Y-%m-%d")
            except ValueError:
                continue
            if (cdt - sdt).days <= ev.horizon_days:
                first_crisis = cd
                break
            if (cdt - sdt).days > ev.horizon_days:
                break

        if first_crisis is not None:
            try:
                sdt = datetime.strptime(ev.signal_date, "%Y-%m-%d")
                cdt = datetime.strptime(first_crisis, "%Y-%m-%d")
                lead = (cdt - sdt).days
            except ValueError:
                lead = None
            ev.crisis_flag = 1
            ev.lead_days = lead
        else:
            ev.crisis_flag = 0
            ev.lead_days = None
        out.append(ev)
    return out


# ── Ingest from real macro_history + regime_history ───────────────────


def _load_macro_series(variable: str) -> list[tuple]:
    """Đọc chuỗi (date, value) của một biến từ macro_history, de-dup.

    WHY: macro_history có thể chứa nhiều dòng cùng ngày (cập nhật lại giá trị).
    Giữ dòng gần nhất, sắp xếp tăng dần — giống _fetch_macro_series của
    MacroGovernor để một nguồn chân lý về cách đọc dữ liệu.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date, value FROM macro_history WHERE variable = ? ORDER BY date",
            (variable,),
        ).fetchall()
    dedup: dict[str, float] = {}
    for r in rows:
        dedup[r["date"]] = r["value"]  # dòng sau ghi đè dòng trước → keep last
    items = [(d, float(v)) for d, v in sorted(dedup.items()) if v is not None]
    return items


def _load_crisis_dates() -> list[str]:
    """Lấy ngày VN được đánh dấu CRISIS/CRISIS_WARNING từ regime_history."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date FROM regime_history WHERE status IN ('CRISIS', 'CRISIS_WARNING') ORDER BY date"
        ).fetchall()
    return [r["date"] for r in rows]


def ingest_from_macro_history(
    sensor: str,
    variable: str | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    require_full_horizon: bool = True,
) -> int:
    """Xây hồ sơ cảm biến từ dữ liệu macro_history + regime_history thật.

    Pipeline: load series → detect_signal_events → label_outcomes → record.

    WHY require_full_horizon: tín hiệu xuất hiện gần ngày hiện tại chưa có
    đủ horizon_days để biết có crisis hay không. Nếu vẫn record sẽ bị đếm nhầm
    thành false alarm (thiếu dữ liệu ≠ không có crisis) — lookahead bias ngược.
    Chỉ record tín hiệu đã đóng cửa sổ: signal_date + horizon <= max(crisis date
    cuối cùng trong dữ liệu).

    Args:
        sensor: tên cảm biến (KOSPI/SOX/DXY/KRW) — phải có trong SENSOR_SIGNAL_DEFS
        variable: tên biến trong macro_history; mặc định = sensor
        horizon_days: cửa sổ kiểm định
        require_full_horizon: bỏ qua tín hiệu chưa đóng cửa sổ (chống bias)

    Returns:
        Số quan sát đã record.
    """
    var = variable or sensor
    items = _load_macro_series(var)
    if len(items) < 6:
        return 0
    dates = [d for d, _ in items]
    values = [v for _, v in items]
    events = detect_signal_events(sensor, dates, values, horizon_days)
    if not events:
        return 0

    crisis_dates = _load_crisis_dates()
    if not crisis_dates:
        return 0
    labeled = label_outcomes(events, crisis_dates)

    # Lọc tín hiệu đã đóng cửa sổ
    max_known = crisis_dates[-1]
    from datetime import datetime, timedelta

    closed: list[SensorSignal] = []
    for ev in labeled:
        if not require_full_horizon:
            closed.append(ev)
            continue
        try:
            sdt = datetime.strptime(ev.signal_date, "%Y-%m-%d")
        except ValueError:
            continue
        if (sdt + timedelta(days=ev.horizon_days)).date().isoformat() <= max_known:
            closed.append(ev)

    for ev in closed:
        record_signal(ev)
    return len(closed)


# ── Reporting ─────────────────────────────────────────────────────────


def format_profile(profile: SensorProfile, lang_mode: str = "annotated") -> str:
    """Render hồ sơ cảm biến thành text cho CLI."""
    lines: list[str] = []
    lines.append("=" * 62)
    lines.append(f"  SENSOR PROFILE — {profile.sensor}")
    lines.append(f"  Horizon: {profile.horizon_days} phiên")
    lines.append("=" * 62)
    if profile.n == 0:
        lines.append("  Chưa có dữ liệu hồ sơ. Chạy label + record trước.")
        return "\n".join(lines)

    lines.append(f"  n (số tín hiệu)          : {profile.n}")
    lines.append(f"  n crisis (trong horizon)  : {profile.n_crisis}")
    lines.append(f"  P(Crisis | Signal)        : {profile.p_crisis:.1%}")
    lines.append(f"  False alarm rate          : {profile.false_alarm_rate:.1%}")
    if profile.lead_days_mean is not None:
        lines.append(f"  Lead time trung bình      : {profile.lead_days_mean:.1f} phiên")
        lines.append(f"  Lead time trung vị        : {profile.lead_days_median:.1f} phiên")
        lines.append(f"  Lead time min..max        : {profile.lead_days_min}..{profile.lead_days_max}")
    else:
        lines.append("  Lead time                 : N/A (chưa có crisis nào)")

    lines.append("  -- Theo signal_type --")
    for t, st in profile.by_type.items():
        lead_txt = f"{st['lead_days_median']:.1f}" if st["lead_days_median"] is not None else "N/A"
        lines.append(f"    {t:<14s} n={st['n']:<4d} P(C|S)={st['p_crisis']:.1%} lead={lead_txt}")
    lines.append("=" * 62)
    return "\n".join(lines)
