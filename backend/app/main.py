from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api.health import router as health_router
from .api.status import router as status_router
from .api.address import router as address_router
from .api.tx import router as tx_router
from .api.payment import router as payment_router
from .api.wallet import router as wallet_router
from .config import get_settings
from .logging_config import configure_logging
from .services.status_service import get_status

settings = get_settings()
configure_logging(settings)

app = FastAPI(
    title="PEPEW Light",
    version=settings.version,
    description="PEPEPOW ElectrumX API Gateway for read queries and signed raw transaction broadcast.",
)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.include_router(health_router, prefix="/api")
app.include_router(status_router, prefix="/api")
app.include_router(address_router, prefix="/api")
app.include_router(tx_router, prefix="/api")
app.include_router(payment_router, prefix="/api")
app.include_router(wallet_router, prefix="/api")



@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    try:
        status = await get_status()
    except Exception:
        status = {"ok": False, "error": "status_error"}
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
            "status": status,
        },
    )


@app.head("/")
async def index_head() -> Response:
    return Response(status_code=200)


@app.get("/address", response_class=HTMLResponse)
async def address_page(request: Request, q: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "address.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
            "query_address": (q or "").strip(),
        },
    )


@app.head("/address")
async def address_head() -> Response:
    return Response(status_code=200)


@app.get("/pay", response_class=HTMLResponse)
async def pay_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "payment.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
            "min_confirmations": settings.pepew_min_confirmations,
            "pepew_decimals": settings.pepew_decimals,
        },
    )


@app.head("/pay")
async def pay_head() -> Response:
    return Response(status_code=200)


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


@app.get("/tx", response_class=HTMLResponse)
async def tx_page(request: Request, txid: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "tx.html",
        {
            "app_name": settings.app_name,
            "version": settings.version,
            "query_txid": (txid or "").strip(),
        },
    )


@app.head("/tx")
async def tx_head() -> Response:
    return Response(status_code=200)
