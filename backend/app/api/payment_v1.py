from fastapi import APIRouter, Header, Path, Query, status
from pydantic import BaseModel, Field

from .errors import api_error_response
from ..services.address_service import InvalidPepewAddressError
from ..services.payment_auth import (
    PaymentAuthError,
    PaymentAuthUnconfiguredError,
    require_payment_create_auth,
    require_payment_merchant_auth,
)
from ..services.payment_gateway_service import (
    PaymentGatewayDisabledError,
    PaymentIdempotencyConflictError,
    PaymentNotFoundError,
    PaymentStoreError,
    PaymentTipUnavailableError,
    create_persisted_payment,
    get_persisted_payment,
    list_persisted_payments,
)
from ..services.payment_service import InvalidPaymentAmountError, InvalidPaymentParameterError

router = APIRouter(tags=["payments-v1"])


class CreatePaymentRequest(BaseModel):
    address: str = Field(min_length=1, max_length=128)
    amount: str = Field(min_length=1, max_length=64)
    confirmations: int | None = Field(default=None, ge=0, le=100)
    expires_in: int | None = Field(default=None, ge=60, le=86400)
    label: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=256)


@router.get("/v1/payments")
async def list_payments(
    authorization: str | None = Header(default=None),
    payment_status: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    before_created_at: int | None = Query(default=None, ge=0),
    before_payment_id: str | None = Query(default=None, min_length=8, max_length=96),
):
    try:
        require_payment_merchant_auth(authorization)
        return await list_persisted_payments(
            status=payment_status,
            limit=limit,
            before_created_at=before_created_at,
            before_payment_id=before_payment_id,
        )
    except PaymentAuthUnconfiguredError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "payment_auth_unconfigured",
        )
    except PaymentAuthError:
        return api_error_response(
            status.HTTP_401_UNAUTHORIZED,
            "payment_auth_required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidPaymentParameterError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except PaymentGatewayDisabledError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_api_disabled")
    except PaymentStoreError:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "payment_store_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.post("/v1/payments", status_code=status.HTTP_201_CREATED)
async def create_payment(
    request: CreatePaymentRequest,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        require_payment_create_auth(authorization)
        return await create_persisted_payment(
            address=request.address,
            amount=request.amount,
            confirmations=request.confirmations,
            expires_in=request.expires_in,
            label=request.label,
            message=request.message,
            idempotency_key=idempotency_key,
        )
    except PaymentAuthUnconfiguredError:
        return api_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "payment_auth_unconfigured",
        )
    except PaymentAuthError:
        return api_error_response(
            status.HTTP_401_UNAUTHORIZED,
            "payment_auth_required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except InvalidPaymentAmountError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except InvalidPaymentParameterError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except PaymentGatewayDisabledError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_api_disabled")
    except PaymentTipUnavailableError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_tip_unavailable")
    except PaymentIdempotencyConflictError:
        return api_error_response(
            status.HTTP_409_CONFLICT,
            "payment_idempotency_conflict",
            "Idempotency-Key was already used with different payment parameters.",
        )
    except PaymentStoreError:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "payment_store_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.get("/v1/payments/{payment_id}")
async def get_payment(payment_id: str = Path(..., min_length=8, max_length=96)):
    try:
        return await get_persisted_payment(payment_id)
    except PaymentNotFoundError:
        return api_error_response(status.HTTP_404_NOT_FOUND, "payment_not_found")
    except PaymentGatewayDisabledError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "payment_api_disabled")
    except PaymentStoreError:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "payment_store_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")
