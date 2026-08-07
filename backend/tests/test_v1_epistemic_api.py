"""test_v1_epistemic_api.py — Test cho REST API /api/v1/epistemic."""

from fastapi.testclient import TestClient


def test_epistemic_composite_api_endpoint(monkeypatch):
    import src.api.main as api_main
    monkeypatch.setattr(api_main, "_is_window_open", lambda: True)

    client = TestClient(api_main.app)
    response = client.get("/api/v1/epistemic/composite?symbols=DGC&symbols=VCB")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "data" in data
    assert len(data["data"]) == 2
    row = data["data"][0]
    assert "symbol" in row
    assert "macro_score" in row
    assert "internal_score" in row
    assert "market_score" in row
    assert "coverage" in row
    assert "coherence" in row
    assert "governor_mandate" in row
    assert "why_drivers" in row


def test_epistemic_data_density_api_endpoint(monkeypatch):
    import src.api.main as api_main
    monkeypatch.setattr(api_main, "_is_window_open", lambda: True)

    client = TestClient(api_main.app)
    response = client.get("/api/v1/epistemic/data-density?symbols=VCB&symbols=IJC")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "data" in data
    assert len(data["data"]) == 2
    row = data["data"][0]
    assert "required_quarters" in row
    assert "available_quarters" in row
    assert "density_pct" in row
