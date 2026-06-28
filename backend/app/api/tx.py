from fastapi import APIRouter, Path, status
from fastapi.responses import JSONResponse

from .errors import api_error_response
from ..services.tx_service import (
    get_transaction_details,
    InvalidTxidError,
    TxNotFoundError,
    TxLookupError,
)

router = APIRouter(tags=["transaction"])


@router.get("/tx/{txid}")
async def tx_lookup(txid: str = Path(...)) -> JSONResponse:
    try:
        result = await get_transaction_details(txid)
        return JSONResponse(status_code=status.HTTP_200_OK, content=result)
    except InvalidTxidError as exc:
        return api_error_response(status.HTTP_400_BAD_REQUEST, exc.code, exc.message)
    except TxNotFoundError as exc:
        return api_error_response(status.HTTP_404_NOT_FOUND, exc.code, exc.message)
    except TxLookupError:
        return api_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "electrumx_error")
    except Exception:
        return api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error")

