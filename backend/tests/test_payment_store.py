import sqlite3
import time

import pytest

from app.services.merchant_store import LEGACY_MERCHANT_ID, MerchantStore
from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import (
    PaymentAddressInUseError,
    PaymentIdempotencyConflictError,
    PaymentMerchantReferenceConflictError,
    PaymentNotFoundError,
    PaymentStore,
    PaymentStoreError,
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


def test_list_payments_is_bounded_stable_and_includes_idempotency_key(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    for payment_id, created_at, key in (
        ("pay_a", 1000, "order-a"),
        ("pay_b", 1000, "order-b"),
        ("pay_c", 1001, None),
    ):
        store.create_payment(
            payment_id=payment_id,
            address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
            scripthash="11" * 32,
            amount_sats=100,
            confirmations_required=3,
            created_at=created_at,
            created_height=500,
            expires_at=created_at + 900,
            idempotency_key=key,
            request_hash=None if key is None else f"hash-{payment_id}",
        )

    first, has_more = store.list_payments(limit=2)
    assert [item["payment_id"] for item in first] == ["pay_c", "pay_b"]
    assert has_more is True
    assert first[1]["idempotency_key"] == "order-b"

    second, has_more = store.list_payments(
        limit=2,
        before_created_at=first[-1]["created_at"],
        before_payment_id=first[-1]["payment_id"],
    )
    assert [item["payment_id"] for item in second] == ["pay_a"]
    assert second[0]["idempotency_key"] == "order-a"
    assert has_more is False


def test_list_payments_can_filter_status_without_count_query(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    store.create_payment(
        payment_id="pay_waiting",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    store.create_payment(
        payment_id="pay_expired",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=900,
        created_height=499,
        expires_at=950,
    )
    store.refresh_payment("pay_expired", now=1000)

    waiting, has_more = store.list_payments(status="waiting", limit=10)
    assert [item["payment_id"] for item in waiting] == ["pay_waiting"]
    assert has_more is False

    expired, has_more = store.list_payments(status="expired", limit=10)
    assert [item["payment_id"] for item in expired] == ["pay_expired"]
    assert has_more is False


def test_list_payments_requires_complete_cursor_pair(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.initialize()

    with pytest.raises(PaymentStoreError):
        store.list_payments(before_created_at=1000)


def test_merchant_reference_is_unique_lookupable_and_in_event(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    created = store.create_payment(
        payment_id="pay_order_1",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        merchant_reference="ORDER-1001",
    )

    assert created["merchant_reference"] == "ORDER-1001"
    assert store.get_payment_by_merchant_reference("ORDER-1001")["payment_id"] == "pay_order_1"

    events = store.list_events(payment_id="pay_order_1")
    assert events[0]["payload"]["data"]["merchant_reference"] == "ORDER-1001"

    with pytest.raises(PaymentMerchantReferenceConflictError):
        store.create_payment(
            payment_id="pay_order_2",
            address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
            scripthash="22" * 32,
            amount_sats=200,
            confirmations_required=3,
            created_at=1001,
            created_height=500,
            expires_at=1901,
            merchant_reference="ORDER-1001",
        )

    with pytest.raises(PaymentNotFoundError):
        store.get_payment("pay_order_2")


def test_idempotent_replay_precedes_merchant_reference_conflict(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    first = store.create_payment(
        payment_id="pay_first",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        merchant_reference="ORDER-1001",
        idempotency_key="retry-order-1001",
        request_hash="same-request",
    )

    replay = store.create_payment(
        payment_id="pay_second",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1001,
        created_height=501,
        expires_at=1901,
        merchant_reference="ORDER-1001",
        idempotency_key="retry-order-1001",
        request_hash="same-request",
    )

    assert first["payment_id"] == "pay_first"
    assert replay["payment_id"] == "pay_first"
    assert [event["event_type"] for event in store.list_events()] == ["payment.created"]


def test_list_payments_can_recover_exact_merchant_reference(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    for payment_id, reference in (
        ("pay_a", "ORDER-A"),
        ("pay_b", "ORDER-B"),
    ):
        store.create_payment(
            payment_id=payment_id,
            address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
            scripthash=("11" if payment_id == "pay_a" else "22") * 32,
            amount_sats=100,
            confirmations_required=3,
            created_at=1000,
            created_height=500,
            expires_at=1900,
            merchant_reference=reference,
        )

    rows, has_more = store.list_payments(
        merchant_reference="ORDER-B",
        limit=10,
    )

    assert [item["payment_id"] for item in rows] == ["pay_b"]
    assert rows[0]["merchant_reference"] == "ORDER-B"
    assert has_more is False


def test_initialize_migrates_existing_payments_table_for_merchant_reference(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                scripthash TEXT NOT NULL,
                amount_sats INTEGER NOT NULL CHECK (amount_sats > 0),
                confirmations_required INTEGER NOT NULL CHECK (confirmations_required >= 0),
                created_at INTEGER NOT NULL,
                created_height INTEGER NOT NULL CHECK (created_height >= 0),
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                received_sats INTEGER NOT NULL DEFAULT 0,
                confirmed_sats INTEGER NOT NULL DEFAULT 0,
                policy_confirmed_sats INTEGER NOT NULL DEFAULT 0,
                label TEXT,
                message TEXT,
                updated_at INTEGER NOT NULL
            );
            """
        )

    store = PaymentStore(str(path))
    store.initialize()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(payments)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(payments)").fetchall()
        }

    assert "merchant_reference" in columns
    assert "idx_payments_merchant_reference" in indexes

    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    payment = store.create_payment(
        payment_id="pay_after_migration",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        merchant_reference="ORDER-AFTER-MIGRATION",
    )
    assert payment["merchant_reference"] == "ORDER-AFTER-MIGRATION"



def test_payment_namespaces_are_isolated_by_merchant(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_scope_a",
        display_name="Merchant A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_scope_b",
        display_name="Merchant B",
        now=100,
    )

    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    common = {
        "amount_sats": 100,
        "confirmations_required": 3,
        "created_height": 500,
        "expires_at": 1900,
        "merchant_reference": "ORDER-SAME",
        "idempotency_key": "retry-same",
        "request_hash": "same-request",
    }

    created_a = store.create_payment(
        payment_id="pay_scope_a",
        merchant_id=merchant_a["merchant_id"],
        address="P-merchant-a",
        scripthash="11" * 32,
        created_at=1000,
        **common,
    )
    created_b = store.create_payment(
        payment_id="pay_scope_b",
        merchant_id=merchant_b["merchant_id"],
        address="P-merchant-b",
        scripthash="22" * 32,
        created_at=1001,
        **common,
    )

    assert created_a["merchant_id"] == merchant_a["merchant_id"]
    assert created_b["merchant_id"] == merchant_b["merchant_id"]

    assert store.get_payment_by_merchant_reference(
        "ORDER-SAME",
        merchant_id=merchant_a["merchant_id"],
    )["payment_id"] == "pay_scope_a"
    assert store.get_payment_by_merchant_reference(
        "ORDER-SAME",
        merchant_id=merchant_b["merchant_id"],
    )["payment_id"] == "pay_scope_b"

    replay_a = store.get_payment_by_idempotency_key(
        "retry-same",
        request_hash="same-request",
        merchant_id=merchant_a["merchant_id"],
    )
    replay_b = store.get_payment_by_idempotency_key(
        "retry-same",
        request_hash="same-request",
        merchant_id=merchant_b["merchant_id"],
    )
    assert replay_a["payment_id"] == "pay_scope_a"
    assert replay_b["payment_id"] == "pay_scope_b"

    rows_a, more_a = store.list_payments(
        merchant_id=merchant_a["merchant_id"],
        limit=10,
    )
    rows_b, more_b = store.list_payments(
        merchant_id=merchant_b["merchant_id"],
        limit=10,
    )
    assert [row["payment_id"] for row in rows_a] == ["pay_scope_a"]
    assert [row["payment_id"] for row in rows_b] == ["pay_scope_b"]
    assert more_a is False
    assert more_b is False


def test_initialize_backfills_pre_k_rows_to_legacy_merchant(tmp_path):
    path = tmp_path / "pre-k.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                scripthash TEXT NOT NULL,
                amount_sats INTEGER NOT NULL CHECK (amount_sats > 0),
                confirmations_required INTEGER NOT NULL CHECK (confirmations_required >= 0),
                created_at INTEGER NOT NULL,
                created_height INTEGER NOT NULL CHECK (created_height >= 0),
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                received_sats INTEGER NOT NULL DEFAULT 0,
                confirmed_sats INTEGER NOT NULL DEFAULT 0,
                policy_confirmed_sats INTEGER NOT NULL DEFAULT 0,
                label TEXT,
                message TEXT,
                merchant_reference TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE payment_idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (payment_id) REFERENCES payments(payment_id) ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, address, scripthash, amount_sats,
                confirmations_required, created_at, created_height,
                expires_at, status, version, received_sats,
                confirmed_sats, policy_confirmed_sats,
                label, message, merchant_reference, updated_at
            ) VALUES (
                'pay_legacy', 'PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb', ?, 100,
                3, 1000, 500, 1900, 'waiting', 1, 0, 0, 0,
                NULL, NULL, 'ORDER-LEGACY', 1000
            )
            """,
            ("11" * 32,),
        )
        connection.execute(
            """
            INSERT INTO payment_idempotency_keys (
                idempotency_key, request_hash, payment_id, created_at
            ) VALUES ('retry-legacy', 'hash', 'pay_legacy', 1000)
            """
        )

    store = PaymentStore(str(path))
    store.initialize()

    with sqlite3.connect(path) as connection:
        payment_owner = connection.execute(
            "SELECT merchant_id FROM payments WHERE payment_id = 'pay_legacy'"
        ).fetchone()[0]
        idempotency = connection.execute(
            """
            SELECT merchant_id, idempotency_key, payment_id
            FROM payment_idempotency_keys
            WHERE idempotency_key = 'retry-legacy'
            """
        ).fetchone()
        columns = {
            row[1]: row[5]
            for row in connection.execute(
                "PRAGMA table_info(payment_idempotency_keys)"
            ).fetchall()
        }

    assert payment_owner == LEGACY_MERCHANT_ID
    assert idempotency == (LEGACY_MERCHANT_ID, "retry-legacy", "pay_legacy")
    assert columns["merchant_id"] == 1
    assert columns["idempotency_key"] == 2
    assert MerchantStore(str(path)).get_merchant(LEGACY_MERCHANT_ID)["enabled"] is True


def test_same_address_payment_windows_cannot_overlap(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    store.create_payment(
        payment_id="pay_first_window",
        address="P-window-address",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )

    with pytest.raises(PaymentAddressInUseError):
        store.create_payment(
            payment_id="pay_overlapping_window",
            address="P-window-address",
            scripthash="11" * 32,
            amount_sats=200,
            confirmations_required=1,
            created_at=1500,
            created_height=500,
            expires_at=2400,
        )

    with pytest.raises(PaymentAddressInUseError):
        store.create_payment(
            payment_id="pay_boundary_window",
            address="P-window-address",
            scripthash="11" * 32,
            amount_sats=200,
            confirmations_required=1,
            created_at=1900,
            created_height=500,
            expires_at=2800,
        )


def test_same_address_can_be_reused_after_previous_window_expires(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    store.create_payment(
        payment_id="pay_old_window",
        address="P-reusable-address",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    created = store.create_payment(
        payment_id="pay_new_window",
        address="P-reusable-address",
        scripthash="11" * 32,
        amount_sats=200,
        confirmations_required=1,
        created_at=1901,
        created_height=500,
        expires_at=2801,
    )

    assert created["payment_id"] == "pay_new_window"


def test_address_window_exclusivity_is_global_across_merchants(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_window_a",
        display_name="Window A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_window_b",
        display_name="Window B",
        now=100,
    )
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    store.create_payment(
        payment_id="pay_window_a",
        merchant_id=merchant_a["merchant_id"],
        address="P-shared-window",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )

    with pytest.raises(PaymentAddressInUseError):
        store.create_payment(
            payment_id="pay_window_b",
            merchant_id=merchant_b["merchant_id"],
            address="P-shared-window",
            scripthash="11" * 32,
            amount_sats=100,
            confirmations_required=1,
            created_at=1001,
            created_height=500,
            expires_at=1901,
        )


def test_different_addresses_can_have_overlapping_payment_windows(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    first = store.create_payment(
        payment_id="pay_parallel_a",
        address="P-parallel-a",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    second = store.create_payment(
        payment_id="pay_parallel_b",
        address="P-parallel-b",
        scripthash="22" * 32,
        amount_sats=200,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )

    assert first["payment_id"] == "pay_parallel_a"
    assert second["payment_id"] == "pay_parallel_b"
