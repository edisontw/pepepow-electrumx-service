import importlib.util
from pathlib import Path

import pytest

from app.services.merchant_store import MerchantStore
from app.services.payment_store import PaymentStore


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "phase_k_production_migration.py"
    spec = importlib.util.spec_from_file_location(
        "phase_k_production_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = load_script()


def create_authoritative_db(path: Path) -> PaymentStore:
    store = PaymentStore(str(path))
    store.create_webhook_endpoint(
        endpoint_id="wh_legacy",
        url="https://merchant.example/hook",
        event_types=None,
        created_at=900,
    )
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
        merchant_reference="ORDER-LEGACY",
        idempotency_key="retry-legacy",
        request_hash="same-request",
    )
    return store


def test_migrate_database_preserves_authoritative_data_and_profile(tmp_path):
    path = tmp_path / "payments.sqlite3"
    create_authoritative_db(path)

    before = migration.authority_snapshot(path)
    result = migration.migrate_database(path)

    assert result["before"] == before
    assert result["summary"]["schema_profile"] == "phase_k_merchant_v1"
    assert set(result["summary"]["merchant_ownership_checks"].values()) == {0}
    assert migration.authority_snapshot(path) == before


def test_initial_migration_helper_rejects_existing_nonlegacy_rows(tmp_path):
    path = tmp_path / "payments.sqlite3"
    store = create_authoritative_db(path)
    merchants = MerchantStore(str(path))
    merchant = merchants.create_merchant(
        merchant_id="mrc_existing_scoped",
        display_name="Existing scoped merchant",
        now=1100,
    )
    store.create_payment(
        payment_id="pay_scoped",
        merchant_id=merchant["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1100,
        created_height=500,
        expires_at=2000,
    )

    with pytest.raises(
        migration.MigrationError,
        match="unexpected non-legacy ownership",
    ):
        migration.migrate_database(path, require_legacy_only=True)

    result = migration.migrate_database(
        path,
        require_legacy_only=False,
    )
    assert result["summary"]["schema_profile"] == "phase_k_merchant_v1"
