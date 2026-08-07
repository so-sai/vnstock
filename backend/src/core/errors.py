"""
Core Domain Exceptions for PTCK_VNSTOCK.

Hệ thống ngoại lệ phân miền tập trung. Mọi module nghiệp vụ nên raise các
ngoại lệ cụ thể kế thừa từ PTCKError thay vì ném Exception chung chung.

Tuân thủ Error Discipline (Skill: code-py-314):
- Errors handled explicitly hoặc re-raised with context.
- Domain exceptions KHÔNG vượt boundary (chỉ api/routes chuyển → HTTPException).
- Low-level exceptions được wrap semantic context: raise X from e.
"""

from __future__ import annotations


class PTCKError(Exception):
    """Gốc ngoại lệ tối cao của hệ thống PTCK_VNSTOCK."""

    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, object] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload or {}
        if cause:
            self.__cause__ = cause

    def __str__(self) -> str:
        base = self.message
        if self.payload:
            base += f" | payload={self.payload}"
        return base


# ──────────────────────────────────────────────────────────────
# 1. Tầng Dữ liệu & CSDL
# ──────────────────────────────────────────────────────────────


class DataAccessError(PTCKError):
    """
    Lỗi truy vấn CSDL SQLite / read pandas / schema mismatch.
    Wrap: sqlite3.Error, pandas.io.sql.DatabaseError, pd.read_sql failures.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        query: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if table:
            payload["table"] = table
        if query:
            payload["query"] = query[:200]  # truncate
        super().__init__(f"[DataAccess] {message}", payload=payload, cause=cause)


class DataIntegrityError(PTCKError):
    """
    Dữ liệu đọc được nhưng sai cấu trúc / NULL không mong muốn / checksum fail.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        column: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if table:
            payload["table"] = table
        if column:
            payload["column"] = column
        super().__init__(f"[DataIntegrity] {message}", payload=payload, cause=cause)


# ──────────────────────────────────────────────────────────────
# 2. Tầng Nhà cung cấp dữ liệu bên ngoài (External Providers)
# ──────────────────────────────────────────────────────────────


class ProviderError(PTCKError):
    """
    Lỗi kết nối / phản hồi API nguồn ngoài: VCI, CafeF, Vietstock, Yahoo Finance,
    WorldBank, WorldGold, Silver service, etc.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        endpoint: str | None = None,
        status_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if provider:
            payload["provider"] = provider
        if endpoint:
            payload["endpoint"] = endpoint
        if status_code:
            payload["status_code"] = status_code
        super().__init__(f"[Provider] {message}", payload=payload, cause=cause)


class RateLimitError(ProviderError):
    """Rate limit (HTTP 429) từ provider bên ngoài."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retry_after: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, provider=provider, cause=cause)
        if retry_after:
            self.payload["retry_after"] = retry_after


# ──────────────────────────────────────────────────────────────
# 3. Tầng Phân tích & Tính toán (Engines)
# ──────────────────────────────────────────────────────────────


class AnalysisError(PTCKError):
    """
    Lỗi tính toán chỉ số tài chính, Z-score, HMM convergence,
    NaN array, entropy, regime detection, money flow, breadth...
    """

    def __init__(
        self,
        message: str,
        *,
        engine: str | None = None,
        metric: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if engine:
            payload["engine"] = engine
        if metric:
            payload["metric"] = metric
        super().__init__(f"[Analysis] {message}", payload=payload, cause=cause)


class ConvergenceError(AnalysisError):
    """HMM / EM / optimization không hội tụ, LinAlgError, singular matrix."""

    def __init__(
        self,
        message: str,
        *,
        iterations: int | None = None,
        tolerance: float | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, engine="convergence", cause=cause)
        if iterations:
            self.payload["iterations"] = iterations
        if tolerance:
            self.payload["tolerance"] = tolerance


class NaNArrayError(AnalysisError):
    """Array đầu vào chứa NaN/Inf gây vỡ tính toán (Z-score, entropy, log)."""

    def __init__(
        self,
        message: str,
        *,
        array_name: str | None = None,
        nan_count: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, engine="nan_check", cause=cause)
        if array_name:
            self.payload["array_name"] = array_name
        if nan_count:
            self.payload["nan_count"] = nan_count


# ──────────────────────────────────────────────────────────────
# 4. Tầng Ra quyết định (Governor / Decision Guard)
# ──────────────────────────────────────────────────────────────


class GovernorDecisionError(PTCKError):
    """
    Lỗi suy luận Bayesian, BMA weights, Causal Graph propagation,
    Evidence fusion, Circuit breaker, ModelRegistry lookup.
    """

    def __init__(
        self,
        message: str,
        *,
        node: str | None = None,
        evidence_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if node:
            payload["node"] = node
        if evidence_id:
            payload["evidence_id"] = evidence_id
        super().__init__(f"[Governor] {message}", payload=payload, cause=cause)


class CircuitBreakerError(GovernorDecisionError):
    """Circuit breaker tripped — caller phải fallback."""

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        retry_after: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, node="circuit_breaker", cause=cause)
        if source:
            self.payload["source"] = source
        if retry_after:
            self.payload["retry_after"] = retry_after


# ──────────────────────────────────────────────────────────────
# 5. Tầng Kiểm toán & Dòng chảy dữ liệu (Provenance / Epistemic)
# ──────────────────────────────────────────────────────────────


class ProvenanceError(PTCKError):
    """Vi phạm Zero-Hallucination, thiếu provenance, epistemic state invalid."""

    def __init__(
        self,
        message: str,
        *,
        signal_id: str | None = None,
        node_id: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if signal_id:
            payload["signal_id"] = signal_id
        if node_id:
            payload["node_id"] = node_id
        super().__init__(f"[Provenance] {message}", payload=payload, cause=cause)


# ──────────────────────────────────────────────────────────────
# Re-export các class exception cũ để tương thích ngược (backward compat)
# ──────────────────────────────────────────────────────────────

# Lưu ý: Các class dưới đây hiện đã có sẵn trong codebase tại các module riêng.
# Để tránh circular import, chúng ta KHÔNG import chúng ở đây.
# Khi các module cũ được refactor, hãy sửa base class về PTCKError tại chỗ.
# Danh sách tham khảo:
# - ResourceLockedException (database/acid.py)          -> DataAccessError
# - TransactionTimeout (database/acid.py)               -> DataAccessError
# - APIBlockedError (utils/defense.py)                  -> ProviderError
# - RateLimitException (utils/macro_sensors.py)         -> RateLimitError
# - CommanderDataError (engine/strategy_commander.py)   -> AnalysisError
# - GraphCycleError (orchestration/daily_cycle_orchestrator.py) -> GovernorDecisionError

__all__ = [
    "PTCKError",
    "DataAccessError",
    "DataIntegrityError",
    "ProviderError",
    "RateLimitError",
    "AnalysisError",
    "ConvergenceError",
    "NaNArrayError",
    "GovernorDecisionError",
    "CircuitBreakerError",
    "ProvenanceError",
]
