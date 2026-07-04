from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field
from fastapi import APIRouter, Path, Query, status
from fastapi.responses import JSONResponse

from .errors import api_error_response
from ..config import get_settings
from ..services.address_service import (
    AddressUpstreamError,
    InvalidPepewAddressError,
    get_address_history,
    get_address_summary,
    get_address_utxos,
)
from ..services.tx_service import (
    broadcast_signed_raw_tx,
    get_transaction_details,
    InvalidRawTxError,
    InvalidTxidError,
    TxLookupError,
    TxNotFoundError,
    TxUpstreamError,
)
from ..services.payment_service import format_pepew_amount_from_sats

router = APIRouter(prefix="/wallet", tags=["wallet"])


class BroadcastRequest(BaseModel):
    raw_tx: str = Field(..., min_length=20, max_length=200_000)


async def _call_with_optional_fresh(
    func: Callable[..., Awaitable[dict[str, Any]]],
    *args: Any,
    fresh: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Call helpers with fresh= support while keeping older test fakes compatible."""
    try:
        return await func(*args, fresh=fresh, **kwargs)
    except TypeError as exc:
        if "fresh" not in str(exc):
            raise
        return await func(*args, **kwargs)


@router.get("/address/{address}")
async def wallet_address_lookup(
    address: str = Path(...),
    fresh: bool = Query(default=False),
) -> JSONResponse:
    try:
        summary = await _call_with_optional_fresh(get_address_summary, address, fresh=fresh)
        history_data = await _call_with_optional_fresh(get_address_history, address, limit=50, offset=0, fresh=fresh)

        confirmed_sats = summary["balance"]["confirmed"]
        unconfirmed_sats = summary["balance"]["unconfirmed"]

        settings = get_settings()
        confirmed_pepew = format_pepew_amount_from_sats(confirmed_sats, settings.pepew_decimals)
        unconfirmed_pepew = format_pepew_amount_from_sats(unconfirmed_sats, settings.pepew_decimals)

        mapped_history = []
        for item in history_data.get("history", []):
            mapped_history.append({
                "txid": item.get("tx_hash"),
                "height": item.get("height")
            })

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "address": summary["address"],
                "balance": {
                    "confirmed": confirmed_sats,
                    "unconfirmed": unconfirmed_sats,
                    "confirmed_pepew": confirmed_pepew,
                    "unconfirmed_pepew": unconfirmed_pepew,
                },
                "history": mapped_history,
                "source": "electrumx",
                "read_only": True,
                "cache": summary.get("cache", {}),
            }
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.get("/history/{address}")
async def wallet_address_history(
    address: str = Path(...),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    fresh: bool = Query(default=False),
) -> JSONResponse:
    try:
        history_data = await _call_with_optional_fresh(get_address_history, address, limit=limit, offset=offset, fresh=fresh)

        mapped_history = []
        for item in history_data.get("history", []):
            mapped_history.append({
                "txid": item.get("tx_hash"),
                "height": item.get("height")
            })

        mapped_mempool = []
        for item in history_data.get("mempool", []):
            mapped_mempool.append({
                "txid": item.get("tx_hash"),
                "height": item.get("height")
            })

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "address": history_data["address"],
                "history": mapped_history,
                "mempool": mapped_mempool,
                "source": "electrumx",
                "read_only": True,
                "cache": history_data.get("cache", {}),
            }
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.get("/utxo/{address}")
async def wallet_address_utxos(
    address: str = Path(...),
    fresh: bool = Query(default=False),
) -> JSONResponse:
    try:
        result = await _call_with_optional_fresh(get_address_utxos, address, fresh=fresh)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "address": result["address"],
                "utxos": result["utxos"],
                "utxo_count": result["utxo_count"],
                "total": result["total"],
                "source": "electrumx",
                "read_only": True,
                "cache": result.get("cache", {}),
            }
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.get("/tx/{txid}")
async def wallet_tx_lookup(
    txid: str = Path(...),
    raw: bool = Query(default=False),
) -> JSONResponse:
    try:
        result = await get_transaction_details(txid, verbose=not raw)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "txid": result["txid"],
                "data": result["data"],
                "source": "electrumx",
                "read_only": True,
                "raw": raw,
            }
        )
    except InvalidTxidError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except TxNotFoundError as exc:
        return api_error_response(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    except TxLookupError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.post("/broadcast")
async def wallet_broadcast_signed_raw_tx(payload: BroadcastRequest) -> JSONResponse:
    try:
        result = await broadcast_signed_raw_tx(payload.raw_tx)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "ok": True,
                "txid": result.get("txid"),
                "source": "electrumx",
                "signed_raw_tx_only": True,
            }
        )
    except InvalidRawTxError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except TxUpstreamError as exc:
        code = exc.code if exc.code == "broadcast_rejected" else "electrumx_error"
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, code)
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")
