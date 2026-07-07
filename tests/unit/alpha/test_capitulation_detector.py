"""Tests for phase4/capitulation_detector.py — P_cap, S_struct, Scale-In, Abortion, Double Signal, Chaos."""
import math
import random
import pytest
import numpy as np
from src.alpha.capitulation_detector import (
    CapitulationDetector,
    MultiTrancheScaler,
    AbortionProtocol,
    AbortionState,
    ScaleInCampaign,
    Tranche,
    DEFAULT_PARAMS,
    _sigma,
    _clamp,
    campaign_factory,
    compute_max_drawdown_limit,
)


class TestSigmoid:
    def test_sigmoid_midpoint(self):
        assert _sigma(0, 1.0) == pytest.approx(0.5, abs=0.01)

    def test_sigmoid_positive(self):
        assert _sigma(2, 1.0) > 0.8

    def test_sigmoid_negative(self):
        assert _sigma(-2, 1.0) < 0.2

    def test_sigmoid_monotonic(self):
        vals = [_sigma(x, 1.0) for x in range(-5, 6)]
        for i in range(len(vals) - 1):
            assert vals[i + 1] > vals[i]

    def test_clamp(self):
        assert _clamp(-0.5) == 0.0
        assert _clamp(1.5) == 1.0
        assert _clamp(0.5) == 0.5


class TestCapitulationDetector:
    def setup_method(self):
        self.det = CapitulationDetector()

    def test_p_cap_normal_market(self):
        result = self.det.compute_p_cap(
            bdi=0.0, bdi_prev=0.0,
            entropy=1.5, entropy_prev=1.4,
            delta_sa=-0.3, delta_sa_prev=-0.2,
            volume_zscore=0.0,
        )
        assert result.value < 0.85, f"P_cap={result.value} phải < 0.85 ở thị trường bt"

    def test_p_cap_washout(self):
        result = self.det.compute_p_cap(
            bdi=1.5, bdi_prev=-0.5,
            entropy=3.5, entropy_prev=2.5,
            delta_sa=0.3, delta_sa_prev=-0.2,
            volume_zscore=3.0,
        )
        assert result.value >= 0.75, f"P_cap={result.value} phải >= 0.75 ở wash-out"

    def test_p_cap_bdi_accel_dominant(self):
        r1 = self.det.compute_p_cap(0.6, -0.2, 2.0, 1.9, 0.0, 0.0, 0.0)
        r2 = self.det.compute_p_cap(0.0, 0.0, 2.0, 1.9, 0.0, 0.0, 0.0)
        assert r1.value > r2.value, "BDI tăng tốc phải tăng P_cap"

    def test_p_cap_components_accuracy(self):
        result = self.det.compute_p_cap(0.5, -0.3, 3.0, 2.5, 0.2, 0.5, 2.0)
        assert result.components["bdi_accel"] is not None
        assert result.components["entropy_deriv"] is not None
        assert result.components["delta_sa_deriv"] is not None
        assert result.components["vol_zscore"] is not None

    def test_s_struct_healthy(self):
        result = self.det.compute_s_struct(
            pillars_active=3, total_pillars=3,
            ac_latency=1.5, entropy=1.2,
        )
        assert result.value > 0.70, f"S_struct={result.value} phải > 0.70"

    def test_s_struct_broken(self):
        result = self.det.compute_s_struct(
            pillars_active=0, total_pillars=3,
            ac_latency=0.1, entropy=3.2,
        )
        assert result.value < 0.55, f"S_struct={result.value} phải < 0.55"

    def test_s_struct_components(self):
        result = self.det.compute_s_struct(2, 3, 1.0, 1.5)
        assert result.components["pillar_ratio"] == pytest.approx(2 / 3, abs=0.001)
        assert result.components["ac_latency_norm"] == pytest.approx(1.0 / 2.0, abs=0.001)
        assert result.components["entropy_norm"] == pytest.approx(1.5 / 3.5, abs=0.001)

    def test_p_cap_final_washout(self):
        p = self.det.compute_p_cap(0.5, -0.3, 3.2, 2.8, 0.1, 0.4, 2.5)
        s = self.det.compute_s_struct(3, 3, 1.2, 1.0)
        final = self.det.compute_p_cap_final(p, s)
        assert final > 0.55, f"P_cap_final={final} phải > 0.55"

    def test_p_cap_final_bear_rally(self):
        p = self.det.compute_p_cap(0.4, -0.2, 3.0, 2.7, 0.0, 0.3, 1.5)
        s = self.det.compute_s_struct(1, 3, 0.2, 3.0)
        final = self.det.compute_p_cap_final(p, s)
        assert final < 0.70, f"Bear Rally P_cap_final={final} phải < 0.70"

    def test_p_cap_final_edge_zero_sstruct(self):
        p = self.det.compute_p_cap(0.5, -0.5, 3.5, 3.0, 0.3, 0.6, 3.0)
        fake_s = type("obj", (object,), {"value": 0.0})()
        final = self.det.compute_p_cap_final(p, fake_s)
        assert final == 0.0

    def test_p_cap_final_edge_one(self):
        p = self.det.compute_p_cap(0.0, 0.0, 1.5, 1.4, -0.3, -0.2, 0.0)
        fake_s = type("obj", (object,), {"value": 1.0})()
        final = self.det.compute_p_cap_final(p, fake_s)
        assert final == pytest.approx(p.value)

    def test_params_override(self):
        custom = dict(DEFAULT_PARAMS)
        custom["w1"] = 0.0
        p = self.det.compute_p_cap(0.5, -0.5, 1.5, 1.4, 0.0, 0.0, 0.0, params=custom)
        default_p = self.det.compute_p_cap(0.5, -0.5, 1.5, 1.4, 0.0, 0.0, 0.0)
        assert p.value < default_p.value, "w1=0 phải giảm P_cap"


