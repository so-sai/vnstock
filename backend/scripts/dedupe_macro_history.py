#!/usr/bin/env python3
"""dedupe_macro_history.py — GỘP BẢN GHI TRÙNG NGÀY trong macro_state_history.json.

WHY (E6, 2026-09-25):
  MacroStateClassifier._save_state() trước đây append không điều kiện theo date →
  file history chứa:
      2026-09-24 × 4 (US10Y 5.006 vs 5.114, GOLD 4356.0 vs 4302.0 TRANH CHẤP)
      2022-01-07 × 31
  → 365 ô trượt nhưng chỉ 328 ngày unique, G5 fresh_ratio vẫn báo 100%.
  Writer đã được sửa (thay thế tại chỗ cùng date) — script này dọn DỮ LIỆU CŨ.

RULE (zero-hallucination, không đoán):
  File không lưu timestamp flush → không có căn cứ chọn "bản EOD chuẩn".
  Dùng quy tắc duy nhất có thể kiểm chứng: DATE-LOCK = bản ghi XUẤT HIỆN
  CUỐI CÙNG trong file (append order = flush order) thắng, giữ tại VỊ TRÍ lần
  xuất hiện ĐẦU TIÊN (giữ nguyên thứ tự). Mọi trường khác biệt giữa các bản ghi
  cùng ngày được BÁO RA để người vận hành tự đối soát — không tự quyết thay.

Usage:
  python backend/scripts/dedupe_macro_history.py            # DRY RUN (mặc định)
  python backend/scripts/dedupe_macro_history.py --apply    # ghi đè file
  python backend/scripts/dedupe_macro_history.py --path X --apply

Exit codes:
  0 — OK (đã sạch, hoặc đã ghi thành công)
  1 — lỗi đọc/ghi file
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = next(
    (p for p in [Path(__file__).resolve(), *Path(__file__).resolve().parents] if (p / "AGENTS.md").exists()),
    Path(__file__).resolve().parents[2],
)
DEFAULT_PATH = REPO_ROOT / "backend" / "data" / "macro" / "macro_state_history.json"


MAX_CONFLICT_VALUES = 4  # số giá trị tranh chấp tối đa in ra mỗi trường


def _short(value) -> str:
    """Chuỗi rút gọn để đối soát — hiển thị được cả scalar lẫn dict/list."""
    if isinstance(value, (dict, list)):
        s = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        return s if len(s) <= 60 else s[:57] + "..."
    return repr(value)


def dedupe(records: list[dict]) -> tuple[list[dict], dict]:
    """Gộp bản ghi trùng date.

    Returns:
        (kept, report) — kept giữ vị trí lần xuất hiện đầu, nội dung lần xuất
        hiện cuối. report["duplicates"] liệt kê ngày trùng + trường tranh chấp.
    """
    first_pos: dict[str, int] = {}
    last_rec: dict[str, dict] = {}
    all_recs: dict[str, list[tuple[int, dict]]] = {}

    for i, rec in enumerate(records):
        d = rec.get("date")
        if d is None:
            continue
        first_pos.setdefault(d, i)
        last_rec[d] = rec
        all_recs.setdefault(d, []).append((i, rec))

    kept: list[dict] = []
    dropped = 0
    duplicates = []
    for i, rec in enumerate(records):
        d = rec.get("date")
        if d is None:
            kept.append(rec)
            continue
        if first_pos[d] == i:
            kept.append(last_rec[d])
        else:
            dropped += 1

    for d, rows in all_recs.items():
        if len(rows) < 2:
            continue
        keys = {k for _, r in rows for k in r}
        conflicts = {}
        for k in sorted(keys):
            vals = [r.get(k) for _, r in rows]
            if all(isinstance(v, dict) for v in vals):
                # So sánh theo từng khoá con → chỉ liệt kê khoá THẬT SỰ tranh chấp
                sub = {}
                for kk in sorted({kk for v in vals for kk in v}):
                    vs = {_short(v.get(kk)) for v in vals}
                    if len(vs) > 1:
                        sub[kk] = sorted(vs)[:MAX_CONFLICT_VALUES]
                if sub:
                    conflicts[k] = sub
            else:
                seen = []
                for v in vals:
                    s = _short(v)
                    if s not in seen:
                        seen.append(s)
                if len(seen) > 1:
                    shown = seen[:MAX_CONFLICT_VALUES]
                    if len(seen) > MAX_CONFLICT_VALUES:
                        shown.append(f"...(+{len(seen) - MAX_CONFLICT_VALUES})")
                    conflicts[k] = shown
        duplicates.append(
            {
                "date": d,
                "n": len(rows),
                "kept_index": first_pos[d],
                "kept_content_from_index": rows[-1][0],
                "dropped_indices": [i for i, _ in rows[:-1]],
                "conflicts": conflicts,
            }
        )
    duplicates.sort(key=lambda x: x["date"])

    report = {
        "total_in": len(records),
        "total_out": len(kept),
        "removed": dropped,
        "unique_dates": len({r.get("date") for r in records if r.get("date")}),
        "duplicates": duplicates,
    }
    return kept, report


def print_report(report: dict, path: Path) -> None:
    print(f"MACRO HISTORY DEDUPE — {path}")
    print(
        f"  total={report['total_in']} → {report['total_out']} "
        f"(removed={report['removed']}, unique_dates={report['unique_dates']}, "
        f"dates_with_dup={len(report['duplicates'])})"
    )
    for d in report["duplicates"]:
        drops = d["dropped_indices"]
        drop_txt = str(drops[:8])
        if len(drops) > 8:
            drop_txt = drop_txt[:-1] + f", ... +{len(drops) - 8}]"
        print(
            f"  [{d['date']}] n={d['n']} keep_idx={d['kept_index']} "
            f"(content from idx {d['kept_content_from_index']}) drop={drop_txt}"
        )
        if d["conflicts"]:
            for field, vals in d["conflicts"].items():
                if isinstance(vals, dict):
                    detail = ", ".join(f"{k}={v}" for k, v in vals.items())
                else:
                    detail = " || ".join(str(v) for v in vals)
                print(f"      CONFLICT {field}: {detail}")
        else:
            print("      (các bản ghi giống hệt nhau — dedupe vô hại)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dedupe macro_state_history.json theo date")
    ap.add_argument("--path", default=str(DEFAULT_PATH))
    ap.add_argument("--apply", action="store_true", help="Ghi đè file (mặc định: dry run)")
    a = ap.parse_args(argv)

    path = Path(a.path)
    if not path.exists():
        print(f"ERR: không tìm thấy {path}")
        return 1
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERR: đọc JSON thất bại: {e}")
        return 1
    if not isinstance(records, list):
        print(f"ERR: dạng không phải list ({type(records).__name__})")
        return 1

    kept, report = dedupe(records)
    print_report(report, path)

    if not a.apply:
        print("  DRY RUN — không ghi file. Chạy lại với --apply để ghi.")
        return 0
    if report["removed"] == 0:
        print("  Đã sạch — không có gì để ghi.")
        return 0

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    print(f"  ✅ Đã ghi {path} ({report['total_out']} records, removed {report['removed']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
