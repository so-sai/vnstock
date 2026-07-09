"""
bp_imm.py — Module 2: Bimodal-Preserving Interacting Multiple Model.

Architecture: PTD Layer 2 (Driver Inference Engine)
  Input:  feature vector from TimeSeriesAligner (n_obs)
  Output: Gaussian mixture [(weight, mean, cov), ...] for Conditioning Gate

Specification (frozen 2026-07-09):
  M=4 regimes, KL divergence threshold θ=2.5
  max_KL ≤ θ  → collapse to single Gaussian (computational efficiency)
  max_KL > θ  → preserve multi-component mixture (heavy tail protection)
  Adaptive pruning: entropy < 0.5 → collapse, entropy > 2.0 → expand
"""

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


# ── Default regime dynamics ───────────────────────────────────────────
# 4 macro regimes: Normal, Stress, Liquidity_Scarcity, Recovery
# Each regime has different process noise (variance of driver evolution)

REGIME_LABELS = ["NORMAL", "STRESS", "LIQUIDITY_SCARCITY", "RECOVERY"]

# Prior process noise: Normal < Recovery < Stress < Liquidity_Scarcity
REGIME_Q_FACTOR = [0.01, 0.05, 0.20, 0.03]

# Prior observation noise (shared across regimes)
DEFAULT_OBS_NOISE = 0.10

# Default transition matrix (diagonally dominant)
DEFAULT_TRANSITION = np.array([
    [0.85, 0.10, 0.00, 0.05],
    [0.10, 0.75, 0.10, 0.05],
    [0.00, 0.05, 0.90, 0.05],
    [0.10, 0.10, 0.05, 0.75],
])


@dataclass
class GaussianComponent:
    """Single Gaussian component in the mixture."""
    weight: float
    mean: np.ndarray
    cov: np.ndarray


@dataclass
class BP_IMM_Output:
    """Complete output from BP-IMM update step."""
    mixture: list[GaussianComponent]
    entropy: float
    max_kl: float
    n_active: int
    regime_weights: np.ndarray
    dominant_regime: str


