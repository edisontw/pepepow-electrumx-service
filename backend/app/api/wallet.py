from fastapi import APIRouter, Path, Query, status
from fastapi.responses import JSONResponse

from .errors import api_error_response
from ..config import get_settings
from ..services.address_service import (
    AddressUpstreamError,
    InvalidPepewAddressError,
    get_address_history,
    get_address_summary,
)
from ..services.tx_service import (
    get_transaction_details,
    InvalidTxidError,
    TxLookupError,
    TxNotFoundError,
)
from ..services.payment_service import format_pepew_amount_from_sats

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/address/{address}")
async def wallet_address_lookup(address: str = Path(...)) -> JSONResponse:
    try:
        summary = await get_address_summary(address)
        # Fetch history list (cached, fast)
        history_data = await get_address_history(address, limit=50, offset=0)

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
) -> JSONResponse:
    try:
        history_data = await get_address_history(address, limit=limit, offset=offset)

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
            }
        )
    except InvalidPepewAddressError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except AddressUpstreamError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")


@router.get("/tx/{txid}")
async def wallet_tx_lookup(txid: str = Path(...)) -> JSONResponse:
    try:
        result = await get_transaction_details(txid)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "txid": result["txid"],
                "data": result["data"],
                "source": "electrumx",
                "read_only": True,
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
