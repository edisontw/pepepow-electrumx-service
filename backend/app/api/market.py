from typing import Any

from fastapi import APIRouter

from ..services.market_service import get_market_info

router = APIRouter(tags=["market"])


@router.get("/market")
async def market() -> dict[str, Any]:
    return await get_market_info()
