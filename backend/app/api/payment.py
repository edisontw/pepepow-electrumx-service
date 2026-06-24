from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from .errors import api_error_response

router = APIRouter(tags=["payment"])


@router.get("/payment/check")
async def payment_check_placeholder(
    address: str = Query(..., min_length=1, max_length=128),
    amount: str = Query(..., min_length=1, max_length=64),
) -> JSONResponse:
    return api_error_response(status.HTTP_501_NOT_IMPLEMENTED, "payment_check_unavailable")
