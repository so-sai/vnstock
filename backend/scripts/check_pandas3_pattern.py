"""check_pandas3_pattern.py — pre-commit hook chặn mẫu hình pandas 3 gây crash.

VÌ SAO
------
pandas 3 (3.0.2 trong venv) KHÔNG còn cho `.loc[:, col] = ...` ĐỔI DTYPE của một
cột đã tồn tại: `column_setitem` cố giữ dtype cũ (thường `str` đến từ SQLite) rồi
raise `TypeError`. Ngày 2026-10-08 lỗi này nằm trong `data_quality.py` và làm
`rs_ranker` chết 4 tháng, kéo theo 7 subsystem tiêu thụ `market_rs.json`.

`ruff` không bắt được vì đây là lỗi NGỮ NGHĨA, không phải lỗi cú pháp/lint
⇒ cần hook riêng.

Dùng như pre-commit hook (nhận danh sách file từ argv) hoặc chạy tay:
    python backend/scripts/check_pandas3_pattern.py [file.py ...]
    python backend/scripts/check_pandas3_pattern.py --all

Exit 1 nếu phát hiện vi phạm (kèm file:dòng + cách sửa).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = PROJECT_ROOT / "backend"

# Không bao giờ quét thư viện cài ngoài / cache / VCS.
SKIP_PARTS = {".venv", "site-packages", "__pycache__", ".git", "node_modules", ".mypy_cache", ".ruff_cache"}
# Marker cho phép: dùng cho dòng CỐ Ý chứa pattern (docstring giải thích, regex của
# guard test, ca kiểm thử khẳng định pandas 3 raise). Phải nêu lý do.
ALLOW_MARKER = "pandas3-ok"

# (tên, regex, gợi ý) — chỉ khớp phần CODE (đã bỏ comment)
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "loc-to-datetime",
        re.compile(r"""\.loc\[\s*:\s*,\s*["'][A-Za-z_][A-Za-z0-9_]*["']\s*\]\s*=\s*pd\.to_datetime"""),
        "gán DatetimeArray vào cột str qua .loc[:, col] ⇒ pandas 3 raise TypeError. "
        "Dùng `df[col] = pd.to_datetime(df[col], ...)` (luôn THAY cột).",
    ),
    (
        "loc-date-any",
        re.compile(r"""\.loc\[\s*:\s*,\s*["']date["']\s*\]\s*="""),
        "gán vào cột 'date' qua .loc[:, 'date'] có thể đổi dtype ⇒ pandas 3 raise. "
        "Dùng `df['date'] = ...`.",
    ),
]


def _skip(path: Path) -> bool:
    """Bỏ qua thư viện ngoài / cache / VCS."""
    return bool(SKIP_PARTS & set(path.parts))


def find_violations(path: Path) -> list[tuple[int, str, str]]:
    if _skip(path):
        return []
    out: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:  # dòng cố ý (có nêu lý do ngay trên cùng dòng)
            continue
        code = line.split("#", 1)[0]  # comment được phép nhắc pattern
        for name, pat, hint in PATTERNS:
            if pat.search(code):
                out.append((i, name, hint))
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Chặn mẫu hình pandas 3 (.loc setitem đổi dtype)")
    ap.add_argument("files", nargs="*", help="file do pre-commit truyền vào")
    ap.add_argument("--all", action="store_true", help="quét toàn bộ backend/")
    args = ap.parse_args()

    if args.all or not args.files:
        targets = sorted(p for p in SCAN_ROOT.rglob("*.py") if not _skip(p))
    else:
        targets = [Path(f) for f in args.files]
        targets = [p for p in targets if p.suffix == ".py" and not _skip(p)]

    # Fail-closed: file CÓ nhưng KHÔNG đọc được ⇒ không được coi là "sạch".
    # File KHÔNG tồn tại thì bỏ qua (pre-commit có thể truyền đường dẫn đã bị xoá).
    picked: list[Path] = []
    unreadable: list[str] = []
    skipped_missing = 0
    for p in targets:
        if not p.exists():
            skipped_missing += 1
            continue
        try:
            p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            unreadable.append(f"{p}: {type(e).__name__}: {e}")
            continue
        picked.append(p)
    if skipped_missing:
        print(f"[info] bỏ qua {skipped_missing} đường dẫn không tồn tại", file=sys.stderr)
    if unreadable:
        for u in unreadable:
            print(f"❌ không đọc được để kiểm tra: {u}", file=sys.stderr)
        print("❌ Commit bị chặn: không xác minh được file (fail-closed).", file=sys.stderr)
        return 1

    bad = 0
    for p in picked:
        for line_no, name, hint in find_violations(p):
            try:
                rel = p.relative_to(PROJECT_ROOT)
            except ValueError:
                rel = p
            print(f"{rel}:{line_no}: [{name}] {hint}", file=sys.stderr)
            bad += 1

    if bad:
        print(
            f"\n❌ pandas 3 pattern: {bad} vi phạm. "
            "Commit bị chặn — sửa sang gán trực tiếp `df[col] = ...`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
