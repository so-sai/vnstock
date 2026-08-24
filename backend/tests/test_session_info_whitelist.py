"""test_session_info_whitelist.py — Regression cho circuit breaker whitelist.

Root cause đã phát hiện qua Playwright visual audit (2026-08-24):
Frontend BackendGate block render đến khi /api/system/session-info trả ok,
nhưng circuit_breaker middleware để path này dưới van ngắt (503 OFFLINE)
và rate-limit (429 sau 15 req/phút) -> user thấy white-screen vĩnh viễn
dù backend đang sống. Fix: đưa session-info vào whitelist (health checks
must never be rate-limited).

Các test này khóa invariant đó:
  1. Van đóng (offline-by-default) -> session-info vẫn 200;
  2. Van mở -> session-info 200;
  3. Endpoint dữ liệu KHÔNG whitelist vẫn bị chặn 503 khi van đóng
     (chống ai đó nới lỏng whitelist quá tay).
"""

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import src.api.main as api_main

    return TestClient(api_main.app)


def test_session_info_bypasses_offline_circuit_breaker(monkeypatch):
    """Van ĐÓNG (offline-by-default) — health endpoint vẫn phải 200."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "_is_window_open", lambda: False)
    response = _client().get("/api/system/session-info")
    assert response.status_code == 200, (
        "session-info bị chặn khi van đóng -> Frontend white-screen"
    )
    body = response.json()
    assert "target_date" in body


def test_session_info_ok_when_window_open(monkeypatch):
    """Van mở — session-info hoạt động bình thường."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "_is_window_open", lambda: True)
    response = _client().get("/api/system/session-info")
    assert response.status_code == 200


def test_data_endpoints_still_blocked_when_offline(monkeypatch):
    """Guard chống nới lỏng whitelist quá tay: endpoint dữ liệu (breadth)
    KHÔNG nằm trong whitelist — van đóng vẫn phải 503 OFFLINE."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "_is_window_open", lambda: False)
    response = _client().get("/api/breadth/")
    assert response.status_code == 503
    assert response.json()["error"] == "OFFLINE"


def test_rate_limit_still_applies_to_data_endpoints(monkeypatch):
    """Rate-limit 15 req/phút vẫn bảo vệ endpoint dữ liệu khi van mở
    (health không được rate-limit nhưng data thì có)."""
    import src.api.main as api_main

    monkeypatch.setattr(api_main, "_is_window_open", lambda: True)
    api_main._request_log.clear()
    client = _client()
    codes = [client.get("/api/breadth/").status_code for _ in range(17)]
    # Sau 15 request trong cửa sổ 60s, các request kế tiếp phải 429
    assert 429 in codes, f"rate limit không kích hoạt: {codes}"
