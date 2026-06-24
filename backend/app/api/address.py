from fastapi import APIRouter, Path, Query, status

from .errors import api_error_response
from ..services.address_service import (
    AddressUpstreamError,
    InvalidPepewAddressError,
    get_address_history,
    get_address_summary,
)

router = APIRouter(tags=["address"])


@router.get("/address/{address}")
async def address_lookup(address: str = Path(...)):
    try:
        return await get_address_summary(address)
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError as exc:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")


@router.get("/address/{address}/history")
async def address_history(
    address: str = Path(...),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await get_address_history(address, limit=limit, offset=offset)
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError as exc:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
