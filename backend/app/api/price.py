from typing import Any

from fastapi import APIRouter

from ..services.price_service import get_price_info

router = APIRouter(tags=["price"])


@router.get("/price")
async def price() -> dict[str, Any]:
    return await get_price_info()