class TestMultiTrancheScaler:
    def test_sizes_sum_to_one(self):
        scaler = MultiTrancheScaler(max_pos=100, s_struct_t0=0.85, n=6, alpha=0.5, gamma=0.0)
        total = sum(t.pct for t in scaler.campaign.tranches)
        assert total == pytest.approx(1.0, abs=0.001)

    def test_increasing_cumulative(self):
        scaler = MultiTrancheScaler(100, 0.85, n=6, alpha=0.5, gamma=0.0)
        for t in scaler.campaign.tranches:
            if t.index > 1:
                prev = scaler.get_tranche(t.index - 1)
                assert t.cumulative > prev.cumulative

    def test_max_pos_campaign_frozen(self):
        scaler = MultiTrancheScaler(100, 0.80, n=6, alpha=0.5, gamma=0.0)
        assert scaler.campaign.max_pos_campaign == 80.0

    def test_gamma_left_skew(self):
        s0 = MultiTrancheScaler(100, 1.0, n=6, alpha=0.5, gamma=0.0)
        sg = MultiTrancheScaler(100, 1.0, n=6, alpha=0.5, gamma=-1.5)
        t1_s0 = s0.get_tranche(1).pct
        t1_sg = sg.get_tranche(1).pct
        assert t1_sg > t1_s0, "Gamma<0 (left-skew) phải tăng tranche đầu"

    def test_adapt_gamma_v_shape(self):
        scaler = MultiTrancheScaler(100, 1.0)
        gamma = scaler.adapt_gamma(v_idx=0.25, v_shape_threshold=0.15)
        assert gamma < 0.0
        assert scaler.campaign.gamma == -1.0

    def test_adapt_gamma_u_shape(self):
        scaler = MultiTrancheScaler(100, 1.0)
        gamma = scaler.adapt_gamma(v_idx=0.05, v_shape_threshold=0.15)
        assert gamma == 0.0

    def test_tranche_states_initial(self):
        scaler = MultiTrancheScaler(100, 1.0)
        for t in scaler.campaign.tranches:
            assert t.state == "PENDING"

    def test_position_values(self):
        scaler = MultiTrancheScaler(max_pos=1000, s_struct_t0=0.70, n=6, alpha=0.5)
        for t in scaler.campaign.tranches:
            pos_value = t.pct * scaler.campaign.max_pos_campaign
            assert pos_value > 0

    def test_n_tranches_min_2(self):
        scaler = MultiTrancheScaler(100, 1.0, n=1)
        assert scaler.campaign.n_tranches >= 2


