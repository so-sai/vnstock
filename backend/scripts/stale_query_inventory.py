"""stale_query_inventory.py — KIỂM KÊ (inventory-only) các truy vấn đọc daily_ohlcv.

MỤC ĐÍCH
--------
Sau sự cố `is_stale` (phải vá `breadth_engine` rồi mới phát hiện `regime_engine`
cũng dính), cần biết CHÍNH XÁC còn bao nhiêu chỗ đọc `daily_ohlcv` mà không lọc
`is_stale`. Đây là **kiểm kê để chia sprint**, KHÔNG phải bản sửa: script này
**tuyệt đối không sửa code**, chỉ đọc và ghi báo cáo.

Vì sao KHÔNG sửa hàng loạt ngay: 100+ chỗ nằm rải trong hàng chục module, sửa
đồng loạt mà không có test riêng cho từng module là blast-radius lớn hơn lợi ích.
Danh mục này là backlog chuẩn cho từng sprint nhỏ.

PHÂN LOẠI
---------
  FILTERED      — SQL có token `is_stale` (cần lọc)
  DELIBERATE    — có `is_stale = 1` (cố ý đọc dòng nhiễm; ghi để không báo nhầm)
  UNFILTERED    — đọc daily_ohlcv mà KHÔNG có `is_stale` ⇒ rủi ro nhiễm
  DYNAMIC       — SQL ghép động (f-string/format) ⇒ không phân tích tĩnh được

Usage:
    python backend/scripts/stale_query_inventory.py
    python backend/scripts/stale_query_inventory.py --json-out <path>
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "backend" / "reports" / "audit_archive" / "stale_query_inventory.json"

SCAN_DIRS = [PROJECT_ROOT / "backend" / "src", PROJECT_ROOT / "backend" / "scripts"]
SCAN_FILES = [PROJECT_ROOT / "ptck.py"]

TABLE_RE = re.compile(r"\b(?:from|join)\s+daily_ohlcv\b", re.IGNORECASE)
STALE_ONE_RE = re.compile(r"is_stale\s*=\s*1\b", re.IGNORECASE)
STALE_ANY_RE = re.compile(r"\bis_stale\b", re.IGNORECASE)

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def classify(sql: str) -> str:
    if not TABLE_RE.search(sql):
        return "NOT_APPLICABLE"
    if STALE_ONE_RE.search(sql):
        return "DELIBERATE"
    if STALE_ANY_RE.search(sql):
        return "FILTERED"
    return "UNFILTERED"


def _snippet(sql: str, limit: int = 180) -> str:
    s = " ".join(sql.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def scan_file(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    out: list[dict] = []
    for node in ast.walk(tree):
        sql: str | None = None
        dynamic = False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value
        elif isinstance(node, ast.JoinedStr):  # f-string ⇒ ghép động
            literal = "".join(
                v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if TABLE_RE.search(literal):
                sql, dynamic = literal, True
        if sql is None or not TABLE_RE.search(sql):
            continue
        kind = "DYNAMIC" if dynamic else classify(sql)
        out.append(
            {
                "file": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "line": getattr(node, "lineno", 0),
                "kind": kind,
                "snippet": _snippet(sql),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Kiểm kê truy vấn daily_ohlcv thiếu lọc is_stale")
    ap.add_argument("--json-out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    targets: list[Path] = []
    for d in SCAN_DIRS:
        if d.exists():
            targets += [p for p in d.rglob("*.py") if "__pycache__" not in p.parts]
    targets += [p for p in SCAN_FILES if p.exists()]

    records: list[dict] = []
    for p in sorted(set(targets)):
        records += scan_file(p)

    by_kind: dict[str, int] = {}
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    payload = {
        "generated_at": datetime.now(VN_TZ).isoformat(),
        "scanner": "stale_query_inventory.py (AST, inventory-only)",
        "scanned_files": len(set(targets)),
        "note": (
            "INVENTORY-ONLY: script này không sửa code. UNFILTERED = có 'daily_ohlcv' "
            "trong SQL mà không thấy token 'is_stale'; token có mặt là điều kiện CẦN, "
            "không phải đủ (vẫn phải đọc để chắc là lọc ĐÚNG chiều)."
        ),
        "summary": {
            "total": len(records),
            "unfiltered": by_kind.get("UNFILTERED", 0),
            "filtered": by_kind.get("FILTERED", 0),
            "deliberate": by_kind.get("DELIBERATE", 0),
            "dynamic": by_kind.get("DYNAMIC", 0),
            "by_file_top": {},
        },
        "records": records,
    }

    per_file: dict[str, int] = {}
    for r in records:
        if r["kind"] == "UNFILTERED":
            per_file[r["file"]] = per_file.get(r["file"], 0) + 1
    payload["summary"]["by_file_top"] = dict(sorted(per_file.items(), key=lambda kv: -kv[1])[:15])

    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[inventory] quét {payload['scanned_files']} file → {len(records)} truy vấn daily_ohlcv")
    for k in ("unfiltered", "filtered", "deliberate", "dynamic"):
        print(f"  {k:<11}: {payload['summary'][k]}")
    if per_file:
        print("  top file còn UNFILTERED:")
        for f, n in list(payload["summary"]["by_file_top"].items())[:10]:
            print(f"    {n:>3}  {f}")
    print(f"[inventory] đã ghi → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
