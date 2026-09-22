from fastapi.testclient import TestClient

from app.api import webhook_v1
from app.main import app


def test_create_webhook_endpoint_returns_signing_secret_once(monkeypatch):
    monkeypatch.setattr(webhook_v1, "require_payment_create_auth", lambda _value: None)

    async def fake_create(**kwargs):
        return {
            "ok": True,
            "endpoint_id": "wh_example",
            "url": kwargs["url"],
            "event_types": kwargs["event_types"],
            "enabled": True,
            "created_at": 1000,
            "updated_at": 1000,
            "signing_secret": "secret-on-create",
        }

    monkeypatch.setattr(webhook_v1, "create_webhook_endpoint", fake_create)
    client = TestClient(app)
    response = client.post(
        "/api/v1/webhook-endpoints",
        json={
            "url": "https://merchant.example/hook",
            "event_types": ["payment.paid_confirmed"],
        },
    )

    assert response.status_code == 201
    assert response.json()["signing_secret"] == "secret-on-create"


def test_list_webhook_endpoints_does_not_return_secret(monkeypatch):
    monkeypatch.setattr(webhook_v1, "require_payment_create_auth", lambda _value: None)

    async def fake_list():
        return [{
            "endpoint_id": "wh_example",
            "url": "https://merchant.example/hook",
            "event_types": None,
            "enabled": True,
            "created_at": 1000,
            "updated_at": 1000,
        }]

    monkeypatch.setattr(webhook_v1, "list_webhook_endpoints", fake_list)
    client = TestClient(app)
    response = client.get("/api/v1/webhook-endpoints")

    assert response.status_code == 200
    endpoint = response.json()["endpoints"][0]
    assert "signing_secret" not in endpoint


def test_webhook_endpoint_api_requires_merchant_auth(monkeypatch):
    def reject(_value):
        raise webhook_v1.PaymentAuthError("missing")

    monkeypatch.setattr(webhook_v1, "require_payment_create_auth", reject)
    client = TestClient(app)
    response = client.get("/api/v1/webhook-endpoints")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "payment_auth_required"
    assert response.headers["www-authenticate"] == "Bearer"


def test_disable_webhook_endpoint(monkeypatch):
    monkeypatch.setattr(webhook_v1, "require_payment_create_auth", lambda _value: None)

    async def fake_disable(endpoint_id):
        assert endpoint_id == "wh_example"

    monkeypatch.setattr(webhook_v1, "disable_webhook_endpoint", fake_disable)
    client = TestClient(app)
    response = client.delete("/api/v1/webhook-endpoints/wh_example")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "endpoint_id": "wh_example",
        "enabled": False,
    }
