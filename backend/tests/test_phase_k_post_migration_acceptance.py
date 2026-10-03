import importlib.util
from pathlib import Path

import pytest

from app.services.merchant_store import MerchantStore
from app.services.payment_store import PaymentStore


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts" / "phase_k_post_migration_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "phase_k_post_migration_acceptance",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = load_script()


def create_legacy_db(path: Path) -> PaymentStore:
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
    return store


def test_post_migration_database_acceptance_passes_for_legacy_only(tmp_path):
    path = tmp_path / "payments.sqlite3"
    create_legacy_db(path)

    acceptance.verify_database(path)
    assert acceptance.latest_payment_id(path) == "pay_legacy"


def test_post_migration_database_acceptance_rejects_scoped_rows(tmp_path):
    path = tmp_path / "payments.sqlite3"
    store = create_legacy_db(path)
    merchants = MerchantStore(str(path))
    merchant = merchants.create_merchant(
        merchant_id="mrc_too_early",
        display_name="Too early",
        now=1100,
    )
    store.create_payment(
        payment_id="pay_scoped",
        merchant_id=merchant["merchant_id"],
        address="P-scoped-post-migration",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1100,
        created_height=500,
        expires_at=2000,
    )

    with pytest.raises(
        acceptance.AcceptanceError,
        match="unexpected non-legacy production ownership",
    ):
        acceptance.verify_database(path)



def test_post_migration_database_acceptance_allows_scoped_rows_in_resume_mode(tmp_path):
    path = tmp_path / "payments.sqlite3"
    store = create_legacy_db(path)
    merchants = MerchantStore(str(path))
    merchant = merchants.create_merchant(
        merchant_id="mrc_resume_ok",
        display_name="Resume merchant",
        now=1100,
    )
    store.create_payment(
        payment_id="pay_resume",
        merchant_id=merchant["merchant_id"],
        address="PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
        scripthash="22" * 32,
        amount_sats=100,
        confirmations_required=3,
        created_at=1100,
        created_height=500,
        expires_at=2000,
    )

    acceptance.verify_database(
        path,
        require_legacy_only=False,
    )
