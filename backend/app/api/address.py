from fastapi import APIRouter, HTTPException, Path, status

from ..services.address_service import (
    AddressUpstreamError,
    InvalidPepewAddressError,
    get_address_history,
    get_address_summary,
)

router = APIRouter(tags=["address"])


def _error_detail(error: str, extra: dict | None = None) -> dict:
    detail = {"ok": False, "error": error}
    if extra:
        detail.update(extra)
    return detail


@router.get("/address/{address}")
async def address_lookup(address: str = Path(..., min_length=1, max_length=128)) -> dict:
    try:
        return await get_address_summary(address)
    except InvalidPepewAddressError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail(exc.code),
        ) from exc
    except AddressUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error_detail(exc.code, exc.detail),
        ) from exc


@router.get("/address/{address}/history")
async def address_history(address: str = Path(..., min_length=1, max_length=128)) -> dict:
    try:
        return await get_address_history(address)
    except InvalidPepewAddressError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error_detail(exc.code),
        ) from exc
    except AddressUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_error_detail(exc.code, exc.detail),
        ) from exc
