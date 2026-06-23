from fastapi import APIRouter, HTTPException, Path, status

router = APIRouter(tags=["transaction"])


@router.get("/tx/{txid}")
async def tx_placeholder(txid: str = Path(..., min_length=64, max_length=64)) -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Transaction lookup is planned for Phase 2.",
    )