class TestAbortionProtocol:
    def setup_method(self):
        self.scaler = MultiTrancheScaler(max_pos=100, s_struct_t0=0.85)
        self.abort = AbortionProtocol()

    def test_active_state_initial(self):
        assert self.abort.status.state == AbortionState.ACTIVE

    def test_freeze_on_shock(self):
        s = self.abort.evaluate(p_cap_final=0.45, s_struct=0.8, campaign=self.scaler.campaign)
        assert s.state == AbortionState.FROZEN

    def test_no_freeze_above_threshold(self):
        s = self.abort.evaluate(p_cap_final=0.85, s_struct=0.8, campaign=self.scaler.campaign)
        assert s.state == AbortionState.ACTIVE

    def test_cooldown_after_freeze(self):
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.8, campaign=self.scaler.campaign)
        s = self.abort.evaluate(p_cap_final=0.40, s_struct=0.8, campaign=self.scaler.campaign, dt_hours=1)
        assert s.state == AbortionState.COOLDOWN

    def test_active_after_cooldown_struct_healed(self):
        """Cooldown hoàn tất + S_struct hồi phục → ACTIVE (không còn RESUME)."""
        self.abort.cooldown_hours = 0
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.8, campaign=self.scaler.campaign)
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.8, campaign=self.scaler.campaign, dt_hours=1)
        s = self.abort.evaluate(p_cap_final=0.80, s_struct=0.7, campaign=self.scaler.campaign, dt_hours=72)
        assert s.state == AbortionState.ACTIVE, f"Phải là ACTIVE, không phải {s.state}"

    def test_graduated_exit_if_struct_still_broken(self):
        self.abort.cooldown_hours = 0
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.2, campaign=self.scaler.campaign)
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.2, campaign=self.scaler.campaign, dt_hours=1)
        s = self.abort.evaluate(p_cap_final=0.40, s_struct=0.15, campaign=self.scaler.campaign, dt_hours=72)
        assert s.state == AbortionState.EXITING

    def test_aborted_after_exit_complete(self):
        self.abort.cooldown_hours = 0
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.2, campaign=self.scaler.campaign)
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.2, campaign=self.scaler.campaign, dt_hours=1)
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.15, campaign=self.scaler.campaign, dt_hours=72)
        for _ in range(6):
            self.abort.evaluate(p_cap_final=0.40, s_struct=0.15, campaign=self.scaler.campaign, dt_hours=24)
        assert self.abort.status.state == AbortionState.ABORTED

    def test_filled_pct_tracking(self):
        self.scaler.campaign.tranches[0].state = "FILLED"
        self.scaler.campaign.tranches[1].state = "FILLED"
        s = self.abort.evaluate(p_cap_final=0.40, s_struct=0.3, campaign=self.scaler.campaign)
        assert s.filled_pct > 0

    def test_pending_tranches_skipped_on_freeze(self):
        """Khi đóng băng, các tranche PENDING phải chuyển SKIPPED."""
        self.abort.evaluate(p_cap_final=0.40, s_struct=0.5, campaign=self.scaler.campaign)
        for t in self.scaler.campaign.tranches:
            assert t.state in ("FILLED", "SKIPPED"), f"Tranche {t.index} không được ở {t.state}"


