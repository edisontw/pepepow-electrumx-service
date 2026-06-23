from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["app"] == "pepew-light"
    assert data["version"] == "0.1.0"


def test_index_page():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "PEPEW Light" in response.text
