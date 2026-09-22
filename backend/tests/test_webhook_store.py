from app.services.payment_state import PaymentTransactionObservation
from app.services.payment_store import PaymentStore


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
