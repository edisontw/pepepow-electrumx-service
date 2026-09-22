import pytest

from app.config import get_settings
from app.services import payment_auth


def test_payment_create_auth_accepts_matching_bearer(monkeypatch):
    secret = "a" * 48
    settings = get_settings().model_copy(update={"payment_create_api_key": secret})
    monkeypatch.setattr(payment_auth, "get_settings", lambda: settings)

    payment_auth.require_payment_create_auth(f"Bearer {secret}")


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
