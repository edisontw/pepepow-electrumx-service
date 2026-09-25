import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "reference_merchant.py"

spec = importlib.util.spec_from_file_location("reference_merchant", EXAMPLE)
assert spec is not None and spec.loader is not None
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class FakeResponse:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode("utf-8")
        self.closed = False

    def read(self):
        return self.raw

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.payloads.pop(0))


def signed_headers(secret, event, *, timestamp=1_760_000_000, delivery_id="dlv_test"):
    raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    message = (
        str(timestamp).encode("ascii")
        + b"."
        + event["event_id"].encode("utf-8")
        + b"."
        + raw
    )
    signature = "v1=" + hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-PepewPay-Event-Id": event["event_id"],
        "X-PepewPay-Delivery-Id": delivery_id,
        "X-PepewPay-Timestamp": str(timestamp),
        "X-PepewPay-Signature": signature,
    }
    return headers, raw


def payment_response(order_id="ORDER-1234", payment_id="pay_abcdefgh1234", version=1):
    return {
        "ok": True,
        "payment_id": payment_id,
        "merchant_reference": order_id,
        "status": "waiting",
        "version": version,
    }


def event(
    event_id,
    *,
    payment_id="pay_abcdefgh1234",
    version=1,
    status="waiting",
    merchant_reference="ORDER-1234",
    event_type="payment.created",
):
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "payment_id": payment_id,
        "payment_version": version,
        "created_at": 1_760_000_000,
        "data": {
            "status": status,
            "merchant_reference": merchant_reference,
        },
    }


def test_checkout_url_contains_only_payment_capability():
    url = reference.build_checkout_url("pay_abcdefgh1234")
    parsed = urlparse(url)

    assert parsed.scheme == "https"
    assert parsed.netloc == "pay.pepepow.net"
    assert parse_qs(parsed.query) == {"payment_id": ["pay_abcdefgh1234"]}
    assert "merchant" not in parsed.query.lower()
    assert "secret" not in parsed.query.lower()


def test_create_payment_keeps_bearer_server_side_and_sends_stable_idempotency():
    opener = FakeOpener([payment_response()])
    client = reference.MerchantApiClient(
        api_key="merchant-secret-api-key",
        api_base="https://pay.example",
        opener=opener,
    )

    result = client.create_payment(
        merchant_reference="ORDER-1234",
        idempotency_key="create:ORDER-1234:v1",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        amount="12.34",
        confirmations=3,
        expires_in=900,
    )

    request, timeout = opener.requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    body = json.loads(request.data.decode("utf-8"))

    assert request.full_url == "https://pay.example/api/v1/payments"
    assert timeout == 10.0
    assert headers["authorization"] == "Bearer merchant-secret-api-key"
    assert headers["idempotency-key"] == "create:ORDER-1234:v1"
    assert body["merchant_reference"] == "ORDER-1234"
    assert body["amount"] == "12.34"
    assert "merchant-secret-api-key" not in request.data.decode("utf-8")
    assert result["payment_id"] == "pay_abcdefgh1234"


def test_recovery_uses_authenticated_exact_merchant_reference_filter():
    opener = FakeOpener([
        {
            "ok": True,
            "payments": [payment_response(order_id="ORDER/1234")],
            "has_more": False,
            "next_before_created_at": None,
            "next_before_payment_id": None,
        }
    ])
    client = reference.MerchantApiClient(
        api_key="merchant-secret-api-key",
        api_base="https://pay.example",
        opener=opener,
    )

    recovered = client.recover_payment("ORDER/1234")

    request, _ = opener.requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    query = parse_qs(urlparse(request.full_url).query)

    assert headers["authorization"] == "Bearer merchant-secret-api-key"
    assert query == {"merchant_reference": ["ORDER/1234"], "limit": ["2"]}
    assert recovered["payment_id"] == "pay_abcdefgh1234"


def test_webhook_verifies_exact_raw_body_and_case_insensitive_headers():
    secret = "endpoint-signing-secret"
    payload = event("evt_valid")
    headers, raw = signed_headers(secret, payload)
    headers = {key.lower(): value for key, value in headers.items()}

    verified = reference.verify_webhook(
        headers=headers,
        raw_body=raw,
        signing_secret=secret,
        now=1_760_000_020,
    )

    assert verified["event_id"] == "evt_valid"
    assert verified["payment_version"] == 1


