from fastapi import APIRouter, Path, status
from fastapi.responses import JSONResponse

from .errors import api_error_response

router = APIRouter(tags=["transaction"])


@router.get("/tx/{txid}")
async def tx_placeholder(txid: str = Path(..., min_length=64, max_length=64)) -> JSONResponse:
    return api_error_response(status.HTTP_501_NOT_IMPLEMENTED, "transaction_lookup_unavailable")
