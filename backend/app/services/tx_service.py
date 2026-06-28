from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError, ElectrumXMethodError
from ..electrumx.methods import transaction_get, server_version
from ..pepepow.tx import normalize_txid


class TxLookupError(Exception):
    code = "tx_lookup_error"


class InvalidTxidError(TxLookupError):
    code = "invalid_txid"

    def __init__(self, code: str = "invalid_txid", message: str = "Transaction id must be 64 hex characters.") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class TxNotFoundError(TxLookupError):
    code = "tx_not_found"

    def __init__(self, code: str = "tx_not_found", message: str = "Transaction not found.") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class TxUpstreamError(TxLookupError):
    code = "electrumx_error"

    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


async def get_transaction_details(txid: str) -> dict[str, Any]:
    try:
        norm_txid = normalize_txid(txid)
    except ValueError as exc:
        raise InvalidTxidError("invalid_txid", "Transaction id must be 64 hex characters.") from exc

    settings = get_settings()
    client = ElectrumXClient(settings)
    try:
        # Some servers require version identification
        await server_version(client)
        # Query with verbose=True
        tx_data = await transaction_get(client, norm_txid, verbose=True)
    except ElectrumXMethodError as exc:
        err_msg = str(exc).lower()
        # Usually daemon error: No such transaction or similar message
        if "not found" in err_msg or "no such transaction" in err_msg or "daemon error" in err_msg:
            raise TxNotFoundError("tx_not_found", "Transaction not found.") from exc
        raise TxUpstreamError("electrumx_error", {"message": str(exc)}) from exc
    except ElectrumXError as exc:
        raise TxUpstreamError("electrumx_error", {"message": str(exc)}) from exc
    finally:
        await client.close()

    # Wrap raw hex string as dictionary if returned that way
    if isinstance(tx_data, str):
        tx_data = {"hex": tx_data, "txid": norm_txid}

    return {
        "ok": True,
        "txid": norm_txid,
        "data": tx_data
    }
