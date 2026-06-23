from fastapi import APIRouter
from pydantic import BaseModel

from ..config import get_settings


class HealthResponse(BaseModel):
    ok: bool
    app: str
    env: str
    version: str


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        ok=True,
        app=settings.app_name,
        env=settings.app_env,
        version=settings.version,
    )
