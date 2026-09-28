"""Tests — E8 (2026-09-28): aligner features thật (bug TypeError bị nuốt) + warmup deterministic.

Bug cũ: `TimeSeriesAligner().compute_eigenvalues(target_date=...)` không khớp
signature `compute_eigenvalues(corr_matrix)` → TypeError bị nuốt bởi except
(TypeError nằm trong tuple) → luôn None → spectral_stress=UNKNOWN /
phase=UNCERTAIN conf=0.0 ở toàn bộ 328 record.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.core.macro import macro_state_classifier as msc
from src.core.macro.macro_state_classifier import WARMUP_SEED, MacroStateClassifier
from src.services.macro.time_series_aligner import TimeSeriesAligner

CONTRACT_KEYS = ("lambda_max", "lambda_ratio", "spectral_entropy")


@pytest.fixture()
def clf(tmp_path) -> MacroStateClassifier:
    return MacroStateClassifier(persistence_dir=tmp_path)


class TestAlignerFeatures:
    def test_returns_contract_keys(self, clf):
        """Coordinator.update yêu cầu đúng 3 khoá này (market_macro_coordinator:90)."""
        features = clf._fetch_aligner_features(None)
        assert features is not None, "aligner features vẫn trả về None → bug cũ còn đó"
        for k in CONTRACT_KEYS:
            assert k in features, f"thiếu khoá {k} mà coordinator cần"
            assert isinstance(features[k], (int, float))
        assert 1.0 <= features["lambda_max"] <= len(msc.ALIGNER_ASSETS) + 1
        assert features["lambda_ratio"] > 0

    def test_no_target_date_kwarg_mistake(self, monkeypatch, tmp_path):
        """Phòng hộ: compute_eigenvalues KHÔNG được gọi với kwarg target_date."""
        original = TimeSeriesAligner.compute_eigenvalues

        def spy(self, corr_matrix):
            assert not isinstance(corr_matrix, str), "gọi sai signature (target_date)"
            return original(self, corr_matrix)

        monkeypatch.setattr(TimeSeriesAligner, "compute_eigenvalues", spy)
        features = MacroStateClassifier(persistence_dir=tmp_path)._fetch_aligner_features("2026-06-30")
        assert features is not None

    def test_pit_bounded_no_lookahead(self, clf):
        """target_date cũ → chỉ dùng dữ liệu <= ngày đó (không đọc dữ liệu tương lai)."""
        early = clf._fetch_aligner_features("2022-01-07")
        late = clf._fetch_aligner_features("2026-09-25")
        assert early is not None and late is not None
        assert early["eigenvalues"] != late["eigenvalues"], "features cho 2022 trùng với 2026 → có lookahead"
        # 2022: KOSPI/TAIEX/SHENZHEN chưa có (từ 2023-07), SP500/NASDAQ từ 2025-07
        # → số asset trong ma trận correlation phải nhỏ hơn ngày hôm nay
        assert len(early["eigenvalues"]) < len(late["eigenvalues"]), "số asset 2022 không nhỏ hơn 2026"

    def test_insufficient_data_returns_none_not_crash(self, clf):
        """Trước khi có đủ series → None (spectral=UNKNOWN) chứ không ném lỗi."""
        assert clf._fetch_aligner_features("2021-04-06") is None

    def test_old_broken_call_style_is_gone(self):
        """Guard hồi quy: không còn call compute_eigenvalues(target_date=...) ở dạng CODE."""
        import ast

        src = (PROJECT_ROOT / "backend/src/core/macro/macro_state_classifier.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        bad = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compute_eigenvalues"
            and any(kw.arg == "target_date" for kw in node.keywords)
        ]
        assert not bad, f"vẫn còn {len(bad)} call sai signature tại dòng {bad[0].lineno}"


class TestWarmupDeterministic:
    @pytest.fixture()
    def obs(self) -> np.ndarray:
        return np.array([0.768, 2.0, 2.0, 1.299, -0.8235, 1.3879, 1.0473])

    def _run(self, obs: np.ndarray, tmp_path: Path) -> dict:
        c = MacroStateClassifier(persistence_dir=tmp_path)
        c._engine = msc.PTDEngine()
        c._warmup_bpimm(obs)
        st = c._engine.step(obs, None)
        return {
            "dominant": st.dominant_narrative,
            "novelty": st.novelty_flag,
            "probs": tuple(sorted((k, round(float(v), 10)) for k, v in st.narrative_probs.items())),
            "scalars": (st.position_scalar, st.risk_on_scalar, st.defensive_scalar),
        }

    def test_same_obs_same_output(self, obs, tmp_path):
        a = self._run(obs, tmp_path / "a")
        b = self._run(obs, tmp_path / "b")
        assert a == b, "warmup không deterministic → record không tái lập được"

    def test_seed_constant_is_pinned(self):
        assert WARMUP_SEED == 20260925

    def test_seed_isolated_from_global_numpy_state(self, obs, tmp_path):
        """RNG riêng (default_rng) → không đụng np.random toàn cục."""
        np.random.seed(12345)
        before = np.random.rand(1)
        np.random.seed(12345)
        self._run(obs, tmp_path)
        after = np.random.rand(1)
        assert np.allclose(before, after)
