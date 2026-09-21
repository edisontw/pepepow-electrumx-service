import asyncio

from app.config import get_settings
from app.services import payment_gateway_service
from app.services.payment_state import PaymentTransactionObservation


ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


def test_create_then_get_payment_uses_sqlite_without_get_polling(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_default_expiry_seconds": 900,
            "payment_max_expiry_seconds": 86400,
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    calls = {"status": 0}

    async def fake_status():
        calls["status"] += 1
        return {
            "ok": True,
            "electrumx": {
                "height": 500,
                "tip_hash": "tip",
            },
        }

    monkeypatch.setattr(payment_gateway_service, "get_status", fake_status)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1.25",
            confirmations=3,
            expires_in=900,
            label="Demo",
            message="Order 1",
        )
    )

    assert created["status"] == "waiting"
    assert created["amount_sats"] == 125000000
    assert created["created_height"] == 500
    assert calls["status"] == 1

    async def forbidden_status():
        raise AssertionError("GET payment status must not poll ElectrumX.")

    monkeypatch.setattr(payment_gateway_service, "get_status", forbidden_status)
    loaded = asyncio.run(payment_gateway_service.get_persisted_payment(created["payment_id"]))

    assert loaded["payment_id"] == created["payment_id"]
    assert loaded["status"] == "waiting"
    assert loaded["amount_sats"] == 125000000
    assert calls["status"] == 1


def test_persisted_gateway_reflects_transaction_observation(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_default_expiry_seconds": 900,
            "payment_max_expiry_seconds": 86400,
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    async def fake_status():
        return {
            "ok": True,
            "electrumx": {
                "height": 500,
                "tip_hash": "tip",
            },
        }

    monkeypatch.setattr(payment_gateway_service, "get_status", fake_status)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="0.000001",
            confirmations=3,
            expires_in=900,
        )
    )

    store = payment_gateway_service._store_for_path(settings.payment_db_path)
    payment = store.get_payment(created["payment_id"])
    seen_at = int(payment["created_at"]) + 1
    store.upsert_transaction(
        created["payment_id"],
        PaymentTransactionObservation(
            txid="a" * 64,
            vout=0,
            value_sats=100,
            height=501,
            first_seen_at=seen_at,
        ),
        updated_at=seen_at,
    )
    store.set_chain_tip(503, tip_hash="tip3", updated_at=seen_at + 1)

    loaded = asyncio.run(payment_gateway_service.get_persisted_payment(created["payment_id"]))

    assert loaded["status"] == "paid_confirmed"
    assert loaded["received_sats"] == 100
    assert loaded["policy_confirmed_sats"] == 100
    assert loaded["version"] == 2


def test_payment_api_is_disabled_by_default(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": False,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="1",
            )
        )
    except payment_gateway_service.PaymentGatewayDisabledError:
        pass
    else:
        raise AssertionError("Expected disabled Payment API to reject creation.")
