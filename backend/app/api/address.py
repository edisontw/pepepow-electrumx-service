from fastapi import APIRouter, HTTPException, Path, status

router = APIRouter(tags=["address"])


@router.get("/address/{address}")
async def address_placeholder(address: str = Path(..., min_length=1, max_length=128)) -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Address lookup is planned for Phase 2.",
    )


@router.get("/address/{address}/history")
async def address_history_placeholder(address: str = Path(..., min_length=1, max_length=128)) -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Address history is planned for Phase 2.",
    )
