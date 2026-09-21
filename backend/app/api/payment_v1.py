from fastapi import APIRouter, Path, status
from pydantic import BaseModel, Field

from .errors import api_error_response
from ..services.address_service import InvalidPepewAddressError
from ..services.payment_gateway_service import (
    PaymentGatewayDisabledError,
    PaymentNotFoundError,
    PaymentStoreError,
    PaymentTipUnavailableError,
    create_persisted_payment,
    get_persisted_payment,
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


@router.post("/v1/payments", status_code=status.HTTP_201_CREATED)
async def create_payment(request: CreatePaymentRequest):
    try:
        return await create_persisted_payment(
            address=request.address,
            amount=request.amount,
            confirmations=request.confirmations,
            expires_in=request.expires_in,
            label=request.label,
            message=request.message,
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
