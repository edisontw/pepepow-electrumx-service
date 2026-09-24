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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

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
    assert calls["snapshot"] == 1

    async def forbidden_snapshot(_settings, _scripthash):
        raise AssertionError("GET payment status must not poll ElectrumX.")

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", forbidden_snapshot)
    loaded = asyncio.run(payment_gateway_service.get_persisted_payment(created["payment_id"]))

    assert loaded["payment_id"] == created["payment_id"]
    assert loaded["status"] == "waiting"
    assert loaded["amount_sats"] == 125000000
    assert calls["snapshot"] == 1


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

    async def fake_snapshot(_settings, _scripthash):
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

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
    assert loaded["received"] == "0.000001"
    assert loaded["received_sats"] == 100
    assert loaded["confirmed"] == "0.000001"
    assert loaded["policy_confirmed"] == "0.000001"
    assert loaded["policy_confirmed_sats"] == 100
    assert loaded["overpaid_by"] == "0"
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


def test_creation_snapshot_persists_preexisting_mempool_baseline(tmp_path, monkeypatch):
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

    old_mempool_txid = "c" * 64

    async def fake_snapshot(_settings, _scripthash):
        return 500, "tip", (old_mempool_txid,)

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            confirmations=3,
            expires_in=900,
        )
    )

    store = payment_gateway_service._store_for_path(settings.payment_db_path)
    assert store.get_baseline_txids(created["payment_id"]) == {old_mempool_txid}


def test_idempotent_create_reuses_payment_without_second_electrumx_snapshot(tmp_path, monkeypatch):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    first = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1.25",
            confirmations=3,
            expires_in=900,
            label="Demo",
            message="Order 123",
            idempotency_key="order-123-attempt-1",
        )
    )
    second = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1.25",
            confirmations=3,
            expires_in=900,
            label="Demo",
            message="Order 123",
            idempotency_key="order-123-attempt-1",
        )
    )

    assert second["payment_id"] == first["payment_id"]
    assert calls["snapshot"] == 1

    store = payment_gateway_service._store_for_path(settings.payment_db_path)
    assert [event["event_type"] for event in store.list_events()] == ["payment.created"]


def test_idempotency_conflict_is_detected_before_second_snapshot(tmp_path, monkeypatch):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            idempotency_key="order-123-attempt-1",
        )
    )

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="2",
                idempotency_key="order-123-attempt-1",
            )
        )
    except payment_gateway_service.PaymentIdempotencyConflictError:
        pass
    else:
        raise AssertionError("Expected reused Idempotency-Key with changed request to conflict.")

    assert calls["snapshot"] == 1


def test_idempotency_key_validation_rejects_unsafe_characters(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    async def forbidden_snapshot(_settings, _scripthash):
        raise AssertionError("Invalid idempotency key must fail before ElectrumX.")

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", forbidden_snapshot)

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="1",
                idempotency_key="order 123",
            )
        )
    except payment_gateway_service.InvalidPaymentParameterError as exc:
        assert exc.code == "invalid_idempotency_key"
    else:
        raise AssertionError("Expected invalid idempotency key to be rejected.")


def test_list_persisted_payments_is_sqlite_only_and_returns_recovery_metadata(tmp_path, monkeypatch):
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

    async def fake_snapshot(_settings, _scripthash):
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1.25",
            confirmations=3,
            expires_in=900,
            label="Demo",
            message="Order 123",
            idempotency_key="order-123-attempt-1",
        )
    )

    async def forbidden_snapshot(_settings, _scripthash):
        raise AssertionError("Merchant listing must not poll ElectrumX.")

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", forbidden_snapshot)

    result = asyncio.run(
        payment_gateway_service.list_persisted_payments(limit=50)
    )

    assert result["ok"] is True
    assert result["has_more"] is False
    assert result["next_before_created_at"] is None
    assert result["next_before_payment_id"] is None
    assert len(result["payments"]) == 1
    item = result["payments"][0]
    assert item["payment_id"] == created["payment_id"]
    assert item["amount"] == "1.25"
    assert item["idempotency_key"] == "order-123-attempt-1"


