"""Hợp đồng BIÊN: lỗi/"không biết" không được mang hình dạng của dữ liệu hợp lệ.

[WHY]
    Sự cố musl libc + OpenSSH (vá 05/11/2026, OpenSSH 10.6p2) không phải lỗi bộ nhớ mà là
    **In-band Error Signaling**: `crypt()` báo "không tính được" bằng chuỗi `"*"` — trùng đúng
    ký hiệu "tài khoản bị khoá" của `/etc/shadow`, nên `strcmp("*", "*") == 0` ⇒ bypass.
    Repo này không có `crypt()`/PAM/sshd nên không thể dính đúng CVE đó, NHƯNG cùng *lớp lỗi*
    thì có, ở tầng ngữ nghĩa: "không đọc được sổ sự kiện" bị báo thành "dữ liệu hoàn hảo".

[ROOT CAUSE được bảo vệ]
    1. `quality_score_engine.compute_report`: registry ném lỗi ⇒ `_empty_report()` trả
       `integrity_score=1.0`, `recommendation="clean (no events)"` — tức "không biết" mang
       nhãn LÀNH. Con số đó chảy vào `cao_validation/activation_gate.py` (`DIS >= 0.50`) và
       `trust_accumulator` (`.get("data_integrity_score", 1.0)`).
    2. `TrustState.data_integrity_score: float = 1.0` — default coi "chưa từng đo" là hoàn hảo.

[INVARIANT]
    (a) registry lỗi ⇒ `integrity_score is None` + `degraded=True` + `recommendation` bắt đầu
        bằng "UNKNOWN"; KHÔNG được là 1.0, KHÔNG được là 0.0 (0.0 cũng là một khẳng định sai);
    (b) cổng activation coi `None` là **KHÔNG ĐẠT** (fail-closed) — "không biết" không được
        vượt cổng 0.50;
    (c) state đọc từ kho mà thiếu trường ⇒ `None`, không được default 1.0;
    (d) chống vá quá tay: registry SỐNG nhưng 0 sự kiện ⇒ vẫn `1.0`, `degraded=False`
        (monitor chạy, không có gì để báo) — nếu biến ca này thành "unknown" thì mọi cổng đỏ
        vĩnh viễn ngay sau khi cài đặt, và người trực sẽ tắt cổng.
"""

import json
import sys
from pathlib import Path

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


class _BrokenRegistry:
    """Sổ sự kiện hỏng: mọi lần đọc đều ném lỗi (giống DB/SQLite bị khoá)."""

    def get_events(self, source=None):
        raise RuntimeError("registry không đọc được")


class _EmptyRegistry:
    """Sổ sự kiện SỐNG nhưng chưa có sự kiện nào."""

    def get_events(self, source=None):
        return []


class _StorageMissingDis:
    """Kho bền vững có state nhưng THIẾU trường `data_integrity_score`."""

    def get_belief_value(self, key):
        return json.dumps({"total_samples": 10, "mean_consistency": 0.7, "confidence": 0.7})

    def save_belief_value(self, key, value):
        return True


def test_dq_report_is_unknown_when_registry_read_fails():
    """[WHY] Trung tâm của lớp lỗi: đọc sổ sự kiện lỗi KHÔNG được báo "sạch". Nếu test này
    xanh với `integrity_score == 1.0`, hệ thống đang tự khen mình khi mù thông tin — đúng mẫu
    `strcmp("*","*")` của musl ở tầng số liệu.
    """
    from src.core.data_quality.quality_score_engine import QualityScoreEngine

    report = QualityScoreEngine(registry=_BrokenRegistry()).compute_report()

    assert report.integrity_score is None, "không biết ≠ 1.0 (và cũng ≠ 0.0)"
    assert report.degraded is True
    assert report.recommendation.upper().startswith("UNKNOWN")


def test_empty_registry_stays_clean_and_not_degraded():
    """[WHY] Chống vá quá tay: monitor SỐNG, 0 sự kiện = "không có gì để báo" là trạng thái
    hợp lệ. Nếu biến nó thành unknown, mọi cổng đỏ ngay lần cài đầu và sẽ bị tắt.
    """
    from src.core.data_quality.quality_score_engine import QualityScoreEngine

    report = QualityScoreEngine(registry=_EmptyRegistry()).compute_report()

    assert report.integrity_score == 1.0
    assert report.degraded is False
    assert report.recommendation == "clean"


@pytest.mark.parametrize(
    "dis,expected",
    [(None, False), (0.0, False), (0.49, False), (0.50, True), (1.0, True)],
)
def test_activation_gate_treats_unknown_integrity_as_fail(dis, expected):
    """[WHY] "Không biết" phải là KHÔNG ĐẠT. Nếu `None` lọt qua cổng 0.50 thì mọi lỗi đọc
    sổ sự kiện trở thành giấy thông hành — fail-open đúng nghĩa.
    """
    from src.cao_validation.activation_gate import integrity_gate_passes

    assert integrity_gate_passes(dis) is expected


def test_trust_state_missing_dis_is_unknown_not_perfect():
    """[WHY] `TrustState.data_integrity_score` từng mặc định 1.0 ⇒ state cũ/thiếu trường được
    đọc thành "pipeline hoàn hảo" mà không ai đo. Thiếu dấu vết ⇒ `None`.
    """
    from src.cao_validation.trust_accumulator import TrustAccumulator

    acc = TrustAccumulator(storage=_StorageMissingDis())
    states = acc.get_state()

    assert states, "phải nạp được state từ kho"
    assert all(s.data_integrity_score is None for s in states.values()), {
        r: s.data_integrity_score for r, s in states.items()
    }


def test_trust_update_with_unknown_dis_does_not_accrue_trust():
    """[WHY] Khi DIS chưa biết, KHÔNG được cộng dồn niềm tin (nếu không, một sự cố DQ lại làm
    trust tăng nhanh hơn). Đồng thời state phải giữ `None` — không được ghi 0.0 để "cho đẹp".
    """
    from src.cao_validation.models import ConsistencyScore
    from src.cao_validation.trust_accumulator import TrustAccumulator

    score = ConsistencyScore(
        decision_id="d-1",
        regime="BULL",
        overall=0.9,
        attribution_stability=0.9,
        sign_consistency=0.9,
        sensitivity_invariance=0.9,
        engine_contributions=[],
        delta_alpha_shadow=0.0,
        delta_alpha_real=0.0,
        delta_alpha_agreement=True,
    )
    acc = TrustAccumulator(storage=None)
    state = acc.update(score, data_integrity_score=None)

    assert state.data_integrity_score is None
    history = acc.get_history(limit=5)
    assert history, "vẫn phải ghi lịch sử (để thấy sự kiện), nhưng không cộng niềm tin"
    assert all(p.consistency == 0.0 for p in history), [p.consistency for p in history]
