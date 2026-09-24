from fastapi.testclient import TestClient

from app.api import payment_v1
from app.main import app

ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


def test_create_payment_v1_returns_201(monkeypatch):
    async def fake_create(**kwargs):
        assert kwargs["idempotency_key"] == "order-1-attempt-1"
        assert kwargs["merchant_reference"] == "ORDER-1"
        return {
            "ok": True,
            "payment_id": "pay_example",
            "address": kwargs["address"],
            "amount": kwargs["amount"],
            "amount_sats": 100000000,
            "confirmations_required": 3,
            "created_at": 1000,
            "created_height": 500,
            "expires_at": 1900,
            "status": "waiting",
            "version": 1,
            "received_sats": 0,
            "confirmed_sats": 0,
            "policy_confirmed_sats": 0,
            "overpaid_by_sats": 0,
            "label": kwargs["label"],
            "message": kwargs["message"],
            "updated_at": 1000,
        }

    monkeypatch.setattr(payment_v1, "create_persisted_payment", fake_create)
    monkeypatch.setattr(payment_v1, "require_payment_create_auth", lambda _authorization: None)
    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": "order-1-attempt-1"},
        json={
            "address": ADDRESS,
            "amount": "1",
            "label": "Demo",
            "message": "Order 1",
            "merchant_reference": "ORDER-1",
        },
    )

    assert response.status_code == 201
    assert response.json()["payment_id"] == "pay_example"
    assert response.json()["status"] == "waiting"


def test_get_payment_v1_reads_persisted_service(monkeypatch):
    async def fake_get(payment_id):
        return {
            "ok": True,
            "payment_id": payment_id,
            "address": ADDRESS,
            "amount": "1",
            "amount_sats": 100000000,
            "confirmations_required": 3,
            "created_at": 1000,
            "created_height": 500,
            "expires_at": 1900,
            "status": "paid_confirmed",
            "version": 2,
            "received_sats": 100000000,
            "confirmed_sats": 100000000,
            "policy_confirmed_sats": 100000000,
            "overpaid_by_sats": 0,
            "label": None,
            "message": None,
            "updated_at": 1100,
        }

    monkeypatch.setattr(payment_v1, "get_persisted_payment", fake_get)
    client = TestClient(app)
    response = client.get("/api/v1/payments/pay_example")

    assert response.status_code == 200
    assert response.json()["status"] == "paid_confirmed"


