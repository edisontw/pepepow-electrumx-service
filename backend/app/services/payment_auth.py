import hmac

from ..config import get_settings


class PaymentAuthError(RuntimeError):
    pass


class PaymentAuthUnconfiguredError(RuntimeError):
    pass


def require_payment_create_auth(authorization: str | None) -> None:
    """Require a server-side Bearer API key for payment creation."""
    settings = get_settings()
    configured = (settings.payment_create_api_key or "").strip()

    # A short or missing key is treated as deployment misconfiguration rather
    # than silently weakening the create endpoint.
    if len(configured) < 32:
        raise PaymentAuthUnconfiguredError("Payment creation API key is not configured.")

    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise PaymentAuthError("Bearer authorization is required.")

    presented = authorization[7:].strip()
    if not presented or not hmac.compare_digest(presented, configured):
        raise PaymentAuthError("Bearer authorization is required.")