def test_list_persisted_payments_validates_status_and_cursor_before_store_use(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    try:
        asyncio.run(
            payment_gateway_service.list_persisted_payments(status="unknown")
        )
    except payment_gateway_service.InvalidPaymentParameterError as exc:
        assert exc.code == "invalid_payment_status"
    else:
        raise AssertionError("Expected unsupported payment status to be rejected.")

    try:
        asyncio.run(
            payment_gateway_service.list_persisted_payments(
                before_created_at=1000,
                before_payment_id=None,
            )
        )
    except payment_gateway_service.InvalidPaymentParameterError as exc:
        assert exc.code == "invalid_payment_cursor"
    else:
        raise AssertionError("Expected incomplete payment cursor to be rejected.")


def test_create_payment_with_merchant_reference_returns_merchant_metadata(tmp_path, monkeypatch):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1.25",
            merchant_reference="ORDER-1001",
            idempotency_key="retry-order-1001",
        )
    )

    assert created["merchant_reference"] == "ORDER-1001"
    assert created["idempotency_key"] == "retry-order-1001"
    assert calls["snapshot"] == 1


def test_duplicate_merchant_reference_conflicts_before_second_snapshot(tmp_path, monkeypatch):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-1001",
        )
    )

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="2",
                merchant_reference="ORDER-1001",
            )
        )
    except payment_gateway_service.PaymentMerchantReferenceConflictError:
        pass
    else:
        raise AssertionError("Expected duplicate merchant_reference to conflict.")

    assert calls["snapshot"] == 1


def test_idempotent_retry_with_same_merchant_reference_replays_before_reference_conflict(
    tmp_path,
    monkeypatch,
):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    first = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-1001",
            idempotency_key="retry-order-1001",
        )
    )
    replay = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-1001",
            idempotency_key="retry-order-1001",
        )
    )

    assert replay["payment_id"] == first["payment_id"]
    assert replay["merchant_reference"] == "ORDER-1001"
    assert calls["snapshot"] == 1


def test_merchant_reference_validation_fails_before_electrumx(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    async def forbidden_snapshot(_settings, _scripthash):
        raise AssertionError("Invalid merchant reference must fail before ElectrumX.")

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", forbidden_snapshot)

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="1",
                merchant_reference="ORDER 1001",
            )
        )
    except payment_gateway_service.InvalidPaymentParameterError as exc:
        assert exc.code == "invalid_merchant_reference"
    else:
        raise AssertionError("Expected invalid merchant_reference to be rejected.")


def test_list_persisted_payments_filters_exact_merchant_reference(tmp_path, monkeypatch):
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

    async def fake_snapshot(_settings, _scripthash):
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    first = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-A",
        )
    )
    asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="2",
            merchant_reference="ORDER-B",
        )
    )

    result = asyncio.run(
        payment_gateway_service.list_persisted_payments(
            merchant_reference="ORDER-A",
            limit=10,
        )
    )

    assert [item["payment_id"] for item in result["payments"]] == [first["payment_id"]]
    assert result["payments"][0]["merchant_reference"] == "ORDER-A"


def test_public_persisted_payment_status_hides_merchant_metadata(tmp_path, monkeypatch):
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

    async def fake_snapshot(_settings, _scripthash):
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    created = asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-PRIVATE-1",
            idempotency_key="retry-private-1",
        )
    )
    loaded = asyncio.run(
        payment_gateway_service.get_persisted_payment(created["payment_id"])
    )

    assert "merchant_reference" not in loaded
    assert "idempotency_key" not in loaded


def test_list_persisted_payments_rejects_invalid_merchant_reference_filter(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "payment_api_enabled": True,
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
        }
    )
    monkeypatch.setattr(payment_gateway_service, "get_settings", lambda: settings)
    payment_gateway_service.clear_payment_store_cache()

    try:
        asyncio.run(
            payment_gateway_service.list_persisted_payments(
                merchant_reference="ORDER 1",
            )
        )
    except payment_gateway_service.InvalidPaymentParameterError as exc:
        assert exc.code == "invalid_merchant_reference"
    else:
        raise AssertionError("Expected invalid merchant reference filter to be rejected.")


def test_idempotency_key_conflicts_when_merchant_reference_changes(tmp_path, monkeypatch):
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

    calls = {"snapshot": 0}

    async def fake_snapshot(_settings, _scripthash):
        calls["snapshot"] += 1
        return 500, "tip", ()

    monkeypatch.setattr(payment_gateway_service, "_snapshot_creation_state", fake_snapshot)

    asyncio.run(
        payment_gateway_service.create_persisted_payment(
            address=ADDRESS,
            amount="1",
            merchant_reference="ORDER-A",
            idempotency_key="retry-order",
        )
    )

    try:
        asyncio.run(
            payment_gateway_service.create_persisted_payment(
                address=ADDRESS,
                amount="1",
                merchant_reference="ORDER-B",
                idempotency_key="retry-order",
            )
        )
    except payment_gateway_service.PaymentIdempotencyConflictError:
        pass
    else:
        raise AssertionError(
            "Changing merchant_reference under the same Idempotency-Key must conflict."
        )

    assert calls["snapshot"] == 1
