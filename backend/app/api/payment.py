from fastapi import APIRouter, Query, status

from .errors import api_error_response
from ..services.address_service import InvalidPepewAddressError
from ..services.payment_service import (
    InvalidPaymentAmountError,
    InvalidPaymentParameterError,
    PaymentUpstreamError,
    check_payment,
)

router = APIRouter(tags=["payment"])


@router.get("/payment/check")
async def payment_check(
    address: str = Query(..., min_length=1, max_length=128),
    amount: str = Query(..., min_length=1, max_length=64),
    confirmations: int | None = Query(default=None, ge=0),
    expires_at: str | None = Query(default=None, max_length=64),
    expires_in: int | None = Query(default=None, ge=1),
):
    try:
        return await check_payment(
            address=address,
            amount=amount,
            confirmations=confirmations,
            expires_at=expires_at,
            expires_in=expires_in,
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except InvalidPaymentAmountError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except InvalidPaymentParameterError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except PaymentUpstreamError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")
