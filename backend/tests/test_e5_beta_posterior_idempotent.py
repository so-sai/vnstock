"""Tests — E5: update_beta_posteriors() phải IDEMPOTENT (2026-09-25).

Bản cũ: alpha = old_alpha + counts(toàn bộ outcomes trong cửa sổ days_back).
→ Mỗi lần daily-close lại cộng thêm CÙNG cửa sổ 90 ngày → alpha/beta bị thổi
phồng (3 lần chạy = ×3) → LR thiên lệch cực đoan.

Bản mới: alpha = PRIOR_ALPHA + gains_in_window (đúng docstring đầu file) —
posterior là HÀM CỦA CỬA SỔ, tính lại từ đầu → chạy 1 hay 100 lần y hệt.

KHÔNG đụng DB thật: fake get_outcomes_for_calibration / get_beta_posteriors /
upsert_beta trong namespace của calibrator.
"""

import sys
from pathlib import Path

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import src.calibration.calibrator as calibrator


def _fake_backend(monkeypatch, outcomes, store=None):
    """Thay 3 dependency DB bằng in-memory dict. Trả về store."""
    store = {} if store is None else store
    monkeypatch.setattr(calibrator, "get_outcomes_for_calibration", lambda days_back=90: outcomes)
    monkeypatch.setattr(calibrator, "get_beta_posteriors", lambda: dict(store))
    monkeypatch.setattr(
        calibrator,
        "upsert_beta",
        lambda key, alpha, beta: store.__setitem__(key, (alpha, beta)),
    )
    return store


def _row(macro_state, outcome, **extra):
    row = {c: "UNKNOWN" for c in calibrator.EVIDENCE_COLUMNS}
    row["macro_state"] = macro_state
    row["outcome"] = outcome
    row.update(extra)
    return row


class TestE5Idempotent:
    def test_repeat_calls_do_not_inflate(self, monkeypatch):
        outcomes = [_row("CREDIT_STRESS", 1.0), _row("CREDIT_STRESS", 0.0), _row("RISK_OFF", 1.0)]
        store = _fake_backend(monkeypatch, outcomes)

        calibrator.update_beta_posteriors(days_back=90)
        after_first = dict(store)
        assert after_first["macro_state::CREDIT_STRESS"] == (
            calibrator.PRIOR_ALPHA + 1,
            calibrator.PRIOR_BETA + 1,
        )

        for _ in range(4):  # 5 lần chạy liên tiếp như 5 lần daily-close
            calibrator.update_beta_posteriors(days_back=90)
        assert store == after_first, "posterior bị cộng dồn đè — không idempotent"

    def test_counts_are_not_summed_across_calls(self, monkeypatch):
        """Sau N lần gọi: alpha vẫn = prior + N_gains, KHÔNG phải prior + N*N_gains."""
        outcomes = [_row("AI_BOOM", 1.0)] * 7 + [_row("AI_BOOM", 0.0)] * 3
        store = _fake_backend(monkeypatch, outcomes)

        for _ in range(10):
            calibrator.update_beta_posteriors(days_back=90)

        alpha, beta = store["macro_state::AI_BOOM"]
        assert alpha == calibrator.PRIOR_ALPHA + 7
        assert beta == calibrator.PRIOR_BETA + 3

    def test_key_out_of_window_resets_to_prior(self, monkeypatch):
        """Key có trong DB nhưng không còn outcome trong cửa sổ → về prior."""
        store = {"macro_state::OLD_KEY": (99.0, 77.0)}
        _fake_backend(monkeypatch, [_row("CREDIT_STRESS", 1.0)], store)

        calibrator.update_beta_posteriors(days_back=90)
        assert store["macro_state::OLD_KEY"] == (calibrator.PRIOR_ALPHA, calibrator.PRIOR_BETA)

    def test_empty_window_writes_nothing(self, monkeypatch):
        store = {"macro_state::KEEP_ME": (4.0, 2.0)}
        _fake_backend(monkeypatch, [], store)
        calibrator.update_beta_posteriors(days_back=90)
        assert store == {"macro_state::KEEP_ME": (4.0, 2.0)}

    def test_all_evidence_columns_counted(self, monkeypatch):
        row = _row("CREDIT_STRESS", 1.0, transmission_phase="HEAD")
        store = _fake_backend(monkeypatch, [row])
        calibrator.update_beta_posteriors(days_back=90)

        for col in calibrator.EVIDENCE_COLUMNS:
            key = f"{col}::{row[col]}"
            assert key in store, f"thiếu key {key}"
            assert store[key] == (calibrator.PRIOR_ALPHA + 1, calibrator.PRIOR_BETA)
