import pytest

from app.services.merchant_store import (
    LEGACY_MERCHANT_ID,
    MerchantCredentialNotFoundError,
    MerchantStore,
)


def test_initialize_creates_reserved_legacy_merchant(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    merchant = store.get_merchant(LEGACY_MERCHANT_ID)

    assert merchant["merchant_id"] == LEGACY_MERCHANT_ID
    assert merchant["enabled"] is True


def test_scoped_credential_is_hashed_authenticatable_and_metadata_only(tmp_path):
    database = tmp_path / "payments.sqlite3"
    store = MerchantStore(str(database))
    merchant = store.create_merchant(
        merchant_id="mrc_test_one",
        display_name="Test merchant",
        now=100,
    )
    metadata, token = store.create_credential(
        merchant_id=merchant["merchant_id"],
        label="primary",
        now=101,
    )

    assert token.startswith("pepew_live.")
    assert token not in database.read_bytes().decode("utf-8", errors="ignore")

    authenticated = store.authenticate_credential(token)
    assert authenticated == {
        "merchant_id": merchant["merchant_id"],
        "credential_id": metadata["credential_id"],
    }

    listed = store.list_credentials(merchant["merchant_id"])
    assert listed == [
        {
            "credential_id": metadata["credential_id"],
            "merchant_id": merchant["merchant_id"],
            "label": "primary",
            "enabled": True,
            "created_at": 101,
            "disabled_at": None,
        }
    ]


def test_wrong_secret_and_unknown_credential_do_not_authenticate(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    merchant = store.create_merchant(
        merchant_id="mrc_test_two",
        display_name="Test merchant",
        now=100,
    )
    metadata, token = store.create_credential(
        merchant_id=merchant["merchant_id"],
        now=101,
    )

    prefix, credential_id, _ = token.split(".", 2)
    assert credential_id == metadata["credential_id"]
    assert store.authenticate_credential(f"{prefix}.{credential_id}.{'x' * 48}") is None
    assert store.authenticate_credential("pepew_live.mck_missing." + ("x" * 48)) is None
    assert store.authenticate_credential("not-a-scoped-token") is None


def test_multiple_credentials_support_rotation_then_disable(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    merchant = store.create_merchant(
        merchant_id="mrc_test_rotate",
        display_name="Rotate merchant",
        now=100,
    )
    first, first_token = store.create_credential(
        merchant_id=merchant["merchant_id"],
        label="old",
        now=101,
    )
    second, second_token = store.create_credential(
        merchant_id=merchant["merchant_id"],
        label="new",
        now=102,
    )

    assert store.authenticate_credential(first_token) is not None
    assert store.authenticate_credential(second_token) is not None

    store.disable_credential(first["credential_id"], now=103)

    assert store.authenticate_credential(first_token) is None
    assert store.authenticate_credential(second_token) == {
        "merchant_id": merchant["merchant_id"],
        "credential_id": second["credential_id"],
    }

    with pytest.raises(MerchantCredentialNotFoundError):
        store.disable_credential(first["credential_id"], now=104)


def test_credentials_are_independent_across_merchants(tmp_path):
    store = MerchantStore(str(tmp_path / "payments.sqlite3"))
    first = store.create_merchant(
        merchant_id="mrc_test_a",
        display_name="A",
        now=100,
    )
    second = store.create_merchant(
        merchant_id="mrc_test_b",
        display_name="B",
        now=100,
    )
    first_meta, first_token = store.create_credential(
        merchant_id=first["merchant_id"],
        now=101,
    )
    second_meta, second_token = store.create_credential(
        merchant_id=second["merchant_id"],
        now=101,
    )

    assert first_meta["credential_id"] != second_meta["credential_id"]
    assert store.authenticate_credential(first_token)["merchant_id"] == first["merchant_id"]
    assert store.authenticate_credential(second_token)["merchant_id"] == second["merchant_id"]
