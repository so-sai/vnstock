"""scan_timeseries_hazards.py — KIỂM KÊ (read-only) hai mẫu hình chết người trên dữ liệu chuỗi thời gian.

Chạy:
    python backend/scripts/scan_timeseries_hazards.py [--root backend/src] [--top 25]

MẪU HÌNH 1 — BẪY CHỈ SỐ ÂM
    Python cho phép `days[i - 3]` khi `i < 3`: chỉ số âm **vòng ra cuối mảng** ⇒ lấy dữ liệu
    TƯƠNG LAI đem tính cho phiên đầu chuỗi. C++/Java sẽ ném lỗi; Python thì im lặng.
    Ca thật: `_vnindex_drop_3d` — bỏ guard `if i < 3` là mở đường rò rỉ look-ahead.

MẪU HÌNH 2 — FAIL-OPEN `.get(khoá, giá_trị_lành)`
    `regime.get(T, "RANGING")` biến "KHÔNG BIẾT" thành "ĐƯỜNG THOÁNG" ⇒ phanh không kích.
    Ca thật: nó đã tắt phanh CRISIS ở 28 % số phiên của một cửa sổ OOS và thổi phồng kết quả.

SCRIPT NÀY KHÔNG SỬA GÌ. Nó chỉ kiểm kê để quyết định sửa gì.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Tên biến/đối tượng gợi ý DỮ LIỆU THỊ TRƯỜNG (khác với cấu hình/tham số).
MARKET_TOKENS = (
    "bench", "regime", "close", "open", "high", "low", "price", "volume", "vol",
    "ohlcv", "nav", "foreign", "flow", "adj", "atr", "rs", "adv", "breadth",
    "index", "vnindex", "days", "dates", "net_",
)

#: Giá trị mặc định "lành" — dùng làm mặc định trên dữ liệu thị trường là fail-open.
BENIGN_DEFAULTS = {"0", "0.0", "1", "1.0", "False", "True", "None", "''", '""',
                   "'RANGING'", "'TRENDING'", "'NONE'", "[]", "{}"}


def _guarded_names(func: ast.AST) -> set[str]:
    """Tên biến đã được kiểm biên trong hàm: `if x < n:` / `if x <= n:` / `if n > x:`."""
    guarded: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and len(t.comparators) == 1:
            left, op, right = t.left, t.ops[0], t.comparators[0]
            is_lt = isinstance(op, ast.Lt | ast.LtE)
            is_gt = isinstance(op, ast.Gt | ast.GtE)
            if is_lt and isinstance(left, ast.Name) and isinstance(right, ast.Constant):
                guarded.add(left.id)
            elif is_gt and isinstance(right, ast.Name) and isinstance(left, ast.Constant):
                guarded.add(right.id)
    return guarded


def scan_negative_index(root: Path) -> list[tuple[str, int, str, str, bool]]:
    """[(file, line, hàm, biểu thức, đã-có-guard?)] cho mọi truy cập `x[i-k]` / `x[-k]`."""
    out: list[tuple[str, int, str, str, bool]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
            guarded = _guarded_names(func)
            for node in ast.walk(func):
                if not isinstance(node, ast.Subscript):
                    continue
                sl = node.slice
                var: str | None = None
                const: int | None = None
                if isinstance(sl, ast.BinOp) and isinstance(sl.op, ast.Sub):
                    if isinstance(sl.left, ast.Name) and isinstance(sl.right, ast.Constant):
                        var, const = sl.left.id, sl.right.value if isinstance(sl.right.value, int) else None
                elif isinstance(sl, ast.UnaryOp) and isinstance(sl.op, ast.USub):
                    if isinstance(sl.operand, ast.Constant) and isinstance(sl.operand.value, int):
                        const = sl.operand.value
                if var is None and const is None:
                    continue
                try:
                    expr = ast.unparse(node)
                except Exception:  # noqa: BLE001 - unparse chỉ để in, không được làm chết scan
                    expr = "<unparseable>"
                has_guard = var in guarded if var else True  # x[-k] hằng số: không tự suy được guard
                out.append((str(path.resolve()), node.lineno, func.name, expr, has_guard))
    return out


def scan_fail_open(root: Path) -> list[tuple[str, int, str, str]]:
    """[(file, line, biểu thức, mức nghi ngờ)] cho `.get(khoá, mặc_định_lành)`."""
    out: list[tuple[str, int, str, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "get") or len(node.args) < 2:
                continue
            try:
                recv = ast.unparse(f.value)
                expr = ast.unparse(node)
                default = ast.unparse(node.args[1])
            except Exception:  # noqa: BLE001
                continue
            if default not in BENIGN_DEFAULTS:
                continue
            hay = f"{recv} {expr}".lower()
            hits = [t for t in MARKET_TOKENS if t in hay]
            if hits:
                out.append((str(path.resolve()), node.lineno, expr,
                            "DỮ LIỆU THỊ TRƯỜNG: " + ",".join(sorted(set(hits))[:4])))
    return out


#: Câu lệnh ĐỌC dữ liệu — chỉ những câu này mới cần vị từ hợp lệ.
_SQL_READERS = ("read_sql", "read_sql_query", "execute", "executemany")
#: Dấu hiệu MỘT câu truy vấn đã kiểm lưới cách ly.
_VALIDITY_MARKERS = ("valid_row_sql", "is_stale")
_PRICE_TABLE = "daily_ohlcv"
#: Cột giá/khối lượng — CHỈ truy vấn ĐỌC chúng mới có thể nuốt dòng đã cách ly.
#: `SELECT MAX(date)` / `SELECT DISTINCT symbol` không đọc giá trị nào ⇒ không thể rò rỉ.
#: (Đây là bản tinh chỉnh để loại dương tính giả — bản đầu bắt theo BẢNG, chưa theo CỘT.)
_PRICE_COLUMNS = ("close", "open", "high", "low", "volume")


def scan_unfiltered_ohlcv_queries(root: Path) -> list[tuple[str, int, str]]:
    """MẪU HÌNH 3 — [(file, line, SQL)] cho truy vấn `daily_ohlcv` THIẾU chốt chặn hợp lệ.

    VÌ SAO ĐÂY LÀ MẪU HÌNH RỦI RO NHẤT: lưới cách ly (`is_stale=1` + `data_validity`) chỉ có
    tác dụng nếu MỌI đường đọc đều kiểm nó. Ca thật: `breakout_continuation` gõ raw SQL
    không vị từ ⇒ nuốt trọn 36.971 dòng `close <= 0` đã cách ly ⇒ `pct_change(5) = inf`
    ⇒ `OverflowError` làm chết engine. Một câu `SELECT` quên vị từ xoá sạch công sức của
    toàn bộ hệ thống quarantine.
    """
    out: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in _SQL_READERS or not node.args:
                continue
            try:
                txt = " ".join(ast.unparse(a) for a in node.args[:2])
            except Exception:  # noqa: BLE001 - unparse chỉ để in
                continue
            low = txt.lower()
            if _PRICE_TABLE not in low or "select" not in low:
                continue
            if any(m in low for m in _VALIDITY_MARKERS):
                continue
            # Chỉ báo khi câu truy vấn thật sự ĐỌC cột giá/khối lượng (lọc dương tính giả).
            if not any(col in low for col in _PRICE_COLUMNS):
                continue
            out.append((str(path.resolve()), node.lineno, " ".join(txt.split())[:130]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="backend/src")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    root = PROJECT_ROOT / args.root

    neg = scan_negative_index(root)
    unguarded = [r for r in neg if not r[4]]
    print(f"\n=== MẪU HÌNH 1: chỉ số âm ({len(neg)} chỗ, trong đó {len(unguarded)} CHƯA thấy guard) ===")
    for file, line, fn, expr, ok in neg[: args.top]:
        flag = "  " if ok else "⚠ "
        print(f" {flag}{file}:{line}  [{fn}]  {expr}   {'(có guard)' if ok else '(CHƯA GUARD)'}")

    fo = scan_fail_open(root)
    print(f"\n=== MẪU HÌNH 2: fail-open .get trên dữ liệu thị trường ({len(fo)} chỗ) ===")
    for file, line, expr, why in fo[: args.top]:
        print(f" ⚠ {file}:{line}  {expr[:110]}")
        print(f"      → {why}")

    q = scan_unfiltered_ohlcv_queries(root)
    print(f"\n=== MẪU HÌNH 3: truy vấn `daily_ohlcv` THIẾU chốt chặn hợp lệ ({len(q)} chỗ) ===")
    print("    (thiếu cả `is_stale` lẫn `VALID_ROW_SQL` ⇒ đọc thẳng dòng ĐÃ CÁCH LY)")
    for file, line, sql in q[: args.top]:
        print(f" ⚠ {file}:{line}")
        print(f"      {sql}")

    print(
        f"\nTỔNG: chỉ-số-âm chưa guard = {len(unguarded)} · fail-open nghi ngờ = {len(fo)} "
        f"· truy-vấn-thiếu-vị-từ = {len(q)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