def test_get_payment_v1_not_found(monkeypatch):
    async def fake_get(_payment_id):
        raise payment_v1.PaymentNotFoundError("missing")

    monkeypatch.setattr(payment_v1, "get_persisted_payment", fake_get)
    client = TestClient(app)
    response = client.get("/api/v1/payments/pay_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "payment_not_found"


def test_create_payment_v1_requires_bearer_auth(monkeypatch):
    def reject(_authorization):
        raise payment_v1.PaymentAuthError("missing")

    monkeypatch.setattr(payment_v1, "require_payment_create_auth", reject)
    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        json={"address": ADDRESS, "amount": "1"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "payment_auth_required"
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_payment_v1_fails_closed_when_auth_unconfigured(monkeypatch):
    def reject(_authorization):
        raise payment_v1.PaymentAuthUnconfiguredError("unconfigured")

    monkeypatch.setattr(payment_v1, "require_payment_create_auth", reject)
    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        headers={"Authorization": "Bearer test"},
        json={"address": ADDRESS, "amount": "1"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "payment_auth_unconfigured"


def test_create_payment_v1_returns_conflict_for_reused_idempotency_key(monkeypatch):
    async def fake_create(**_kwargs):
        raise payment_v1.PaymentIdempotencyConflictError("order-1-attempt-1")

    monkeypatch.setattr(payment_v1, "create_persisted_payment", fake_create)
    monkeypatch.setattr(payment_v1, "require_payment_create_auth", lambda _authorization: None)

    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": "order-1-attempt-1"},
        json={"address": ADDRESS, "amount": "2"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payment_idempotency_conflict"


def test_create_payment_v1_rejects_invalid_idempotency_key(monkeypatch):
    async def fake_create(**_kwargs):
        raise payment_v1.InvalidPaymentParameterError(
            "invalid_idempotency_key",
            "invalid",
        )

    monkeypatch.setattr(payment_v1, "create_persisted_payment", fake_create)
    monkeypatch.setattr(payment_v1, "require_payment_create_auth", lambda _authorization: None)

    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        headers={"Idempotency-Key": "order 1"},
        json={"address": ADDRESS, "amount": "1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


def test_list_payments_v1_requires_merchant_auth(monkeypatch):
    def reject(_authorization):
        raise payment_v1.PaymentAuthError("missing")

    monkeypatch.setattr(payment_v1, "require_payment_merchant_auth", reject)
    client = TestClient(app)
    response = client.get("/api/v1/payments")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "payment_auth_required"
    assert response.headers["www-authenticate"] == "Bearer"


def test_list_payments_v1_passes_bounded_filters_and_cursor(monkeypatch):
    def accept(authorization):
        assert authorization == "Bearer merchant-test"

    async def fake_list(**kwargs):
        assert kwargs == {
            "status": "paid_confirmed",
            "merchant_reference": "ORDER-1",
            "limit": 25,
            "before_created_at": 1234,
            "before_payment_id": "pay_cursor",
        }
        return {
            "ok": True,
            "payments": [
                {
                    "ok": True,
                    "payment_id": "pay_example",
                    "address": ADDRESS,
                    "amount": "1",
                    "amount_sats": 100000000,
                    "confirmations_required": 3,
                    "created_at": 1200,
                    "created_height": 500,
                    "expires_at": 2100,
                    "status": "paid_confirmed",
                    "version": 2,
                    "received": "1",
                    "received_sats": 100000000,
                    "confirmed": "1",
                    "confirmed_sats": 100000000,
                    "policy_confirmed": "1",
                    "policy_confirmed_sats": 100000000,
                    "overpaid_by": "0",
                    "overpaid_by_sats": 0,
                    "label": None,
                    "message": None,
                    "updated_at": 1300,
                    "idempotency_key": "order-1",
                    "merchant_reference": "ORDER-1",
                }
            ],
            "has_more": False,
            "next_before_created_at": None,
            "next_before_payment_id": None,
        }

    monkeypatch.setattr(payment_v1, "require_payment_merchant_auth", accept)
    monkeypatch.setattr(payment_v1, "list_persisted_payments", fake_list)

    client = TestClient(app)
    response = client.get(
        "/api/v1/payments",
        headers={"Authorization": "Bearer merchant-test"},
        params={
            "status": "paid_confirmed",
            "merchant_reference": "ORDER-1",
            "limit": 25,
            "before_created_at": 1234,
            "before_payment_id": "pay_cursor",
        },
    )

    assert response.status_code == 200
    assert response.json()["payments"][0]["payment_id"] == "pay_example"
    assert response.json()["payments"][0]["idempotency_key"] == "order-1"


def test_public_payment_status_does_not_require_merchant_list_auth(monkeypatch):
    def forbidden(_authorization):
        raise AssertionError("Public capability status must not require merchant Bearer auth.")

    async def fake_get(payment_id):
        return {
            "ok": True,
            "payment_id": payment_id,
            "address": ADDRESS,
            "amount": "1",
            "amount_sats": 100000000,
            "confirmations_required": 3,
            "created_at": 1000,
            "created_height": 500,
            "expires_at": 1900,
            "status": "waiting",
            "version": 1,
            "received": "0",
            "received_sats": 0,
            "confirmed": "0",
            "confirmed_sats": 0,
            "policy_confirmed": "0",
            "policy_confirmed_sats": 0,
            "overpaid_by": "0",
            "overpaid_by_sats": 0,
            "label": None,
            "message": None,
            "updated_at": 1000,
        }

    monkeypatch.setattr(payment_v1, "require_payment_merchant_auth", forbidden)
    monkeypatch.setattr(payment_v1, "get_persisted_payment", fake_get)

    client = TestClient(app)
    response = client.get("/api/v1/payments/pay_example")

    assert response.status_code == 200
    assert response.json()["payment_id"] == "pay_example"


def test_create_payment_v1_returns_merchant_reference_conflict(monkeypatch):
    async def fake_create(**_kwargs):
        raise payment_v1.PaymentMerchantReferenceConflictError("ORDER-1")

    monkeypatch.setattr(payment_v1, "create_persisted_payment", fake_create)
    monkeypatch.setattr(payment_v1, "require_payment_create_auth", lambda _authorization: None)

    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        json={
            "address": ADDRESS,
            "amount": "1",
            "merchant_reference": "ORDER-1",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "payment_merchant_reference_conflict"


def test_create_payment_v1_rejects_invalid_merchant_reference(monkeypatch):
    async def fake_create(**_kwargs):
        raise payment_v1.InvalidPaymentParameterError(
            "invalid_merchant_reference",
            "invalid",
        )

    monkeypatch.setattr(payment_v1, "create_persisted_payment", fake_create)
    monkeypatch.setattr(payment_v1, "require_payment_create_auth", lambda _authorization: None)

    client = TestClient(app)
    response = client.post(
        "/api/v1/payments",
        json={
            "address": ADDRESS,
            "amount": "1",
            "merchant_reference": "ORDER 1",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_merchant_reference"
