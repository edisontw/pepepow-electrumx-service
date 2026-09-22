from fastapi import APIRouter, Header, Path, Query, status
from pydantic import BaseModel, Field

from .errors import api_error_response
from ..services.payment_auth import (
    PaymentAuthError,
    PaymentAuthUnconfiguredError,
    require_payment_create_auth,
)
from ..services.payment_store import PaymentNotFoundError
from ..services.webhook_security import WebhookResolutionError, WebhookUrlError
from ..services.webhook_service import (
    InvalidWebhookEventError,
    WebhookDisabledError,
    create_webhook_endpoint,
    disable_webhook_endpoint,
    list_webhook_deliveries,
    list_webhook_endpoints,
)
from ..services.webhook_signing import WebhookSigningError

router = APIRouter(tags=["webhooks-v1"])


class CreateWebhookEndpointRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    event_types: list[str] | None = Field(default=None, max_length=16)


def _auth_error(exc: Exception):
    if isinstance(exc, PaymentAuthUnconfiguredError):
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "payment_auth_unconfigured",
        )
    return api_error_response(
        status.HTTP_401_UNAUTHORIZED,
        "payment_auth_required",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/v1/webhook-endpoints", status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    request: CreateWebhookEndpointRequest,
    authorization: str | None = Header(default=None),
):
    try:
        require_payment_create_auth(authorization)
        return await create_webhook_endpoint(
            url=request.url,
            event_types=request.event_types,
        )
    except (PaymentAuthError, PaymentAuthUnconfiguredError) as exc:
        return _auth_error(exc)
    except WebhookDisabledError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook_disabled",
        )
    except WebhookSigningError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook_signing_unconfigured",
        )
    except (WebhookUrlError, InvalidWebhookEventError) as exc:
        code = exc.code if isinstance(exc, WebhookUrlError) else "invalid_webhook_event"
        return api_error_response(status.HTTP_400_BAD_REQUEST, code)
    except WebhookResolutionError:
        return api_error_response(
            status.HTTP_400_BAD_REQUEST,
            "webhook_dns_error",
        )
    except Exception:
        return api_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
        )


@router.get("/v1/webhook-endpoints")
async def list_endpoints(
    authorization: str | None = Header(default=None),
):
    try:
        require_payment_create_auth(authorization)
        return {
            "ok": True,
            "endpoints": await list_webhook_endpoints(),
        }
    except (PaymentAuthError, PaymentAuthUnconfiguredError) as exc:
        return _auth_error(exc)
    except WebhookDisabledError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook_disabled",
        )
    except Exception:
        return api_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
        )


@router.delete("/v1/webhook-endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: str = Path(..., min_length=8, max_length=96),
    authorization: str | None = Header(default=None),
):
    try:
        require_payment_create_auth(authorization)
        await disable_webhook_endpoint(endpoint_id)
        return {"ok": True, "endpoint_id": endpoint_id, "enabled": False}
    except (PaymentAuthError, PaymentAuthUnconfiguredError) as exc:
        return _auth_error(exc)
    except WebhookDisabledError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook_disabled",
        )
    except PaymentNotFoundError:
        return api_error_response(
            status.HTTP_404_NOT_FOUND,
            "webhook_endpoint_not_found",
        )
    except Exception:
        return api_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
        )


@router.get("/v1/webhook-deliveries")
async def list_deliveries(
    authorization: str | None = Header(default=None),
    endpoint_id: str | None = Query(default=None, min_length=8, max_length=96),
    limit: int = Query(default=100, ge=1, le=500),
):
    try:
        require_payment_create_auth(authorization)
        return {
            "ok": True,
            "deliveries": await list_webhook_deliveries(
                endpoint_id=endpoint_id,
                limit=limit,
            ),
        }
    except (PaymentAuthError, PaymentAuthUnconfiguredError) as exc:
        return _auth_error(exc)
    except WebhookDisabledError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "webhook_disabled",
        )
    except Exception:
        return api_error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
        )
