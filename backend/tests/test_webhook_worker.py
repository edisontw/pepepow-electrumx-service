import asyncio
import time

import pytest

from app.config import get_settings
from app.services import webhook_worker
from app.services.payment_store import PaymentStore
from app.services.webhook_security import ResolvedWebhookTarget, WebhookUrlError
from app.services.webhook_signing import (
    WebhookSigningError,
    derive_webhook_secret,
    verify_webhook_signature,
)
from app.services.webhook_worker import WebhookWorker


def _setup(tmp_path, *, max_attempts=3):
    path = tmp_path / "payments.sqlite3"
    settings = get_settings().model_copy(
        update={
            "payment_db_path": str(path),
            "payment_webhook_enabled": True,
            "payment_webhook_master_key": "m" * 48,
            "payment_webhook_batch_size": 10,
            "payment_webhook_max_attempts": max_attempts,
            "payment_webhook_retry_base_seconds": 30,
            "payment_webhook_retry_max_seconds": 300,
            "payment_webhook_timeout_seconds": 1.0,
        }
    )
    store = PaymentStore(str(path))
    store.create_webhook_endpoint(
        endpoint_id="wh_test",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )
    store.set_chain_tip(500, tip_hash="tip", updated_at=950)
    store.create_payment(
        payment_id="pay_test",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    return settings, store


def _target():
    return ResolvedWebhookTarget(
        hostname="merchant.example",
        port=443,
        path_and_query="/hook",
        host_header="merchant.example",
        addresses=("93.184.216.34",),
    )


def test_worker_delivers_signed_event_and_marks_success(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path)
    sent = {}

    async def fake_resolve(_url, **_kwargs):
        return _target()

    async def fake_sender(target, *, body, headers, timeout_seconds):
        sent.update({
            "target": target,
            "body": body,
            "headers": dict(headers),
            "timeout": timeout_seconds,
        })
        return 204

    monkeypatch.setattr(webhook_worker, "resolve_webhook_target", fake_resolve)
    worker = WebhookWorker(settings, sender=fake_sender)
    worker.store = store

    asyncio.run(worker.run_once())

    due = store.list_events(payment_id="pay_test")
    delivery_id = sent["headers"]["X-PepewPay-Delivery-Id"]
    delivery = store.get_webhook_delivery(delivery_id)
    assert delivery["status"] == "delivered"
    assert delivery["attempt_count"] == 1
    assert sent["headers"]["X-PepewPay-Event-Id"] == due[0]["event_id"]

    timestamp = int(sent["headers"]["X-PepewPay-Timestamp"])
    secret = derive_webhook_secret(settings.payment_webhook_master_key, "wh_test")
    assert verify_webhook_signature(
        secret,
        event_id=due[0]["event_id"],
        timestamp=timestamp,
        body=sent["body"],
        signature=sent["headers"]["X-PepewPay-Signature"],
    )


def test_worker_retries_5xx_then_delivers_same_event(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path)
    clock = {"now": 1000}
    calls = []

    async def fake_resolve(_url, **_kwargs):
        return _target()

    async def fake_sender(_target_value, *, body, headers, timeout_seconds):
        calls.append((body, dict(headers), timeout_seconds))
        return 503 if len(calls) == 1 else 200

    monkeypatch.setattr(webhook_worker, "resolve_webhook_target", fake_resolve)
    monkeypatch.setattr(webhook_worker.time, "time", lambda: clock["now"])
    worker = WebhookWorker(settings, sender=fake_sender)
    worker.store = store

    asyncio.run(worker.run_once())
    delivery_id = calls[0][1]["X-PepewPay-Delivery-Id"]
    first = store.get_webhook_delivery(delivery_id)
    assert first["status"] == "retry"
    assert first["attempt_count"] == 1
    assert first["next_attempt_at"] == 1030

    clock["now"] = 1030
    asyncio.run(worker.run_once())
    second = store.get_webhook_delivery(delivery_id)
    assert second["status"] == "delivered"
    assert second["attempt_count"] == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1]["X-PepewPay-Event-Id"] == calls[1][1]["X-PepewPay-Event-Id"]
    assert calls[0][1]["X-PepewPay-Delivery-Id"] == calls[1][1]["X-PepewPay-Delivery-Id"]


def test_worker_marks_nonretryable_4xx_dead(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path)

    async def fake_resolve(_url, **_kwargs):
        return _target()

    async def fake_sender(_target_value, **_kwargs):
        return 400

    monkeypatch.setattr(webhook_worker, "resolve_webhook_target", fake_resolve)
    worker = WebhookWorker(settings, sender=fake_sender)
    worker.store = store

    asyncio.run(worker.run_once())
    assert store.list_due_webhook_deliveries(now=10_000, limit=10) == []

    log = store.list_webhook_deliveries(limit=10)
    assert len(log) == 1
    result = store.get_webhook_delivery(log[0]["delivery_id"])
    assert result["status"] == "dead"
    assert result["http_status"] == 400
    assert result["attempt_count"] == 1


def test_worker_blocks_unsafe_target_without_sending(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path)
    sent = {"count": 0}

    async def unsafe(_url, **_kwargs):
        raise WebhookUrlError("unsafe_webhook_target", "unsafe")

    async def fake_sender(*_args, **_kwargs):
        sent["count"] += 1
        return 200

    monkeypatch.setattr(webhook_worker, "resolve_webhook_target", unsafe)
    worker = WebhookWorker(settings, sender=fake_sender)
    worker.store = store

    asyncio.run(worker.run_once())

    log = store.list_webhook_deliveries(limit=10)
    assert len(log) == 1
    result = store.get_webhook_delivery(log[0]["delivery_id"])
    assert sent["count"] == 0
    assert result["status"] == "dead"
    assert result["error_code"] == "unsafe_webhook_target"


def test_worker_marks_retryable_error_dead_at_max_attempts(tmp_path, monkeypatch):
    settings, store = _setup(tmp_path, max_attempts=1)

    async def fake_resolve(_url, **_kwargs):
        return _target()

    async def fake_sender(*_args, **_kwargs):
        return 503

    monkeypatch.setattr(webhook_worker, "resolve_webhook_target", fake_resolve)
    worker = WebhookWorker(settings, sender=fake_sender)
    worker.store = store

    asyncio.run(worker.run_once())

    log = store.list_webhook_deliveries(limit=10)
    assert len(log) == 1
    result = store.get_webhook_delivery(log[0]["delivery_id"])
    assert result["status"] == "dead"
    assert result["attempt_count"] == 1


def test_worker_fails_closed_when_master_key_missing(tmp_path):
    settings, _store = _setup(tmp_path)
    settings = settings.model_copy(update={"payment_webhook_master_key": None})
    worker = WebhookWorker(settings)

    async def run():
        with pytest.raises(WebhookSigningError):
            await worker.start()

    asyncio.run(run())
