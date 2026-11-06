"""trading_calendar.py — LOADER DUY NHẤT của SSOT lịch giao dịch VN (Mục 29, NQ-18 §II.2).

[WHY]
    Trước 06/11/2026 lịch tồn tại **ba** bản chép tay và đã lệch nhau thật (`daily_updater`,
    `src/main.rs`, `config/weekend_holidays.json`). Sau khi hợp nhất dữ liệu về một file JSON,
    rủi ro tiếp theo là **hai loader khác chính sách lỗi**: `daily_updater` fail-closed còn
    `time_series_aligner` (macro) lại fail-open (trả `set()` + warning). Cùng một file mà hai
    chính sách lỗi thì "một nguồn sự thật" vẫn có thể sinh hai lịch khác nhau khi file hỏng.
    Module này gom phần đọc file + kiểm tra định dạng về MỘT chỗ để không thể lệch.

[ROOT CAUSE]
    Bản macro dùng `try/except` rộng rồi trả `holidays = set()`: khi file hỏng, mọi ngày lễ
    biến thành phiên — đúng kiểu fail-open mà AGENTS §4A cấm ("không biết" ≠ 0).

[INVARIANT]
    (a) thiếu file / JSON hỏng / danh sách rỗng / ngày sai định dạng ⇒ `TradingCalendarError`;
    (b) KHÔNG trả set rỗng trong bất kỳ nhánh lỗi nào — set rỗng là một khẳng định sai sự thật
        ("không có ngày lễ nào") chứ không phải "chưa biết";
    (c) không import gì nặng (pandas/network) để macro (`VNSessionCalendar`) dùng được mà không
        kéo theo toàn bộ `daily_updater`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# [WHY] Đường dẫn suy ra từ chính file này: `backend/src/database/trading_calendar.py` →
# `parents[1]` = `backend/src` → `config/weekend_holidays.json`. Caller có thể truyền danh sách
# khác (ví dụ `daily_updater` cần thêm đường dẫn cho bản đóng gói PyInstaller) — nhưng mặc định
# phải trỏ đúng file SSOT đã được kiểm chứng bằng dữ liệu.
DEFAULT_CALENDAR_PATHS: tuple[Path, ...] = (Path(__file__).resolve().parents[1] / "config" / "weekend_holidays.json",)

_SCHEMA_VERSION_MIN = 2


class TradingCalendarError(RuntimeError):
    """[WHY] Lịch hỏng/thiếu là lỗi CẤU HÌNH, không phải lỗi dữ liệu thị trường: phải dừng
    ngay và nói rõ đường dẫn, nếu không người trực sẽ đi tìm sai chỗ (như sự cố 31/08/2026).
    """


def load_vn_holidays(paths: tuple[Path, ...] | list[Path] | None = None) -> frozenset[str]:
    """Đọc ngày nghỉ VN từ SSOT. FAIL-CLOSED: mọi bất thường ⇒ `TradingCalendarError`."""
    candidates = tuple(DEFAULT_CALENDAR_PATHS if paths is None else paths)
    path = next((Path(p) for p in candidates if Path(p).exists()), None)
    if path is None:
        raise TradingCalendarError(
            "Không tìm thấy SSOT lịch giao dịch `weekend_holidays.json` (đã thử: "
            f"{[str(p) for p in candidates]}). Không được chạy với lịch rỗng."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        days = payload["holidays"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise TradingCalendarError(f"SSOT lịch giao dịch hỏng ({path}): {e}") from e
    if not isinstance(days, list) or not days:
        raise TradingCalendarError(f"SSOT lịch giao dịch rỗng/không phải list ({path}) — fail-closed.")

    version = payload.get("schema_version")
    if isinstance(version, int) and version < _SCHEMA_VERSION_MIN:
        # [WHY] Schema v1 chỉ là danh sách ngày lễ chưa được đối chiếu dữ liệu (thiếu 14 ngày
        # nghỉ, thừa 2 phiên Tết 2024). Đọc nó như thể hợp lệ sẽ âm thầm tái diễn đúng lỗi cũ.
        raise TradingCalendarError(
            f"SSOT lịch giao dịch schema_version={version} < {_SCHEMA_VERSION_MIN} ({path}) — "
            "bản cũ chưa được đối chiếu dữ liệu, không được dùng."
        )

    out: set[str] = set()
    for d in days:
        if not isinstance(d, str):
            raise TradingCalendarError(f"SSOT lịch giao dịch có phần tử không phải chuỗi: {d!r}")
        try:
            out.add(datetime.strptime(d, "%Y-%m-%d").date().isoformat())
        except ValueError as e:
            raise TradingCalendarError(f"SSOT lịch giao dịch có ngày sai định dạng: {d!r}") from e
    return frozenset(out)