class BPIMM:
    """
    Bimodal-Preserving Interacting Multiple Model.

    Parameters
    ----------
    n_drivers : int
        Dimension of latent driver space (default 7).
    n_regimes : int
        Number of dynamics regimes (default 4).
    kl_threshold : float
        KL divergence threshold for collapse veto (default 2.5).
    """

    def __init__(self, n_drivers: int = 7, n_regimes: int = 4, kl_threshold: float = 2.5):
        if n_regimes > len(REGIME_LABELS):
            raise ValueError(
                f"n_regimes ({n_regimes}) exceeds max {len(REGIME_LABELS)}. "
                f"Add labels to REGIME_LABELS to extend."
            )

        self.n = n_drivers
        self.M = n_regimes
        self.theta = kl_threshold
        self.labels = REGIME_LABELS[:n_regimes]

        # ── Transition matrix ──
        self.T = DEFAULT_TRANSITION[:n_regimes, :n_regimes].copy()
        for i in range(n_regimes):
            self.T[i] /= self.T[i].sum()

        # ── Dynamics: x_{t+1} = F_i @ x_t + N(0, Q_i) ──
        self.F = [np.eye(n_drivers) for _ in range(n_regimes)]
        self.Q = [qf * np.eye(n_drivers) for qf in REGIME_Q_FACTOR[:n_regimes]]

        # ── Observation: z_t = H @ x_t + N(0, R) ──
        self.H = np.eye(n_drivers)
        self.R = DEFAULT_OBS_NOISE * np.eye(n_drivers)

        # ── Initial states: equal weights, zero mean, identity cov ──
        w0 = 1.0 / n_regimes
        self.states = [
            GaussianComponent(weight=w0, mean=np.zeros(n_drivers),
                              cov=np.eye(n_drivers))
            for _ in range(n_regimes)
        ]

        # ── Tracking ──
        self.entropy_history: list[float] = []
        self.convergence_count = 0  # steps with entropy < 0.5

    # ── Public API ────────────────────────────────────────────────────

    def update(self, observation: np.ndarray) -> BP_IMM_Output:
        """
        Single step: predict → update → KL gate → output.

        Internal: always maintains M=4 filters for next iteration.
        Output: collapsed (1) or full mixture (M) based on KL gate.

        Parameters
        ----------
        observation : np.ndarray (n_drivers,)
            Feature vector aligned by TimeSeriesAligner.

        Returns
        -------
        BP_IMM_Output with mixture, entropy, max_kl, regime weights.
        """
        obs = np.asarray(observation, dtype=float).ravel()
        assert obs.shape[0] == self.n, (
            f"Observation dim {obs.shape[0]} != n_drivers {self.n}"
        )

        # 1. Mix initial conditions (IMM standard)
        mixed = self._mix_initial_conditions()

        # 2. Predict each regime
        predicted = self._predict(mixed)
        predicted = self._normalize_weights(predicted)

        # 3. Update each regime with observation
        updated = self._update_step(predicted, obs)
        updated = self._normalize_weights(updated)

        # Always store M=4 states internally for next iteration
        self.states = updated

        # 4. KL gate: check divergence between updated M states
        max_kl = self._compute_max_kl(updated)

        # 5. Build output mixture
        if max_kl <= self.theta:
            output = [self._collapse_mixture(updated)]
        else:
            output = updated

        # 6. Entropy
        weights = np.array([s.weight for s in output])
        eps = 1e-10
        entropy = float(-np.sum(weights * np.log(weights + eps)))
        self.entropy_history.append(entropy)

        # 7. Adaptive pruning (expand if needed)
        output = self._adaptive_prune(output, entropy, len(output))

        out_weights = np.array([s.weight for s in output])
        dominant = int(np.argmax(out_weights))

        return BP_IMM_Output(
            mixture=output,
            entropy=round(entropy, 4),
            max_kl=round(max_kl, 4),
            n_active=len(output),
            regime_weights=out_weights,
            dominant_regime=self.labels[dominant] if dominant < len(self.labels) else "UNKNOWN",
        )

    def reset(self) -> None:
        """Reset filter to initial state."""
        w0 = 1.0 / self.M
        self.states = [
            GaussianComponent(weight=w0, mean=np.zeros(self.n),
                              cov=np.eye(self.n))
            for _ in range(self.M)
        ]
        self.entropy_history.clear()
        self.convergence_count = 0

    def get_mixture_moments(self) -> dict:
        """Compute overall mean and cov from the current mixture."""
        weights = np.array([s.weight for s in self.states])
        weights /= weights.sum()
        overall_mean = sum(w * s.mean for w, s in zip(weights, self.states))
        overall_cov = sum(
            w * (s.cov + np.outer(s.mean - overall_mean, s.mean - overall_mean))
            for w, s in zip(weights, self.states)
        )
        return {"mean": overall_mean, "cov": overall_cov, "n_components": len(self.states)}

    # ── Private: Mixing Initial Conditions (IMM Standard) ────────────

    def _mix_initial_conditions(self) -> list[GaussianComponent]:
        """
        IMM mixing step: compute mixed initial condition for each filter.

        For each regime j:
            x_j^0 = Σ_i μ_{i|j} · x_i
            μ_{i|j} = T_{i,j} · μ_i / Σ_k T_{k,j} · μ_k

        This creates M filters from M prior states.
        """
        mixed = []
        for j in range(self.M):
            norm = sum(self.T[i, j] * self.states[i].weight for i in range(self.M))
            if norm < 1e-10:
                mixed.append(GaussianComponent(
                    weight=norm,
                    mean=np.zeros(self.n),
                    cov=np.eye(self.n),
                ))
                continue

            mean_mix = sum(
                self.T[i, j] * self.states[i].weight * self.states[i].mean
                for i in range(self.M)
            ) / norm

            cov_mix = np.zeros((self.n, self.n))
            for i in range(self.M):
                w = self.T[i, j] * self.states[i].weight / norm
                diff = self.states[i].mean - mean_mix
                cov_mix += w * (self.states[i].cov + np.outer(diff, diff))

            mixed.append(GaussianComponent(
                weight=norm,
                mean=mean_mix,
                cov=(cov_mix + cov_mix.T) / 2 + 1e-6 * np.eye(self.n),
            ))

        return mixed

    # ── Private: Prediction ───────────────────────────────────────────

    def _predict(self, mixed: list[GaussianComponent]) -> list[GaussianComponent]:
        """Predict next state for each regime from mixed initial conditions."""
        predictions = []
        for i in range(self.M):
            mean_pred = self.F[i] @ mixed[i].mean
            cov_pred = self.F[i] @ mixed[i].cov @ self.F[i].T + self.Q[i]
            # Mode probability prediction: μ_j(t|t-1) = Σ_i T_{i,j} · μ_i(t-1)
            mu_pred = mixed[i].weight
            predictions.append(GaussianComponent(
                weight=max(mu_pred, 1e-10),
                mean=mean_pred,
                cov=(cov_pred + cov_pred.T) / 2,
            ))
        return predictions

    # ── Private: KL Divergence ────────────────────────────────────────

    def _kl_divergence(self, c1: GaussianComponent, c2: GaussianComponent) -> float:
        """KL(P₁ || P₂) for two Gaussians."""
        k = len(c1.mean)
        s2_inv = np.linalg.inv(c2.cov)
        delta = c2.mean - c1.mean
        term1 = float(np.trace(s2_inv @ c1.cov))
        term2 = float(delta.T @ s2_inv @ delta)
        term3 = k
        try:
            term4 = float(np.log(np.linalg.det(c2.cov) / max(np.linalg.det(c1.cov), 1e-20)))
        except np.linalg.LinAlgError:
            term4 = 0.0
        return 0.5 * max(0.0, term1 + term2 - term3 + term4)

    def _compute_max_kl(self, states: list[GaussianComponent]) -> float:
        """Maximum pairwise KL divergence across all regimes."""
        if len(states) <= 1:
            return 0.0
        max_kl = 0.0
        for i in range(len(states)):
            for j in range(i + 1, len(states)):
                kl = self._kl_divergence(states[i], states[j])
                max_kl = max(max_kl, kl)
        return max_kl

    @staticmethod
    def _collapse_mixture(states: list[GaussianComponent]) -> GaussianComponent:
        """Collapse mixture to single Gaussian (moment-preserving)."""
        w_sum = sum(s.weight for s in states)
        if w_sum < 1e-10:
            return GaussianComponent(weight=1.0, mean=np.zeros_like(states[0].mean),
                                     cov=np.eye(len(states[0].mean)))

        mixed_mean = sum(s.weight * s.mean for s in states) / w_sum
        mixed_cov = sum(
            s.weight * (s.cov + np.outer(s.mean - mixed_mean, s.mean - mixed_mean))
            for s in states
        ) / w_sum
        mixed_cov = (mixed_cov + mixed_cov.T) / 2 + 1e-6 * np.eye(len(mixed_mean))
        return GaussianComponent(weight=1.0, mean=mixed_mean, cov=mixed_cov)

    # ── Private: Update ───────────────────────────────────────────────

    def _update_step(self, states: list[GaussianComponent],
                     obs: np.ndarray) -> list[GaussianComponent]:
        """Kalman update for each component."""
        updated = []
        for s in states:
            innovation = obs - self.H @ s.mean
            innov_cov = self.H @ s.cov @ self.H.T + self.R
            innov_cov = (innov_cov + innov_cov.T) / 2 + 1e-6 * np.eye(self.n)

            # Kalman gain
            try:
                K = s.cov @ self.H.T @ np.linalg.inv(innov_cov)
            except np.linalg.LinAlgError:
                K = s.cov @ self.H.T @ np.linalg.pinv(innov_cov)

            mean_upd = s.mean + K @ innovation
            cov_upd = (np.eye(self.n) - K @ self.H) @ s.cov
            cov_upd = (cov_upd + cov_upd.T) / 2 + 1e-6 * np.eye(self.n)

            likelihood = self._gaussian_loglik(obs, self.H @ s.mean, innov_cov)
            weight_upd = max(s.weight * np.exp(likelihood), 1e-15)

            updated.append(GaussianComponent(weight=weight_upd, mean=mean_upd, cov=cov_upd))

        return updated

    @staticmethod
    def _gaussian_loglik(z: np.ndarray, mean: np.ndarray,
                         cov: np.ndarray) -> float:
        """Log-likelihood under multivariate Gaussian."""
        k = len(z)
        delta = z - mean
        try:
            L = np.linalg.cholesky(cov)
            alpha = np.linalg.solve(L, delta)
            quad = float(alpha @ alpha)
            log_det = 2.0 * float(np.sum(np.log(np.diag(L))))
            return -0.5 * (k * np.log(2 * np.pi) + log_det + quad)
        except np.linalg.LinAlgError:
            diag_cov = np.diag(cov) + 1e-10
            quad = float(np.sum(delta ** 2 / diag_cov))
            log_det = float(np.sum(np.log(diag_cov)))
            return -0.5 * (k * np.log(2 * np.pi) + log_det + quad)

    # ── Private: Adaptive Pruning ─────────────────────────────────────

    def _adaptive_prune(self, states: list[GaussianComponent],
                        entropy: float, n_before_mix: int) -> list[GaussianComponent]:
        """
        Entropy-based pruning:
          entropy < 0.5 → collapse to single component
          entropy > 2.0 → expand back to M regimes (reactivate dormant)
        0.5 ≤ entropy ≤ 2.0 → keep current structure
        """
        n_current = len(states)

        if entropy < 0.5 and n_current > 1:
            self.convergence_count += 1
            return [self._collapse_mixture(states)]

        if entropy > 2.0 and n_current < self.M:
            self.convergence_count = 0
            return self._expand_mixture(states)

        return states

    def _expand_mixture(self, states: list[GaussianComponent]) -> list[GaussianComponent]:
        """
        Expand collapsed mixture back to M regimes.
        Spreads means using current cov as uncertainty guide.
        """
        if len(states) == 1:
            current = states[0]
            expanded = [GaussianComponent(
                weight=current.weight * 0.7,
                mean=current.mean.copy(),
                cov=current.cov.copy(),
            )]
            diag = np.sqrt(np.maximum(np.diag(current.cov), 1e-3))
            for i in range(1, self.M):
                offset = np.random.randn(self.n) * diag * 2.0
                expanded.append(GaussianComponent(
                    weight=current.weight * 0.3 / (self.M - 1),
                    mean=current.mean + offset,
                    cov=current.cov * 3.0,
                ))
            return expanded

        # If M > current, pad with zero-weight components from prior
        expanded = list(states)
        remaining = self.M - len(expanded)
        w_remain = 0.1 / remaining
        for _ in range(remaining):
            expanded.append(GaussianComponent(
                weight=w_remain,
                mean=np.zeros(self.n),
                cov=3.0 * np.eye(self.n),
            ))
        return expanded

    def _normalize_weights(self, states: list[GaussianComponent]) -> list[GaussianComponent]:
        """Ensure weights sum to 1."""
        w_sum = max(sum(s.weight for s in states), 1e-15)
        return [GaussianComponent(weight=s.weight / w_sum, mean=s.mean, cov=s.cov)
                for s in states]