class TestDoubleSignal:
    """Double Signal Protocol: P_cap_final > 0.85 lần 2 trong Cooldown → new campaign."""

    def test_double_signal_state_isolates_stale(self):
        """Tín hiệu kép: campaign cũ bị khóa → DOUBLE_SIGNAL + isolated_stale_tranches."""
        scaler = MultiTrancheScaler(max_pos=100_000, s_struct_t0=0.6)
        scaler.campaign.tranches[0].state = "FILLED"
        scaler.campaign.tranches[1].state = "FILLED"
        abort = AbortionProtocol()
        abort.cooldown_hours = 72

        abort.evaluate(0.45, 0.6, scaler.campaign)  # FREEZE
        abort.evaluate(0.45, 0.6, scaler.campaign, dt_hours=1)  # COOLDOWN
        status = abort.evaluate(0.92, 0.6, scaler.campaign, dt_hours=2)  # DOUBLE SIGNAL!

        assert status.state == AbortionState.DOUBLE_SIGNAL
        assert status.isolated_stale_pct > 0.0
        assert status.double_signal_count == 1
        assert len(scaler.campaign.isolated_stale_tranches) > 0

    def test_double_signal_no_resume_old_campaign(self):
        """Double signal → KHÔNG tiếp tục T3 campaign cũ (các tranche còn lại là REVERTED)."""
        scaler = MultiTrancheScaler(max_pos=100_000, s_struct_t0=0.6)
        scaler.campaign.tranches[0].state = "FILLED"
        scaler.campaign.tranches[1].state = "FILLED"
        abort = AbortionProtocol()
        abort.cooldown_hours = 72

        abort.evaluate(0.45, 0.6, scaler.campaign)
        abort.evaluate(0.45, 0.6, scaler.campaign, dt_hours=1)
        abort.evaluate(0.92, 0.6, scaler.campaign, dt_hours=2)

        for t in scaler.campaign.tranches:
            assert t.state != "FILLED", f"Tranche {t.index} không được còn FILLED"

    def test_double_signal_preserves_correct_stale_pct(self):
        """isolated_stale_pct phải chính xác = % FILLED trước đó."""
        scaler = MultiTrancheScaler(max_pos=100_000, s_struct_t0=0.6)
        scaler.campaign.tranches[0].state = "FILLED"
        scaler.campaign.tranches[1].state = "FILLED"
        scaler.campaign.tranches[2].state = "FILLED"
        filled_pct = sum(t.pct for t in scaler.campaign.tranches if t.state == "FILLED")
        abort = AbortionProtocol()
        abort.cooldown_hours = 72

        abort.evaluate(0.45, 0.6, scaler.campaign)
        abort.evaluate(0.45, 0.6, scaler.campaign, dt_hours=1)
        status = abort.evaluate(0.92, 0.6, scaler.campaign, dt_hours=2)

        assert status.isolated_stale_pct == pytest.approx(filled_pct, abs=0.001)

    def test_double_signal_increments_counter(self):
        """double_signal_count tăng qua nhiều lần double signal."""
        scaler = MultiTrancheScaler(max_pos=100_000, s_struct_t0=0.6)
        scaler.campaign.tranches[0].state = "FILLED"
        abort = AbortionProtocol()
        abort.cooldown_hours = 72

        abort.evaluate(0.45, 0.6, scaler.campaign)
        abort.evaluate(0.45, 0.6, scaler.campaign, dt_hours=1)
        s1 = abort.evaluate(0.92, 0.6, scaler.campaign, dt_hours=2)
        assert s1.double_signal_count == 1, f"Đếm={s1.double_signal_count}"

    def test_double_signal_only_in_cooldown(self):
        """P_cap > 0.85 khi ACTIVE không gây double signal."""
        scaler = MultiTrancheScaler(max_pos=100_000, s_struct_t0=0.6)
        abort = AbortionProtocol()
        abort.cooldown_hours = 72
        s = abort.evaluate(0.95, 0.8, scaler.campaign)
        assert s.state != AbortionState.DOUBLE_SIGNAL, "Không double signal khi ACTIVE"
        assert s.state == AbortionState.ACTIVE, "P_cap cao khi ACTIVE là bình thường"


class TestCampaignFactory:
    def test_campaign_factory_creates_new_campaign(self):
        campaign = campaign_factory(
            max_pos=200_000, s_struct_v2=0.75,
            campaign_id="v2", n_tranches=6,
        )
        assert campaign.campaign_id == "v2"
        assert campaign.max_pos == 200_000
        assert campaign.max_pos_campaign == pytest.approx(150_000, abs=1)

    def test_campaign_factory_has_no_stale_tranches(self):
        """Campaign mới không có isolated_stale_tranches."""
        campaign = campaign_factory(100_000, 0.6, campaign_id="v2")
        assert len(campaign.isolated_stale_tranches) == 0

    def test_campaign_default_id_v1(self):
        campaign = campaign_factory(100_000, 0.6)
        assert campaign.campaign_id == "v1"


