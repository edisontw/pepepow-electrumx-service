from typing import Any

from fastapi import APIRouter

from ..services.network_service import get_network_info

router = APIRouter(tags=["network"])


@router.get("/network")
async def network() -> dict[str, Any]:
    return await get_network_info()
