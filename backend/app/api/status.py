from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["status"])


@router.get("/status")
async def status_placeholder() -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="ElectrumX status is planned for Phase 1.",
    )
