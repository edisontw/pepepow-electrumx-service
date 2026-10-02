from dataclasses import dataclass
import hmac

from ..config import get_settings
from .merchant_store import LEGACY_MERCHANT_ID, MerchantStore


class PaymentAuthError(RuntimeError):
    pass


class PaymentAuthUnconfiguredError(RuntimeError):
    pass


@dataclass(frozen=True)
class MerchantAuthContext:
    merchant_id: str
    credential_id: str
    source: str


def _scoped_auth_context(presented: str) -> MerchantAuthContext | None:
    settings = get_settings()
    if not settings.payment_scoped_merchant_auth_enabled:
        return None

    authenticated = MerchantStore(settings.payment_db_path).authenticate_credential(presented)
    if authenticated is None:
        return None
    return MerchantAuthContext(
        merchant_id=authenticated["merchant_id"],
        credential_id=authenticated["credential_id"],
        source="scoped_db",
    )


def require_payment_merchant_auth(
    authorization: str | None,
) -> MerchantAuthContext:
    """Require merchant Bearer auth and return the resolved merchant context."""
    settings = get_settings()
    configured = (settings.payment_create_api_key or "").strip()
    legacy_configured = len(configured) >= 32

    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        if not legacy_configured and not settings.payment_scoped_merchant_auth_enabled:
            raise PaymentAuthUnconfiguredError("Payment merchant API key is not configured.")
        raise PaymentAuthError("Bearer authorization is required.")

    presented = authorization[7:].strip()
    if not presented:
        raise PaymentAuthError("Bearer authorization is required.")

    if legacy_configured and hmac.compare_digest(presented, configured):
        return MerchantAuthContext(
            merchant_id=LEGACY_MERCHANT_ID,
            credential_id="legacy_env",
            source="legacy_env",
        )

    scoped = _scoped_auth_context(presented)
    if scoped is not None:
        return scoped

    if not legacy_configured and not settings.payment_scoped_merchant_auth_enabled:
        raise PaymentAuthUnconfiguredError("Payment merchant API key is not configured.")
    raise PaymentAuthError("Bearer authorization is required.")


def require_payment_create_auth(
    authorization: str | None,
) -> MerchantAuthContext:
    """Backward-compatible creation-specific auth helper."""
    return require_payment_merchant_auth(authorization)
