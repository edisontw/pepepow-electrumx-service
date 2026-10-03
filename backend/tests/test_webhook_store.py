import sqlite3

import pytest

from app.services.merchant_store import LEGACY_MERCHANT_ID, MerchantStore
from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import PaymentNotFoundError, PaymentStore
from app.services.webhook_signing import derive_webhook_secret


def _payment(store: PaymentStore, payment_id: str, now: int = 1000):
    store.set_chain_tip(500, tip_hash="tip", updated_at=now)
    return store.create_payment(
        payment_id=payment_id,
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=now,
        created_height=500,
        expires_at=now + 900,
    )


def test_event_enqueues_one_delivery_for_existing_endpoint(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.create_webhook_endpoint(
        endpoint_id="wh_test",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )

    _payment(store, "pay_test")
    events = store.list_events(payment_id="pay_test")
    due = store.list_due_webhook_deliveries(now=1000, limit=10)

    assert len(events) == 1
    assert len(due) == 1
    assert due[0]["event_id"] == events[0]["event_id"]
    assert due[0]["endpoint_id"] == "wh_test"
    assert due[0]["status"] == "pending"


def test_endpoint_created_after_event_does_not_backfill_old_event(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    _payment(store, "pay_test")

    store.create_webhook_endpoint(
        endpoint_id="wh_late",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=1100,
    )

    assert store.list_due_webhook_deliveries(now=1200, limit=10) == []


def test_webhook_event_filter_only_enqueues_selected_events(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.create_webhook_endpoint(
        endpoint_id="wh_confirmed_only",
        url="https://merchant.example/hook",
        event_types=("payment.paid_confirmed",),
        created_at=900,
    )

    _payment(store, "pay_test")
    assert store.list_due_webhook_deliveries(now=1000, limit=10) == []


def test_webhook_delivery_state_persists_success_and_failure(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.create_webhook_endpoint(
        endpoint_id="wh_test",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )
    _payment(store, "pay_test")
    delivery = store.list_due_webhook_deliveries(now=1000, limit=10)[0]

    store.mark_webhook_delivery_failure(
        delivery["delivery_id"],
        http_status=503,
        error_code="webhook_http_error",
        attempted_at=1001,
        next_attempt_at=1031,
        dead=False,
    )
    failed = store.get_webhook_delivery(delivery["delivery_id"])
    assert failed["status"] == "retry"
    assert failed["attempt_count"] == 1
    assert failed["next_attempt_at"] == 1031

    store.mark_webhook_delivery_success(
        delivery["delivery_id"],
        http_status=204,
        attempted_at=1031,
    )
    delivered = store.get_webhook_delivery(delivery["delivery_id"])
    assert delivered["status"] == "delivered"
    assert delivered["attempt_count"] == 2
    assert delivered["http_status"] == 204
    assert delivered["delivered_at"] == 1031


def test_disabled_endpoint_is_not_selected_for_delivery(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.create_webhook_endpoint(
        endpoint_id="wh_test",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )
    store.disable_webhook_endpoint("wh_test", updated_at=950)
    _payment(store, "pay_test")

    assert store.list_due_webhook_deliveries(now=1000, limit=10) == []


def test_selected_state_event_enqueues_delivery(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    store.create_webhook_endpoint(
        endpoint_id="wh_confirmed_only",
        url="https://merchant.example/hook",
        event_types=("payment.paid_confirmed",),
        created_at=900,
    )
    _payment(store, "pay_test")
    assert store.list_due_webhook_deliveries(now=1000, limit=10) == []

    store.upsert_transaction(
        "pay_test",
        PaymentTransactionObservation(
            txid="a" * 64,
            vout=0,
            value_sats=100,
            height=501,
            first_seen_at=1001,
        ),
        updated_at=1001,
    )
    store.set_chain_tip(503, tip_hash="tip3", updated_at=1002)
    store.refresh_payment("pay_test", now=1002)

    due = store.list_due_webhook_deliveries(now=1002, limit=10)
    assert len(due) == 1
    assert due[0]["endpoint_id"] == "wh_confirmed_only"
    events = store.list_events(payment_id="pay_test")
    assert events[-1]["event_type"] == "payment.paid_confirmed"
    assert due[0]["event_id"] == events[-1]["event_id"]


def test_webhook_delivery_log_supports_endpoint_filter(tmp_path):
    store = PaymentStore(str(tmp_path / "payments.sqlite3"))
    for endpoint_id in ("wh_one", "wh_two"):
        store.create_webhook_endpoint(
            endpoint_id=endpoint_id,
            url=f"https://{endpoint_id}.example/hook",
            event_types=None,
            created_at=900,
        )

    _payment(store, "pay_test")
    all_deliveries = store.list_webhook_deliveries(limit=10)
    one = store.list_webhook_deliveries(endpoint_id="wh_one", limit=10)

    assert len(all_deliveries) == 2
    assert len(one) == 1
    assert one[0]["endpoint_id"] == "wh_one"
    assert one[0]["event_type"] == "payment.created"
    assert one[0]["payment_id"] == "pay_test"



def test_webhook_ownership_isolated_across_merchants(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_hook_a",
        display_name="Merchant A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_hook_b",
        display_name="Merchant B",
        now=100,
    )
    store = PaymentStore(str(path))

    store.create_webhook_endpoint(
        endpoint_id="wh_a",
        merchant_id=merchant_a["merchant_id"],
        url="https://a.example/hook",
        event_types=None,
        created_at=900,
    )
    store.create_webhook_endpoint(
        endpoint_id="wh_b",
        merchant_id=merchant_b["merchant_id"],
        url="https://b.example/hook",
        event_types=None,
        created_at=900,
    )

    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    store.create_payment(
        payment_id="pay_a",
        merchant_id=merchant_a["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    store.create_payment(
        payment_id="pay_b",
        merchant_id=merchant_b["merchant_id"],
        address="P-webhook-merchant-b",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1001,
        created_height=500,
        expires_at=1901,
    )

    due = store.list_due_webhook_deliveries(now=1001, limit=10)
    assert {(item["endpoint_id"], item["event_id"]) for item in due} == {
        ("wh_a", store.list_events(payment_id="pay_a")[0]["event_id"]),
        ("wh_b", store.list_events(payment_id="pay_b")[0]["event_id"]),
    }

    endpoints_a = store.list_webhook_endpoints(
        merchant_id=merchant_a["merchant_id"],
    )
    endpoints_b = store.list_webhook_endpoints(
        merchant_id=merchant_b["merchant_id"],
    )
    assert [item["endpoint_id"] for item in endpoints_a] == ["wh_a"]
    assert [item["endpoint_id"] for item in endpoints_b] == ["wh_b"]
    assert all("merchant_id" not in item for item in endpoints_a + endpoints_b)

    deliveries_a = store.list_webhook_deliveries(
        merchant_id=merchant_a["merchant_id"],
        limit=10,
    )
    deliveries_b = store.list_webhook_deliveries(
        merchant_id=merchant_b["merchant_id"],
        limit=10,
    )
    assert [item["endpoint_id"] for item in deliveries_a] == ["wh_a"]
    assert [item["endpoint_id"] for item in deliveries_b] == ["wh_b"]

    assert store.list_webhook_deliveries(
        merchant_id=merchant_a["merchant_id"],
        endpoint_id="wh_b",
        limit=10,
    ) == []

    with pytest.raises(PaymentNotFoundError):
        store.disable_webhook_endpoint(
            "wh_b",
            merchant_id=merchant_a["merchant_id"],
            updated_at=1100,
        )

    assert store.get_webhook_endpoint(
        "wh_b",
        merchant_id=merchant_b["merchant_id"],
    )["enabled"] is True


def test_webhook_ownership_migration_backfills_legacy_without_rewriting_payload(tmp_path):
    path = tmp_path / "pre-k3.sqlite3"
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    store.create_webhook_endpoint(
        endpoint_id="wh_legacy",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )
    store.create_payment(
        payment_id="pay_legacy",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        merchant_reference="ORDER-LEGACY",
    )
    original_payload = store.list_events(payment_id="pay_legacy")[0]["payload"]
    original_secret = derive_webhook_secret("m" * 48, "wh_legacy")

    with sqlite3.connect(path) as connection:
        # Model a pre-K3 database while preserving K2 payment ownership.
        connection.execute("DROP INDEX IF EXISTS idx_events_merchant_created")
        connection.execute("DROP INDEX IF EXISTS idx_webhook_endpoints_merchant_enabled")

        connection.execute(
            """
            CREATE TABLE events_pre_k3 AS
            SELECT event_id, payment_id, event_type, payment_version,
                   created_at, payload_json
            FROM events
            """
        )
        connection.execute("DROP TABLE events")
        connection.execute("ALTER TABLE events_pre_k3 RENAME TO events")

        connection.execute(
            """
            CREATE TABLE webhook_endpoints_pre_k3 AS
            SELECT endpoint_id, url, event_types_json, enabled,
                   created_at, updated_at
            FROM webhook_endpoints
            """
        )
        connection.execute("DROP TABLE webhook_endpoints")
        connection.execute(
            "ALTER TABLE webhook_endpoints_pre_k3 RENAME TO webhook_endpoints"
        )

    migrated = PaymentStore(str(path))
    migrated.initialize()

    with sqlite3.connect(path) as connection:
        event_owner = connection.execute(
            "SELECT merchant_id FROM events WHERE event_id = ?",
            (migrated.list_events(payment_id="pay_legacy")[0]["event_id"],),
        ).fetchone()[0]
        endpoint_owner = connection.execute(
            "SELECT merchant_id FROM webhook_endpoints WHERE endpoint_id = 'wh_legacy'"
        ).fetchone()[0]

    assert event_owner == LEGACY_MERCHANT_ID
    assert endpoint_owner == LEGACY_MERCHANT_ID
    assert migrated.list_events(payment_id="pay_legacy")[0]["payload"] == original_payload
    migrated_secret = derive_webhook_secret("m" * 48, "wh_legacy")
    assert migrated_secret == original_secret
