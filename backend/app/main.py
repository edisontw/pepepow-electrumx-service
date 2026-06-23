from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from .api.health import router as health_router
from .api.status import router as status_router
from .api.address import router as address_router
from .api.tx import router as tx_router
from .api.payment import router as payment_router
from .config import get_settings
from .logging_config import configure_logging
from .services.status_service import get_status

settings = get_settings()
configure_logging(settings)

app = FastAPI(
    title="PEPEW Light",
    version=settings.version,
    description="Read-only PEPEPOW ElectrumX API Gateway.",
)

templates = Jinja2Templates(directory="app/templates")

app.include_router(health_router, prefix="/api")
app.include_router(status_router, prefix="/api")
app.include_router(address_router, prefix="/api")
app.include_router(tx_router, prefix="/api")
app.include_router(payment_router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
        },
    )


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request) -> HTMLResponse:
    status = await get_status()
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
            "status": status,
        },
    )


@app.head("/status")
async def status_head() -> Response:
    return Response(status_code=200)
