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
