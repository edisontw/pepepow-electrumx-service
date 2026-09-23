import time

import pytest

from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import (
    PaymentIdempotencyConflictError,
    PaymentNotFoundError,
    PaymentStore,
)


def create(store: PaymentStore, now: int = 1000):
    store.set_chain_tip(500, tip_hash="tip", updated_at=now)
    return store.create_payment(
        payment_id="pay_test",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=now,
        created_height=500,
        expires_at=now + 900,
        label="Demo",
        message="Order 1",
    )


def test_sqlite_payment_persists_across_store_instances(tmp_path):
    path = tmp_path / "payments.sqlite3"
    first = PaymentStore(str(path))
    create(first)

    second = PaymentStore(str(path))
    payment = second.get_payment("pay_test")

    assert payment["amount_sats"] == 100
    assert payment["created_height"] == 500
    assert payment["status"] == "waiting"
    assert payment["label"] == "Demo"


def test_refresh_uses_persisted_transactions_and_true_confirmations(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    observation = PaymentTransactionObservation(
        txid="a" * 64,
        vout=0,
        value_sats=100,
        height=501,
        first_seen_at=1001,
    )
    store.upsert_transaction("pay_test", observation, updated_at=1001)

    store.set_chain_tip(502, tip_hash="tip2", updated_at=1002)
    unconfirmed = store.refresh_payment("pay_test", now=1002)
    assert unconfirmed["status"] == "paid_unconfirmed"
    assert unconfirmed["policy_confirmed_sats"] == 0

    store.set_chain_tip(503, tip_hash="tip3", updated_at=1003)
    confirmed = store.refresh_payment("pay_test", now=1003)
    assert confirmed["status"] == "paid_confirmed"
    assert confirmed["policy_confirmed_sats"] == 100


def test_reorg_update_can_downgrade_persisted_payment(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    confirmed = PaymentTransactionObservation(
        txid="a" * 64,
        vout=0,
        value_sats=100,
        height=501,
        first_seen_at=1001,
    )
    store.upsert_transaction("pay_test", confirmed, updated_at=1001)
    store.set_chain_tip(503, tip_hash="tip3", updated_at=1003)
    assert store.refresh_payment("pay_test", now=1003)["status"] == "paid_confirmed"

    reorged = PaymentTransactionObservation(
        txid="a" * 64,
        vout=0,
        value_sats=100,
        height=0,
        first_seen_at=1001,
    )
    store.upsert_transaction("pay_test", reorged, updated_at=1004)
    downgraded = store.refresh_payment("pay_test", now=1004)

    assert downgraded["status"] == "paid_unconfirmed"
    assert downgraded["confirmed_sats"] == 0


def test_expiry_is_derived_without_electrumx_request(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store, now=1000)

    expired = store.refresh_payment("pay_test", now=1901)

    assert expired["status"] == "expired"
    assert expired["version"] == 2


def test_duplicate_outpoint_is_upserted_not_double_counted(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    first = PaymentTransactionObservation(
        txid="a" * 64,
        vout=1,
        value_sats=100,
        height=0,
        first_seen_at=1001,
    )
    later = PaymentTransactionObservation(
        txid="a" * 64,
        vout=1,
        value_sats=100,
        height=501,
        first_seen_at=1002,
    )
    store.upsert_transaction("pay_test", first, updated_at=1001)
    store.upsert_transaction("pay_test", later, updated_at=1002)

    observations = store.get_observations("pay_test")
    assert len(observations) == 1
    assert observations[0].height == 501
    assert observations[0].first_seen_at == 1001


def test_unknown_payment_raises_not_found(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.initialize()

    with pytest.raises(PaymentNotFoundError):
        store.get_payment("pay_missing")


def test_payment_creation_emits_one_deterministic_created_event(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)

    events = store.list_events(payment_id="pay_test")

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "payment.created"
    assert event["payment_version"] == 1
    assert event["payload"]["schema_version"] == 1
    assert event["payload"]["payment_id"] == "pay_test"
    assert event["payload"]["data"]["status"] == "waiting"

    restarted = PaymentStore(str(tmp_path / "payments.sqlite3"))
    restarted_events = restarted.list_events(payment_id="pay_test")
    assert restarted_events[0]["event_id"] == event["event_id"]


def test_state_change_event_is_atomic_and_duplicate_refresh_is_deduplicated(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    store.upsert_transaction(
        "pay_test",
        PaymentTransactionObservation(
            txid="a" * 64,
            vout=0,
            value_sats=40,
            height=0,
            first_seen_at=1001,
        ),
        updated_at=1001,
    )

    first = store.refresh_payment("pay_test", now=1001)
    second = store.refresh_payment("pay_test", now=1002)
    events = store.list_events(payment_id="pay_test")

    assert first["status"] == "partial"
    assert first["version"] == 2
    assert second["version"] == 2
    assert [event["event_type"] for event in events] == [
        "payment.created",
        "payment.partial",
    ]
    assert events[-1]["payment_version"] == 2
    assert events[-1]["payload"]["data"]["received_sats"] == 40


def test_reorg_or_dropped_mempool_creates_new_versioned_state_event(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    observation = PaymentTransactionObservation(
        txid="a" * 64,
        vout=0,
        value_sats=100,
        height=0,
        first_seen_at=1001,
    )
    store.upsert_transaction("pay_test", observation, updated_at=1001)
    paid = store.refresh_payment("pay_test", now=1001)
    assert paid["status"] == "paid_unconfirmed"

    store.delete_transactions_not_in("pay_test", set())
    waiting = store.refresh_payment("pay_test", now=1002)

    events = store.list_events(payment_id="pay_test")
    assert waiting["status"] == "waiting"
    assert waiting["version"] == 3
    assert [event["event_type"] for event in events] == [
        "payment.created",
        "payment.paid_unconfirmed",
        "payment.waiting",
    ]
    assert [event["payment_version"] for event in events] == [1, 2, 3]


def test_event_sequence_supports_incremental_consumers(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    create(store)
    first = store.list_events()
    assert len(first) == 1

    store.upsert_transaction(
        "pay_test",
        PaymentTransactionObservation(
            txid="a" * 64,
            vout=0,
            value_sats=40,
            height=0,
            first_seen_at=1001,
        ),
        updated_at=1001,
    )
    store.refresh_payment("pay_test", now=1001)

    later = store.list_events(after_sequence=first[0]["sequence"])
    assert len(later) == 1
    assert later[0]["event_type"] == "payment.partial"
    assert later[0]["sequence"] > first[0]["sequence"]


def test_payment_creation_idempotency_replays_existing_payment_and_event(tmp_path):
    path = tmp_path / "payments.sqlite3"
    first = PaymentStore(str(path))
    first.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    created = first.create_payment(
        payment_id="pay_first",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        label="Demo",
        message="Order 1",
        idempotency_key="order-123-attempt-1",
        request_hash="hash-a",
    )

    restarted = PaymentStore(str(path))
    replayed = restarted.create_payment(
        payment_id="pay_should_not_be_created",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1010,
        created_height=501,
        expires_at=1910,
        label="Demo",
        message="Order 1",
        idempotency_key="order-123-attempt-1",
        request_hash="hash-a",
    )

    assert created["payment_id"] == "pay_first"
    assert replayed["payment_id"] == "pay_first"
    assert restarted.get_payment_by_idempotency_key(
        "order-123-attempt-1",
        request_hash="hash-a",
    )["payment_id"] == "pay_first"
    assert [event["event_type"] for event in restarted.list_events()] == [
        "payment.created"
    ]


def test_payment_creation_idempotency_rejects_changed_request(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    store.create_payment(
        payment_id="pay_first",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        idempotency_key="order-123-attempt-1",
        request_hash="hash-a",
    )

    with pytest.raises(PaymentIdempotencyConflictError):
        store.create_payment(
            payment_id="pay_second",
            address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
            scripthash="11" * 32,
            amount_sats=200,
            confirmations_required=3,
            created_at=1001,
            created_height=500,
            expires_at=1901,
            idempotency_key="order-123-attempt-1",
            request_hash="hash-b",
        )

    assert store.get_payment("pay_first")["amount_sats"] == 100
    with pytest.raises(PaymentNotFoundError):
        store.get_payment("pay_second")
