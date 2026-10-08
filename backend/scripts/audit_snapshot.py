"""audit_snapshot.py — Snapshot BẤT BIẾN của audit_trail theo tháng (option C).

Spec: docs/SPEC_NARRATIVE_LAYER_2026-10.md (Điểm 3 — retention)

Vì sao: `backend/reports/audit_trail.jsonl` là file append-only động → gitignore để
tránh commit vụn mỗi ngày. Nhưng audit record là bằng chứng compliance: xoá nhầm
thư mục reports là mất sạch lịch sử quyết định.

Cơ chế: cuối mỗi kỳ, copy toàn bộ dòng vào
    backend/reports/audit_archive/audit_trail_YYYY-MM.jsonl
rồi commit file đó vào Git (1 commit / tháng, không nhiễu history).

BẤT BIẾN: nếu file archive của tháng đó ĐÃ TỒN TẠI → từ chối (exit 3), trừ khi
`--force`. Một snapshot đã ký không được âm thầm ghi đè.

Usage:
    python backend/scripts/audit_snapshot.py                 # tháng hiện tại
    python backend/scripts/audit_snapshot.py --month 2026-10
    python backend/scripts/audit_snapshot.py --dry-run       # chỉ kiểm, không ghi
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE = PROJECT_ROOT / "backend" / "reports" / "audit_trail.jsonl"
ARCHIVE_DIR = PROJECT_ROOT / "backend" / "reports" / "audit_archive"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

EXIT_OK = 0
EXIT_NO_SOURCE = 1
EXIT_EXISTS = 3


def validate_jsonl(path: Path) -> tuple[int, int]:
    """(số dòng hợp lệ, số dòng hỏng)."""
    ok = bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
            ok += 1
        except json.JSONDecodeError:
            bad += 1
    return ok, bad


def month_of(line: str) -> str | None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    as_of = rec.get("as_of")
    return as_of[:7] if isinstance(as_of, str) and len(as_of) >= 7 else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot bất biến audit_trail theo tháng")
    ap.add_argument("--month", default=datetime.now(VN_TZ).strftime("%Y-%m"),
                    help="YYYY-MM (mặc định: tháng hiện tại)")
    ap.add_argument("--source", default=str(LIVE))
    ap.add_argument("--out-dir", default=str(ARCHIVE_DIR))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Ghi đè snapshot đã tồn tại (phá bất biến!)")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists() or src.stat().st_size == 0:
        print(f"[skip] không có audit trail để snapshot: {src}")
        return EXIT_NO_SOURCE

    ok, bad = validate_jsonl(src)
    total = ok + bad
    if ok == 0:
        print(f"[skip] 0 dòng hợp lệ trong {src}")
        return EXIT_NO_SOURCE

    # Chỉ snapshot các dòng thuộc tháng yêu cầu (audit record phải đúng kỳ kế toán).
    keep = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip() and month_of(ln) == args.month]
    if not keep:
        print(f"[skip] không có bản ghi nào thuộc kỳ {args.month} (tổng {ok} dòng hợp lệ)")
        return EXIT_NO_SOURCE

    out_dir = Path(args.out_dir)
    dest = out_dir / f"audit_trail_{args.month}.jsonl"

    print(f"[info] nguồn={src}  tổng dòng={total} (hợp lệ {ok}, hỏng {bad})")
    print(f"[info] kỳ {args.month}: {len(keep)} bản ghi → {dest}")

    if dest.exists() and not args.force:
        print(f"[REFUSED] snapshot {dest.name} ĐÃ TỒN TẠI — bất biến, không ghi đè. (dùng --force nếu thật sự cần)")
        return EXIT_EXISTS

    if args.dry_run:
        print("[dry-run] không ghi gì.")
        return EXIT_OK

    out_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(keep) + "\n", encoding="utf-8")
    ok2, bad2 = validate_jsonl(dest)
    if bad2:
        print(f"[ERROR] snapshot có {bad2} dòng hỏng — đã xoá để tránh archive bẩn.")
        dest.unlink()
        return EXIT_NO_SOURCE
    print(f"[ok] ghi {ok2} dòng hợp lệ → {dest}")
    print("[next] git add backend/reports/audit_archive/ && git commit -m 'audit: snapshot " + args.month + "'")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