class TestMaxDrawdownLimit:
    def test_compute_basic(self):
        result = compute_max_drawdown_limit(
            total_capital=1_000_000,
            isolated_stale_pct=0.13,
            max_drawdown_pct=0.25,
        )
        assert result["stale_capital"] == 130_000.0
        assert result["remaining_capital"] == 870_000.0
        assert result["max_drawdown_limit"] == 250_000.0
        assert result["stale_drawdown_risk"] == 130_000.0
        assert result["available_drawdown"] == 120_000.0

    def test_zero_stale(self):
        result = compute_max_drawdown_limit(1_000_000, 0.0)
        assert result["stale_capital"] == 0.0
        assert result["available_drawdown"] == result["max_drawdown_limit"]

    def test_full_stale(self):
        result = compute_max_drawdown_limit(1_000_000, 1.0)
        assert result["stale_capital"] == 1_000_000.0
        assert result["available_drawdown"] == 0.0

    def test_risk_ratio(self):
        r1 = compute_max_drawdown_limit(1_000_000, 0.25)
        assert r1["risk_ratio"] == pytest.approx(0.25 / 0.25, abs=0.001)  # 1.0
        r2 = compute_max_drawdown_limit(1_000_000, 0.0)
        assert r2["risk_ratio"] == 0.0


class TestIntegration:
    def test_compute_capitulation_status(self):
        from src.alpha.capitulation_detector import compute_capitulation_status
        snapshot = {
            "cau_truc": {"so_tru_con_lai": 1, "tong_so_tru": 3, "entropy": 3.0, "bdi": 0.4},
            "regime": {"trang_thai": "RANGING"},
            "delta_divergence": {"ac_latency": 0.15, "delta_sa": 0.2},
        }
        result = compute_capitulation_status(snapshot)
        assert "p_cap" in result
        assert "s_struct" in result
        assert "p_cap_final" in result
        assert "canh_mua" in result
        assert result["canh_mua"] in (True, False)

    def test_in_bao_cao(self, capsys):
        from src.alpha.capitulation_detector import in_bao_cao
        snapshot = {
            "cau_truc": {"so_tru_con_lai": 2, "tong_so_tru": 3, "entropy": 2.0, "bdi": 0.1},
            "regime": {"trang_thai": "RANGING"},
            "delta_divergence": {"ac_latency": 0.8, "delta_sa": -0.1},
        }
        in_bao_cao(snapshot)
        captured = capsys.readouterr()
        assert "P_cap" in captured.out or "CAPITULATION" in captured.out


# ═══════════════════════════════════════════════════════════════
# CHAOS GENERATOR + FUZZ TEST
# ═══════════════════════════════════════════════════════════════

class ChaosGenerator:
    """Sinh chuỗi (P_cap_final, S_struct) ngẫu nhiên để fuzz test state machine."""

    @staticmethod
    def normal(length: int = 100) -> list[tuple[float, float]]:
        return [(round(random.uniform(0.0, 1.0), 4),
                 round(random.uniform(0.0, 1.0), 4)) for _ in range(length)]

    @staticmethod
    def extreme(num_seqs: int = 10, length: int = 50) -> list[list[tuple[float, float]]]:
        seqs = []
        for _ in range(num_seqs):
            seq = []
            for _ in range(length):
                if random.random() < 0.3:
                    pcap = round(random.choice([0.0, 0.01, 0.99, 1.0]), 4)
                    sstruct = round(random.choice([0.0, 0.01, 0.99, 1.0]), 4)
                else:
                    pcap = round(random.uniform(0.0, 1.0), 4)
                    sstruct = round(random.uniform(0.0, 1.0), 4)
                seq.append((pcap, sstruct))
            seqs.append(seq)
        return seqs

    @staticmethod
    def double_bottom() -> list[tuple[float, float]]:
        """Mô phỏng kịch bản Double Bottom: sốc → phục hồi → sốc lại."""
        return (
            [(0.60, 0.70)] * 3   # bình thường
            + [(0.30, 0.25)] * 2  # FREEZE
            + [(0.45, 0.35)] * 5  # COOLDOWN
            + [(0.90, 0.55)] * 1  # DOUBLE SIGNAL!
            + [(0.45, 0.30)] * 5  # COOLDOWN v2
            + [(0.88, 0.50)] * 1  # DOUBLE SIGNAL v2!
            + [(0.80, 0.65)] * 3  # phục hồi
        )

    @staticmethod
    def sawtooth() -> list[tuple[float, float]]:
        """P_cap dao động liên tục qua ngưỡng freeze."""
        seq = []
        for i in range(100):
            pcap = 0.48 + 0.10 * math.sin(i * 0.5)
            sstruct = 0.5 + 0.3 * math.cos(i * 0.3)
            seq.append((round(max(0.0, min(1.0, pcap)), 4),
                        round(max(0.0, min(1.0, sstruct)), 4)))
        return seq


