import pytest

from app.config import get_settings
from app.services import payment_auth
from app.services.merchant_store import LEGACY_MERCHANT_ID, MerchantStore


def test_payment_create_auth_accepts_matching_bearer(monkeypatch):
    secret = "a" * 48
    settings = get_settings().model_copy(update={"payment_create_api_key": secret})
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    context = payment_auth.require_payment_create_auth(f"Bearer {secret}")
    assert context.merchant_id == LEGACY_MERCHANT_ID
    assert context.credential_id == "legacy_env"
    assert context.source == "legacy_env"


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic abc",
        "Bearer wrong",
    ],
)
def test_payment_create_auth_rejects_missing_or_invalid_bearer(monkeypatch, authorization):
    secret = "b" * 48
    settings = get_settings().model_copy(update={"payment_create_api_key": secret})
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    with pytest.raises(payment_auth.PaymentAuthError):
        payment_auth.require_payment_create_auth(authorization)


@pytest.mark.parametrize("secret", [None, "", "short"])
def test_payment_create_auth_fails_closed_when_key_is_unconfigured(monkeypatch, secret):
    settings = get_settings().model_copy(update={"payment_create_api_key": secret})
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    with pytest.raises(payment_auth.PaymentAuthUnconfiguredError):
        payment_auth.require_payment_create_auth("Bearer anything")



def test_scoped_auth_is_disabled_by_default_even_when_credential_exists(tmp_path, monkeypatch):
    database = tmp_path / "payments.sqlite3"
    store = MerchantStore(str(database))
    merchant = store.create_merchant(
        merchant_id="mrc_auth_disabled",
        display_name="Scoped merchant",
        now=100,
    )
    _, token = store.create_credential(merchant_id=merchant["merchant_id"], now=101)

    settings = get_settings().model_copy(
        update={
            "payment_create_api_key": "a" * 48,
            "payment_db_path": str(database),
            "payment_scoped_merchant_auth_enabled": False,
        }
    )
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    with pytest.raises(payment_auth.PaymentAuthError):
        payment_auth.require_payment_merchant_auth(f"Bearer {token}")


def test_scoped_auth_returns_merchant_context_when_explicitly_enabled(tmp_path, monkeypatch):
    database = tmp_path / "payments.sqlite3"
    store = MerchantStore(str(database))
    merchant = store.create_merchant(
        merchant_id="mrc_auth_enabled",
        display_name="Scoped merchant",
        now=100,
    )
    metadata, token = store.create_credential(
        merchant_id=merchant["merchant_id"],
        now=101,
    )

    settings = get_settings().model_copy(
        update={
            "payment_create_api_key": "a" * 48,
            "payment_db_path": str(database),
            "payment_scoped_merchant_auth_enabled": True,
        }
    )
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    context = payment_auth.require_payment_merchant_auth(f"Bearer {token}")
    assert context.merchant_id == merchant["merchant_id"]
    assert context.credential_id == metadata["credential_id"]
    assert context.source == "scoped_db"
