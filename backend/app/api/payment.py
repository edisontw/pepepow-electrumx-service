from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(tags=["payment"])


@router.get("/payment/check")
async def payment_check_placeholder(
    address: str = Query(..., min_length=1, max_length=128),
    amount: str = Query(..., min_length=1, max_length=64),
) -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Payment checking is planned for Phase 3.",
    )
