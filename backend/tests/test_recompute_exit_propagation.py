"""Test lan truyền mã thoát của bước con trong `recompute_valuation_scores_pit.py`.

[WHY]
  Bước 3 (2026-10-16 21:11) chạy `--apply` và kết thúc **exit 0** với báo cáo "791 mã OK · 0 lỗi",
  trong khi bước con `[C] migrate_shares_history.py` đã thất bại (`exit=2` vì canary
  `('HPG','2018Q1','CHARTER_PIT')` / `('HPG','2025Q2','CHARTER_PIT')`). Một bước con fail-closed mà
  báo cáo tổng vẫn "thành công" ⇒ người vận hành tin nhầm rằng bảng được dựng trên hạ tầng đã cập nhật.

[ROOT CAUSE]
  `subprocess.call(..., stdout=DEVNULL)` — mã thoát bị bỏ qua và chẩn đoán của script con bị xoá sạch;
  caller không kiểm `rc` nên không dừng. Cùng lớp lỗi với "cấm pipe che mã thoát" trong `AGENTS.md`.

[INVARIANT]
  1. `_run_child` trả ĐÚNG mã thoát của script con (không bao giờ trả 0 thay cho thất bại).
  2. Khi `rc != 0`: in chẩn đoán (20 dòng cuối) để không che giấu lỗi.
  3. `main()` với `--apply` PHẢI dừng và trả mã lỗi riêng (6 cho [A], 7 cho [C]) — không được chạy
     tiếp sang bước backup + tính lại, và không được trả 0.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "recompute_valuation_scores_pit",
    Path(__file__).resolve().parents[1] / "scripts" / "recompute_valuation_scores_pit.py",
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
sys.modules["recompute_valuation_scores_pit"] = mod
_SPEC.loader.exec_module(mod)


# [WHY] Bất biến nền: mã thoát của bước con phải được trả nguyên, không được "làm tròn" thành 0.
@pytest.mark.parametrize("rc", [0, 2, 7])
def test_run_child_tra_dung_ma_thoát_con(rc, capsys):
    fake = subprocess.CompletedProcess(args=["x"], returncode=rc, stdout="", stderr="")
    with mock.patch.object(mod.subprocess, "run", return_value=fake):
        assert mod._run_child("migrate_shares_history.py", "C") == rc


# [WHY] Không che giấu lỗi: khi con thất bại, chẩn đoán của nó phải xuất hiện trong log của cha —
# nếu không, người vận hành chỉ thấy "exit=2" mà không biết vì sao (đúng ca HPG canary).
def test_run_child_in_chan_doan_khi_that_bai(capsys):
    fake = subprocess.CompletedProcess(
        args=["x"], returncode=2, stdout="canary sai: HPG 2018Q1\nKẾT LUẬN: FAIL\n", stderr=""
    )
    with mock.patch.object(mod.subprocess, "run", return_value=fake):
        mod._run_child("migrate_shares_history.py", "C")
    out = capsys.readouterr().out
    assert "CHẨN ĐOÁN" in out and "canary sai" in out and "KẾT LUẬN: FAIL" in out


# [WHY] Ca quan trọng nhất: `--apply` khi bước con thất bại PHẢI dừng trước backup/tính lại và trả mã
# lỗi riêng. Trước khi vá: exit 0 + "791 mã OK" (đã đo) ⇒ phải có ca này để không tái diễn.
def test_main_dung_fail_closed_khi_buoc_c_that_bai(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["recompute_valuation_scores_pit.py", "--apply"])
    monkeypatch.setattr(mod, "wait_for_idle", lambda *_a, **_k: (True, 0))
    monkeypatch.setattr(mod, "_run_child", lambda _script, tag: 2 if tag == "C" else 0)
    with mock.patch.object(mod, "ValuationEngine", side_effect=AssertionError("KHÔNG được tính lại")):
        rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 7, f"phải dừng bằng mã 7, nhận {rc}"
    assert "FAIL — bước [C]" in out


# [WHY] Đối xứng cho [A]: bước con thứ nhất thất bại cũng phải dừng (mã 6) — nếu chỉ chặn [C] thì
# đường [A] vẫn có thể ghi dữ liệu facts thiếu `effective_date`.
def test_main_dung_fail_closed_khi_buoc_a_that_bai(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["recompute_valuation_scores_pit.py", "--apply"])
    monkeypatch.setattr(mod, "wait_for_idle", lambda *_a, **_k: (True, 0))
    monkeypatch.setattr(mod, "_run_child", lambda _script, tag: 3 if tag == "A" else 0)
    monkeypatch.setattr(mod, "current_state", lambda _db: None)
    monkeypatch.setattr(mod, "missing_effective_date", lambda _db: 5)
    with mock.patch.object(mod, "ValuationEngine", side_effect=AssertionError("KHÔNG được tính lại")):
        rc = mod.main()
    assert rc in {6, 7}, f"phải dừng bằng 6 (hoặc 7 nếu [A] không chạy), nhận {rc}"
