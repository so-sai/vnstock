# -*- coding: utf-8 -*-
"""EOD SUCCESS phải được ghi vào `eod_run_ledger` (Mục 24, NQ-16 §III, 04/11/2026).

[WHY]
    Phiên 03/11/2026 hạch toán THÀNH CÔNG: `paper_equity_curve` có dòng
    (841.146.848,35), breadth + regime đã chốt, `ptck.py eod-run` exit 0 — nhưng
    `eod_run_ledger.as_of_date = '2026-11-03'` vẫn mang `status = FAILED`,
    `attempts = 1`, `updated_at = 11:46:37` (dòng nợ của lần chạy 11:46 khi chưa có nến).
    Sổ cái vận hành nói DỐI về phiên đã thành công ⇒ lần chạy hôm sau sẽ coi 03/11 là
    ngày nợ và bù lại (double-entry), đúng thứ mà ledger sinh ra để chống.

[ROOT CAUSE]
    `run_eod_pipeline()` gọi `return result` NGAY BÊN TRONG khối
    `with global_transaction(...)`. Câu lệnh `return` thoát khỏi hàm (và thoát `with`)
    nên toàn bộ "LEDGER FALLBACK" phía dưới — nơi duy nhất ghi `STATUS_SUCCESS` cho
    phiên hiện tại — KHÔNG BAO GIỜ chạy. Đường SUCCESS vì thế chỉ tồn tại trên giấy:
    ledger chỉ nhận FAILED (nhánh lỗi) hoặc SUCCESS phát sinh gián tiếp từ catch-up
    (`detect_gap_days` thấy equity đã có). Vì ledger đóng băng từ 16/07/2026 (Mục 21),
    lỗi này chưa từng lộ ra.

[INVARIANT]
    (a) Khi hạch toán SUCCESS, ledger PHẢI có `status = SUCCESS` cho `as_of_date`;
    (b) đường SUCCESS KHÔNG được ghi kèm `FAILED` (không sinh nợ giả);
    (c) đường lỗi vẫn phải ghi `FAILED` (không được "vá" bằng cách bỏ luôn nhánh lỗi);
    (d) không có `return` nào nằm trong khối `with global_transaction(...)` — chốt cấu
        trúc để một lần refactor sau không tái sinh đúng lỗi này.
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EOD_RUNNER = BACKEND_DIR / "src" / "engine" / "eod_runner.py"


def _install_success_stubs(monkeypatch, ledger_calls, *, failing=False):
    """Thay mọi phụ thuộc nặng (DB, engine, transaction) bằng stub thuần trong RAM."""
    import src.daily_updater as daily_updater
    import src.database.acid as acid
    import src.engine.eod_runner as eod
    import src.engine.paper_trading_engine as pte

    class _FakeCursor:
        """[WHY] Từ 05/11/2026 pipeline đọc `DISTINCT date` để kiểm tra phiên liền trước;
        conn giả phải trả về đúng phiên liền trước của ngày test, nếu không cổng Mục 26
        sẽ chặn và test này đo sai thứ nó muốn đo.
        """

        def __iter__(self):
            return iter([("2026-11-02",), ("2026-11-04",)])

    class _FakeConn:
        def execute(self, _sql, _params=()):
            return _FakeCursor()

    class _FakeTxn:
        def __enter__(self):
            return (_FakeConn(), SimpleNamespace(correlation_id="corr-test"))

        def __exit__(self, *exc):
            return False

    def _fake_run_daily(*args, **kwargs):
        if failing:
            raise RuntimeError("accounting boom")
        return {"summary": {}, "orders": [], "decision_date": kwargs.get("decision_date")}

    monkeypatch.setattr(acid, "global_transaction", lambda *a, **k: _FakeTxn(), raising=True)
    monkeypatch.setattr(pte.PaperTradingEngine, "run_daily", staticmethod(_fake_run_daily))
    monkeypatch.setattr(daily_updater, "run_post_update_engines", lambda *a, **k: {})
    monkeypatch.setattr(eod, "_is_already_processed", lambda *a, **k: False, raising=True)
    monkeypatch.setattr(eod, "count_eod_bars", lambda *a, **k: 800, raising=True)
    monkeypatch.setattr(eod, "is_non_trading_day", lambda *a, **k: False, raising=True)
    monkeypatch.setattr(eod, "assert_eod_bars_present", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(eod, "_catchup_gap_days", lambda *a, **k: {"gap_days": []}, raising=True)

    def _fake_record(as_of_date, portfolio_id, status, **kwargs):
        ledger_calls.append({"as_of_date": as_of_date, "status": status, **kwargs})

    monkeypatch.setattr(eod, "_record_ledger", _fake_record, raising=True)
    return eod


# ===================================================================
# 1. Đường THÀNH CÔNG phải ghi SUCCESS
# ===================================================================
def test_success_path_records_ledger_success(monkeypatch):
    """[WHY] Đây chính là lỗi 03/11: equity + breadth + regime đã chốt nhưng ledger vẫn
    FAILED. Nếu test này đỏ, hậu quả thật là phiên đã xong bị coi là nợ ⇒ catch-up bù
    lại lần nữa ⇒ double-entry.
    """
    ledger_calls: list[dict] = []
    eod = _install_success_stubs(monkeypatch, ledger_calls)

    result = eod.run_eod_pipeline(as_of_date="2026-11-03", catchup=True)

    assert result["status"] == "SUCCESS"
    assert ledger_calls, "đường SUCCESS KHÔNG ghi gì vào eod_run_ledger"
    assert [c["status"] for c in ledger_calls] == [eod.STATUS_SUCCESS], ledger_calls
    assert ledger_calls[0]["as_of_date"] == "2026-11-03"


def test_success_path_does_not_write_failed(monkeypatch):
    """[WHY] Chống vá quá tay: sửa để "có ghi ledger" mà ghi cả FAILED thì sổ cái lại
    nói dối theo hướng ngược lại (nợ giả ⇒ catch-up bù một phiên đã xong).
    """
    ledger_calls: list[dict] = []
    eod = _install_success_stubs(monkeypatch, ledger_calls)

    eod.run_eod_pipeline(as_of_date="2026-11-03", catchup=True)

    assert eod.STATUS_FAILED not in [c["status"] for c in ledger_calls], ledger_calls


# ===================================================================
# 2. Đường LỖI vẫn phải ghi FAILED (không được xoá nhánh nợ)
# ===================================================================
def test_failure_path_still_records_ledger_failed(monkeypatch):
    """[WHY] Bản vá chỉ được phép "mở đường" cho SUCCESS, tuyệt đối không làm mất bản
    ghi nợ — đó là lớp bảo vệ duy nhất chống EOD chết âm thầm (Mục 21).
    """
    ledger_calls: list[dict] = []
    eod = _install_success_stubs(monkeypatch, ledger_calls, failing=True)

    with pytest.raises(RuntimeError, match="accounting boom"):
        eod.run_eod_pipeline(as_of_date="2026-11-03", catchup=True)

    assert [c["status"] for c in ledger_calls] == [eod.STATUS_FAILED], ledger_calls
    assert "accounting boom" in (ledger_calls[0].get("last_error") or "")


# ===================================================================
# 3. Chốt cấu trúc: không `return` trong `with global_transaction(...)`
# ===================================================================
def test_no_return_inside_global_transaction_block():
    """[WHY] Bất biến cấu trúc, không phụ thuộc stub: một `return` trong khối
    `with global_transaction(...)` sẽ bỏ qua ledger fallback và tái sinh đúng lỗi này
    ở bất kỳ nhánh nào được thêm sau. Test đọc AST để không bị "vá mồm" bằng đổi tên biến.
    """
    tree = ast.parse(EOD_RUNNER.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.With):
                continue
            calls_global_txn = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "global_transaction"
                for item in sub.items
            )
            if calls_global_txn:
                offenders += [
                    f"{node.name}: return tại dòng {r.lineno}" for r in ast.walk(sub) if isinstance(r, ast.Return)
                ]

    assert not offenders, (
        "`return` bên trong `with global_transaction(...)` sẽ bỏ qua ledger fallback "
        f"(nơi ghi SUCCESS): {offenders}"
    )
