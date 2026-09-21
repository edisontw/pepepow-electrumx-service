import time

import pytest

from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import PaymentNotFoundError, PaymentStore


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
