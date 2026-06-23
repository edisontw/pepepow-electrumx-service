from typing import Any

from fastapi import APIRouter

from ..services.status_service import get_status

router = APIRouter(tags=["status"])


@router.get("/status")
async def status() -> dict[str, Any]:
    return await get_status()