class TestFuzzAbortion:
    """Fuzz Tests — không crash, không deadlock, không resume sai."""

    LIST_LENGTHS = [5, 50, 200]

    @pytest.mark.parametrize("length", LIST_LENGTHS)
    def test_fuzz_no_crash(self, length):
        """Chuỗi ngẫu nhiên không làm crash state machine."""
        random.seed(42 + length)
        seq = ChaosGenerator.normal(length)
        scaler = MultiTrancheScaler(100, 0.8)
        abort = AbortionProtocol()
        for pcap, sstruct in seq:
            abort.evaluate(pcap, sstruct, scaler.campaign, dt_hours=1)

    def test_fuzz_extreme_no_deadlock(self):
        """Chuỗi cực đoan (0/1) không gây deadlock."""
        for seq in ChaosGenerator.extreme(num_seqs=5, length=100):
            random.seed(42)
            scaler = MultiTrancheScaler(100, 0.8)
            abort = AbortionProtocol()
            for pcap, sstruct in seq:
                abort.evaluate(pcap, sstruct, scaler.campaign, dt_hours=1)
            assert abort.status.state in (
                AbortionState.ACTIVE, AbortionState.FROZEN,
                AbortionState.COOLDOWN, AbortionState.DOUBLE_SIGNAL,
                AbortionState.EXITING, AbortionState.ABORTED,
            ), f"Deadlock? State={abort.status.state}"

    def test_fuzz_double_bottom_no_resume(self):
        """Double Bottom: KHÔNG BAO GIỜ resume campaign cũ."""
        random.seed(42)
        seq = ChaosGenerator.double_bottom()
        scaler = MultiTrancheScaler(max_pos=1_000_000, s_struct_t0=0.7)
        scaler.campaign.tranches[0].state = "FILLED"
        scaler.campaign.tranches[1].state = "FILLED"
        abort = AbortionProtocol()
        abort.cooldown_hours = 72

        for pcap, sstruct in seq:
            abort.evaluate(pcap, sstruct, scaler.campaign, dt_hours=1)

        # Nếu có double signal → campaign cũ phải có tranches bị REVERTED
        if abort.status.double_signal_count > 0:
            for t in scaler.campaign.tranches:
                assert t.state != "FILLED", (
                    f"Double signal xảy ra nhưng tranche {t.index} vẫn FILLED"
                )

    def test_fuzz_sawtooth_no_deadlock(self):
        """P_cap dao động liên tục quanh ngưỡng freeze — không deadlock."""
        random.seed(42)
        seq = ChaosGenerator.sawtooth()
        scaler = MultiTrancheScaler(100, 0.8)
        abort = AbortionProtocol()
        for pcap, sstruct in seq:
            abort.evaluate(pcap, sstruct, scaler.campaign, dt_hours=1)
        assert abort.status.state in (
            AbortionState.ACTIVE, AbortionState.FROZEN,
            AbortionState.COOLDOWN, AbortionState.DOUBLE_SIGNAL,
            AbortionState.EXITING, AbortionState.ABORTED,
        ), f"Deadlock: {abort.status.state}"
