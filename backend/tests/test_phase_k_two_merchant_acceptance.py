import io
import urllib.error

import importlib.util
from pathlib import Path
import sqlite3

import pytest

from app.services.merchant_store import MerchantStore
from app.services.payment_store import PaymentStore


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "phase_k_two_merchant_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "phase_k_two_merchant_acceptance",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = load_script()


def test_load_secret_requires_restrictive_permissions(tmp_path):
    secret = tmp_path / "merchant.secret"
    secret.write_text("pepew_live.mck_test." + ("x" * 48) + "\n", encoding="utf-8")
    secret.chmod(0o600)

    token = acceptance.load_secret(secret)
    assert token.startswith("pepew_live.")

    secret.chmod(0o644)
    with pytest.raises(
        acceptance.AcceptanceError,
        match="group/world accessible",
    ):
        acceptance.load_secret(secret)


def create_two_merchant_db(path: Path):
    merchants = MerchantStore(str(path))
    scoped = merchants.create_merchant(
        merchant_id="mrc_accept_scoped",
        display_name="Scoped acceptance",
        now=100,
    )
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)
    store.create_payment(
        payment_id="pay_legacy",
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1000,
        created_height=500,
        expires_at=1900,
    )
    store.create_payment(
        payment_id="pay_scoped",
        merchant_id=scoped["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1001,
        created_height=500,
        expires_at=1901,
    )
    return scoped


def test_two_merchant_acceptance_checks_payment_event_owners(tmp_path):
    path = tmp_path / "payments.sqlite3"
    scoped = create_two_merchant_db(path)

    acceptance.verify_payment_event_owner(
        path,
        payment_id="pay_legacy",
        merchant_id="mrc_legacy_v1",
    )
    acceptance.verify_payment_event_owner(
        path,
        payment_id="pay_scoped",
        merchant_id=scoped["merchant_id"],
    )
    acceptance.verify_global_ownership(path)


def test_two_merchant_acceptance_detects_cross_owner_delivery(tmp_path):
    path = tmp_path / "payments.sqlite3"
    scoped = create_two_merchant_db(path)
    store = PaymentStore(str(path))
    store.create_webhook_endpoint(
        endpoint_id="wh_scoped",
        merchant_id=scoped["merchant_id"],
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )

    # Create a deliberate ownership violation for acceptance detection only.
    with sqlite3.connect(path) as connection:
        scoped_event_id = connection.execute(
            """
            SELECT event_id
            FROM events
            WHERE payment_id = 'pay_scoped'
              AND event_type = 'payment.created'
            """
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO webhook_deliveries (
                delivery_id, event_id, endpoint_id, status,
                attempt_count, next_attempt_at, created_at, updated_at
            ) VALUES (
                'dlv_bad', ?, 'wh_scoped', 'pending',
                0, 1000, 1000, 1000
            )
            """,
            (scoped_event_id,),
        )
        # Flip only the event owner, keeping the payment owner scoped.
        connection.execute(
            """
            UPDATE events
            SET merchant_id = 'mrc_legacy_v1'
            WHERE event_id = ?
            """,
            (scoped_event_id,),
        )

    with pytest.raises(
        acceptance.AcceptanceError,
        match="merchant ownership consistency check failed",
    ):
        acceptance.verify_global_ownership(path)



def test_payment_created_recipient_count_matches_runtime_filtering(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_accept_a",
        display_name="Acceptance A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_accept_b",
        display_name="Acceptance B",
        now=100,
    )
    other = merchants.create_merchant(
        merchant_id="mrc_accept_other",
        display_name="Other",
        now=100,
    )
    store = PaymentStore(str(path))

    store.create_webhook_endpoint(
        endpoint_id="wh_a_all",
        merchant_id=merchant_a["merchant_id"],
        url="https://a.example/all",
        event_types=None,
        created_at=100,
    )
    store.create_webhook_endpoint(
        endpoint_id="wh_a_created",
        merchant_id=merchant_a["merchant_id"],
        url="https://a.example/created",
        event_types=("payment.created",),
        created_at=101,
    )
    store.create_webhook_endpoint(
        endpoint_id="wh_b_confirmed",
        merchant_id=merchant_b["merchant_id"],
        url="https://b.example/confirmed",
        event_types=("payment.paid_confirmed",),
        created_at=102,
    )
    store.create_webhook_endpoint(
        endpoint_id="wh_other_all",
        merchant_id=other["merchant_id"],
        url="https://other.example/all",
        event_types=None,
        created_at=103,
    )
    store.disable_webhook_endpoint(
        "wh_a_created",
        merchant_id=merchant_a["merchant_id"],
        updated_at=104,
    )

    assert acceptance.payment_created_recipient_count(
        path,
        merchant_ids={
            merchant_a["merchant_id"],
            merchant_b["merchant_id"],
        },
    ) == 1

    assert acceptance.payment_created_recipient_count(
        path,
        merchant_ids={merchant_b["merchant_id"]},
    ) == 0

    assert acceptance.payment_created_recipient_count(
        path,
        merchant_ids={other["merchant_id"]},
    ) == 1



def test_heavy_request_controller_retries_429_and_succeeds(monkeypatch):
    calls = []

    def fake_json_request(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) < 3:
            return 429, {}, {}
        return 200, {"ok": True}, {}

    monkeypatch.setattr(acceptance, "json_request", fake_json_request)
    controller = acceptance.HeavyRequestController(
        min_interval=0,
        max_rate_limit_retries=3,
        backoff_seconds=0,
    )

    status, payload, _headers = controller.request(
        "GET",
        "https://example.test/api/v1/payments",
    )

    assert status == 200
    assert payload == {"ok": True}
    assert len(calls) == 3


def test_heavy_request_controller_returns_final_429(monkeypatch):
    calls = []

    def fake_json_request(*args, **kwargs):
        calls.append((args, kwargs))
        return 429, {}, {}

    monkeypatch.setattr(acceptance, "json_request", fake_json_request)
    controller = acceptance.HeavyRequestController(
        min_interval=0,
        max_rate_limit_retries=2,
        backoff_seconds=0,
    )

    status, payload, _headers = controller.request(
        "GET",
        "https://example.test/api/v1/payments",
    )

    assert status == 429
    assert payload == {}
    assert len(calls) == 3


def test_retry_after_parser_is_case_insensitive():
    assert acceptance.HeavyRequestController._retry_after_seconds(
        {"Retry-After": "2"}
    ) == 2.0
    assert acceptance.HeavyRequestController._retry_after_seconds(
        {"retry-after": "0.5"}
    ) == 0.5
    assert acceptance.HeavyRequestController._retry_after_seconds(
        {"Retry-After": "invalid"}
    ) is None



def test_find_existing_acceptance_pair_reuses_latest_common_pair(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_resume_a",
        display_name="Resume A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_resume_b",
        display_name="Resume B",
        now=100,
    )
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    common = {
        "address": "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        "amount_sats": 1,
        "confirmations_required": 1,
        "created_height": 500,
        "expires_at": 1900,
        "merchant_reference": "phase-k-acceptance/shared",
        "idempotency_key": "phase-k-acceptance:shared",
        "request_hash": "same-request",
    }

    store.create_payment(
        payment_id="pay_resume_a",
        merchant_id=merchant_a["merchant_id"],
        scripthash="11" * 32,
        created_at=1000,
        **common,
    )
    store.create_payment(
        payment_id="pay_resume_b",
        merchant_id=merchant_b["merchant_id"],
        scripthash="22" * 32,
        created_at=1001,
        **common,
    )

    pair = acceptance.find_existing_acceptance_pair(
        path,
        merchant_id_a=merchant_a["merchant_id"],
        merchant_id_b=merchant_b["merchant_id"],
    )

    assert pair == (
        "pay_resume_a",
        "pay_resume_b",
        "phase-k-acceptance/shared",
        "phase-k-acceptance:shared",
    )


def test_find_existing_acceptance_pair_requires_same_reference_and_key(tmp_path):
    path = tmp_path / "payments.sqlite3"
    merchants = MerchantStore(str(path))
    merchant_a = merchants.create_merchant(
        merchant_id="mrc_resume_miss_a",
        display_name="Resume A",
        now=100,
    )
    merchant_b = merchants.create_merchant(
        merchant_id="mrc_resume_miss_b",
        display_name="Resume B",
        now=100,
    )
    store = PaymentStore(str(path))
    store.set_chain_tip(500, tip_hash="tip", updated_at=1000)

    store.create_payment(
        payment_id="pay_resume_miss_a",
        merchant_id=merchant_a["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="11" * 32,
        amount_sats=1,
        confirmations_required=1,
        created_at=1000,
        created_height=500,
        expires_at=1900,
        merchant_reference="phase-k-acceptance/a",
        idempotency_key="phase-k-acceptance:a",
        request_hash="a",
    )
    store.create_payment(
        payment_id="pay_resume_miss_b",
        merchant_id=merchant_b["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="22" * 32,
        amount_sats=1,
        confirmations_required=1,
        created_at=1001,
        created_height=500,
        expires_at=1901,
        merchant_reference="phase-k-acceptance/b",
        idempotency_key="phase-k-acceptance:b",
        request_hash="b",
    )

    with pytest.raises(
        acceptance.AcceptanceError,
        match="no completed two-merchant acceptance payment pair",
    ):
        acceptance.find_existing_acceptance_pair(
            path,
            merchant_id_a=merchant_a["merchant_id"],
            merchant_id_b=merchant_b["merchant_id"],
        )



def test_non_json_nginx_429_is_retryable_response(monkeypatch):
    def fake_urlopen(_request, timeout):
        raise urllib.error.HTTPError(
            "https://example.test/api/v1/payments",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"<html>rate limited</html>"),
        )

    monkeypatch.setattr(acceptance.urllib.request, "urlopen", fake_urlopen)

    status, payload, headers = acceptance.json_request(
        "GET",
        "https://example.test/api/v1/payments",
    )

    assert status == 429
    assert payload == {}
    assert headers == {}
