import asyncio
import time

from app.config import get_settings
from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import PaymentStore
from app.services.payment_watcher import PaymentWatcher

ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
SCRIPTHASH = "11" * 32
OLD_TX = "a" * 64
NEW_TX = "b" * 64


class FakeSubscriptionClient:
    def __init__(self, history, transactions):
        self.history = history
        self.transactions = transactions
        self.connected = True
        self.requests = []

    async def request(self, method, params=None):
        self.requests.append((method, params or []))
        if method == "blockchain.scripthash.get_history":
            return self.history
        if method == "blockchain.transaction.get":
            return self.transactions[params[0]]
        raise AssertionError(f"Unexpected method: {method}")


def make_store(path):
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    store.create_payment(
        payment_id="pay_test",
        address=ADDRESS,
        scripthash=SCRIPTHASH,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=4_000_000_000,
        baseline_txids=(OLD_TX,),
    )
    return store


def verbose_tx(txid, address=ADDRESS, value="0.00000100"):
    return {
        "txid": txid,
        "vout": [
            {
                "n": 0,
                "value": value,
                "scriptPubKey": {"address": address},
            }
        ],
    }


def test_watcher_reconcile_ignores_creation_baseline(tmp_path):
    settings = get_settings().model_copy(
        update={
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_watcher_max_subscriptions": 100,
        }
    )
    watcher = PaymentWatcher(settings)
    watcher.store = make_store(tmp_path / "payments.sqlite3")

    client = FakeSubscriptionClient(
        history=[
            {"tx_hash": OLD_TX, "height": 0},
            {"tx_hash": NEW_TX, "height": 501},
        ],
        transactions={
            OLD_TX: verbose_tx(OLD_TX),
            NEW_TX: verbose_tx(NEW_TX),
        },
    )

    asyncio.run(watcher._reconcile_scripthash(client, SCRIPTHASH))

    payment = watcher.store.get_payment("pay_test")
    observations = watcher.store.get_observations("pay_test")

    assert payment["received_sats"] == 100
    assert len(observations) == 1
    assert observations[0].txid == NEW_TX
    assert all(
        not (method == "blockchain.transaction.get" and params[0] == OLD_TX)
        for method, params in client.requests
    )


def test_watcher_removes_dropped_mempool_transaction(tmp_path):
    settings = get_settings().model_copy(
        update={
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_watcher_max_subscriptions": 100,
        }
    )
    watcher = PaymentWatcher(settings)
    watcher.store = make_store(tmp_path / "payments.sqlite3")

    now = int(time.time())
    watcher.store.upsert_transaction(
        "pay_test",
        PaymentTransactionObservation(
            txid=NEW_TX,
            vout=0,
            value_sats=100,
            height=0,
            first_seen_at=now,
        ),
        updated_at=now,
    )
    watcher.store.refresh_payment("pay_test", now=now)
    assert watcher.store.get_payment("pay_test")["status"] == "paid_unconfirmed"

    client = FakeSubscriptionClient(history=[], transactions={})
    asyncio.run(watcher._reconcile_scripthash(client, SCRIPTHASH))

    payment = watcher.store.get_payment("pay_test")
    assert payment["received_sats"] == 0
    assert payment["status"] == "waiting"
    assert watcher.store.get_observations("pay_test") == ()


def test_header_notification_advances_persisted_confirmations(tmp_path):
    settings = get_settings().model_copy(
        update={
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_watcher_max_subscriptions": 100,
        }
    )
    watcher = PaymentWatcher(settings)
    watcher.store = make_store(tmp_path / "payments.sqlite3")

    watcher.store.upsert_transaction(
        "pay_test",
        PaymentTransactionObservation(
            txid=NEW_TX,
            vout=0,
            value_sats=100,
            height=501,
            first_seen_at=1001,
        ),
        updated_at=1001,
    )
    watcher.store.set_chain_tip(502, tip_hash="tip2", updated_at=1002)
    watcher.store.refresh_payment("pay_test", now=1002)
    assert watcher.store.get_payment("pay_test")["status"] == "paid_unconfirmed"

    asyncio.run(watcher._apply_header({"height": 503, "hex": "00"}))

    payment = watcher.store.get_payment("pay_test")
    assert payment["status"] == "paid_confirmed"
    assert payment["policy_confirmed_sats"] == 100


def test_duplicate_reconciliation_does_not_increment_version(tmp_path):
    settings = get_settings().model_copy(
        update={
            "payment_db_path": str(tmp_path / "payments.sqlite3"),
            "payment_watcher_max_subscriptions": 100,
        }
    )
    watcher = PaymentWatcher(settings)
    watcher.store = make_store(tmp_path / "payments.sqlite3")
    watcher.store.set_chain_tip(503, tip_hash="tip3", updated_at=1003)

    client = FakeSubscriptionClient(
        history=[{"tx_hash": NEW_TX, "height": 501}],
        transactions={NEW_TX: verbose_tx(NEW_TX)},
    )

    asyncio.run(watcher._reconcile_scripthash(client, SCRIPTHASH))
    first = watcher.store.get_payment("pay_test")
    first_events = watcher.store.list_events(payment_id="pay_test")

    asyncio.run(watcher._reconcile_scripthash(client, SCRIPTHASH))
    second = watcher.store.get_payment("pay_test")
    second_events = watcher.store.list_events(payment_id="pay_test")

    assert first["status"] == "paid_confirmed"
    assert second["version"] == first["version"]
    assert [event["event_id"] for event in second_events] == [
        event["event_id"] for event in first_events
    ]
    assert [event["event_type"] for event in second_events] == [
        "payment.created",
        "payment.paid_confirmed",
    ]