def test_webhook_rejects_tampered_body():
    secret = "endpoint-signing-secret"
    payload = event("evt_tampered")
    headers, raw = signed_headers(secret, payload)
    tampered = raw.replace(b'"waiting"', b'"partial"')

    with pytest.raises(reference.WebhookVerificationError, match="signature"):
        reference.verify_webhook(
            headers=headers,
            raw_body=tampered,
            signing_secret=secret,
            now=1_760_000_020,
        )


def test_webhook_rejects_timestamp_outside_replay_window():
    secret = "endpoint-signing-secret"
    payload = event("evt_old")
    headers, raw = signed_headers(secret, payload, timestamp=1_760_000_000)

    with pytest.raises(reference.WebhookVerificationError, match="replay_window"):
        reference.verify_webhook(
            headers=headers,
            raw_body=raw,
            signing_secret=secret,
            now=1_760_000_301,
            replay_window_seconds=300,
        )


def test_store_can_bind_signed_created_event_to_reserved_order_before_create_response(tmp_path):
    store = reference.ReferenceMerchantStore(str(tmp_path / "merchant.sqlite3"))
    store.reserve_order("ORDER-1234", "create:ORDER-1234:v1", now=10)

    result = store.apply_verified_event(
        event("evt_created"),
        received_at=11,
    )

    order = store.get_order("ORDER-1234")
    assert result == "applied"
    assert order["payment_id"] == "pay_abcdefgh1234"
    assert order["payment_status"] == "waiting"
    assert order["payment_version"] == 1
    assert order["checkout_url"] == "https://pay.pepepow.net/?payment_id=pay_abcdefgh1234"


def test_store_deduplicates_event_id_and_uses_version_not_status_ranking(tmp_path):
    store = reference.ReferenceMerchantStore(str(tmp_path / "merchant.sqlite3"))
    store.reserve_order("ORDER-1234", "create:ORDER-1234:v1", now=10)

    created = event("evt_v1", version=1, status="waiting")
    confirmed = event(
        "evt_v2",
        version=2,
        status="paid_confirmed",
        event_type="payment.paid_confirmed",
    )
    reorg = event(
        "evt_v3",
        version=3,
        status="paid_unconfirmed",
        event_type="payment.paid_unconfirmed",
    )
    late_old = event(
        "evt_late_v2",
        version=2,
        status="paid_confirmed",
        event_type="payment.paid_confirmed",
    )

    assert store.apply_verified_event(created, received_at=11) == "applied"
    assert store.apply_verified_event(created, received_at=12) == "duplicate"
    assert store.apply_verified_event(confirmed, received_at=13) == "applied"
    assert store.apply_verified_event(reorg, received_at=14) == "applied"
    assert store.apply_verified_event(late_old, received_at=15) == "stale"

    order = store.get_order("ORDER-1234")
    assert order["payment_version"] == 3
    assert order["payment_status"] == "paid_unconfirmed"


def test_unknown_order_event_rolls_back_dedupe_so_platform_retry_can_apply(tmp_path):
    store = reference.ReferenceMerchantStore(str(tmp_path / "merchant.sqlite3"))
    payload = event("evt_race")

    with pytest.raises(reference.UnknownMerchantOrderError):
        store.apply_verified_event(payload, received_at=10)

    store.reserve_order("ORDER-1234", "create:ORDER-1234:v1", now=11)
    assert store.apply_verified_event(payload, received_at=12) == "applied"


def test_bind_payment_requires_reserved_order_and_preserves_payment_identity(tmp_path):
    store = reference.ReferenceMerchantStore(str(tmp_path / "merchant.sqlite3"))
    store.reserve_order("ORDER-1234", "create:ORDER-1234:v1", now=10)

    bound = store.bind_payment(
        "ORDER-1234",
        payment_response(),
        now=11,
    )
    assert bound["payment_id"] == "pay_abcdefgh1234"

    with pytest.raises(reference.MerchantStateError, match="payment_id_conflict"):
        store.bind_payment(
            "ORDER-1234",
            payment_response(payment_id="pay_different1234"),
            now=12,
        )
