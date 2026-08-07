"""
Test Centralized PTCKError → HTTP mapping (Error Discipline / API boundary).

WHY (2026-08-07 — Phase 4 Architecture):
    governor/engine/core/services raise PTCKError subclasses với semantic
    payload. Chúng propagate qua FastAPI boundary và được map sang HTTP
    status TẬP TRUNG tại `api/main.py::ptck_error_handler`. Đây là hợp đồng
    kiến trúc:
      - Route KHÔNG re-wrap exception; chỉ raise PTCKError và handler lo.
      - HTTP status gắn liền với domain (429 rate-limit, 503 provider/db,
        422 data-integrity/analysis, 409 governor conflict, 400 provenance).
      - Envelope JSON chuẩn XAI: {status, error_type, message, payload}.

    Nếu test này fail → hợp đồng boundary bị phá, cần sửa handler/status-map
    trước khi release, KHÔNG được vá từng route.
"""

import json

import pytest

# Lazy import để tránh nạp toàn bộ app (routes nặng) khi chỉ test handler.
import src.api.main as api_main
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.errors import (
    AnalysisError,
    ConvergenceError,
    DataAccessError,
    DataIntegrityError,
    GovernorDecisionError,
    NaNArrayError,
    ProvenanceError,
    ProviderError,
    PTCKError,
    RateLimitError,
)


# ── WHY: dùng 1 app test riêng (không dùng api_main.app) ──────────────────
#   TestClient đăng ký route SAU khi app đã build sẽ không nhận route mới
#   (routes finalized). Tách handler ra app riêng để verify handler đúng
#   không phụ thuộc thứ tự include_router của app production.
def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(PTCKError, api_main.ptck_error_handler)

    @app.get("/raise")
    async def raise_domain(exc_type: str):
        factories = {
            "data_access": DataAccessError("db down", table="daily_ohlcv"),
            "rate_limit": RateLimitError("too many", provider="yf"),
            "provider": ProviderError("provider fail"),
            "data_integrity": DataIntegrityError("null unexpected"),
            "analysis": AnalysisError("nan array"),
            "convergence": ConvergenceError("hmm no converge"),
            "nan": NaNArrayError("nan in zscore"),
            "governor": GovernorDecisionError("bma fail"),
            "provenance": ProvenanceError("no source"),
            "generic": PTCKError("base error"),
        }
        raise factories[exc_type]

    return app


# ── WHY: map status theo MRO — subclass phải thắng parent class ──────────
#   RateLimitError kế thừa ProviderError: khi cả 2 có entry, subclass
#   (429) phải được chọn trước ProviderError (503). `_status_for_error`
#   walk MRO từ lớp cụ thể lên, đảm bảo precedence đúng.
_STATUS_CASES = [
    (DataAccessError("x"), 503),
    (RateLimitError("x", provider="yf"), 429),
    (ProviderError("x"), 503),
    (DataIntegrityError("x"), 422),
    (AnalysisError("x"), 422),
    (ConvergenceError("x"), 422),
    (NaNArrayError("x"), 422),
    (GovernorDecisionError("x"), 409),
    (ProvenanceError("x"), 400),
    (PTCKError("x"), 500),
]


@pytest.mark.parametrize("exc,expected", _STATUS_CASES)
def test_status_mapping(exc, expected):
    """Status mapping đúng theo MRO precedence cho từng họ domain error."""
    assert api_main._status_for_error(exc) == expected


def test_handler_envelope_xai():
    """Handler trả envelope chuẩn XAI: status/error_type/message/payload."""
    client = TestClient(_make_test_app())
    resp = client.get("/raise?exc_type=data_access")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_type"] == "DataAccessError"
    assert body["message"] == "[DataAccess] db down"
    assert body["payload"] == {"table": "daily_ohlcv"}


def test_handler_generic_ptck_error_500():
    """PTCKError gốc (không có entry) → HTTP 500, envelope vẫn chuẩn."""
    client = TestClient(_make_test_app())
    resp = client.get("/raise?exc_type=generic")
    assert resp.status_code == 500
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_type"] == "PTCKError"
    assert body["message"] == "base error"


def test_handler_rate_limit_429():
    """RateLimitError (subclass ProviderError) phải map 429 không phải 503."""
    client = TestClient(_make_test_app())
    resp = client.get("/raise?exc_type=rate_limit")
    assert resp.status_code == 429
    assert resp.json()["error_type"] == "RateLimitError"


def test_handler_registered_on_production_app():
    """WHY: handler phải được đăng ký trên app production (api.main.app).

    Nếu app production không có handler này, các domain exception sẽ rơi
    vào Starlette 500 mặc định — phá vỡ hợp đồng boundary.
    """
    assert PTCKError in api_main.app.exception_handlers


def test_json_body_serializable():
    """Payload chứa dict phải serialize được (envelope không rò NaN/Inf)."""
    exc = DataAccessError("x", table="t", query="SELECT 1")
    rendered = json.dumps(
        {"error_type": type(exc).__name__, "message": exc.message, "payload": exc.payload},
        ensure_ascii=False,
    )
    assert isinstance(rendered, str)
    assert "DataAccessError" in rendered
