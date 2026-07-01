import re
from typing import Any

from ..config import get_settings
from ..electrumx.client import ElectrumXClient
from ..electrumx.errors import ElectrumXError, ElectrumXMethodError
from ..electrumx.methods import transaction_broadcast, transaction_get, server_version
from ..pepepow.tx import normalize_txid

SIGNED_RAW_TX_RE = re.compile(r"^[0-9a-fA-F]+$")
MIN_RAW_TX_HEX_CHARS = 20
MAX_RAW_TX_HEX_CHARS = 200_000


class TxLookupError(Exception):
    code = "tx_lookup_error"


class InvalidTxidError(TxLookupError):
    code = "invalid_txid"

    def __init__(self, code: str = "invalid_txid", message: str = "Transaction id must be 64 hex characters.") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class InvalidRawTxError(TxLookupError):
    code = "invalid_raw_tx"

    def __init__(self, code: str = "invalid_raw_tx", message: str = "Signed raw transaction must be hex.") -> None:
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


def normalize_signed_raw_tx(raw_tx: str) -> str:
    if not isinstance(raw_tx, str):
        raise InvalidRawTxError("invalid_raw_tx", "Signed raw transaction must be hex.")
    value = raw_tx.strip()
    if value.startswith("0x") or value.startswith("0X"):
        value = value[2:]
    if len(value) < MIN_RAW_TX_HEX_CHARS:
        raise InvalidRawTxError("raw_tx_too_short", "Signed raw transaction is too short.")
    if len(value) > MAX_RAW_TX_HEX_CHARS:
        raise InvalidRawTxError("raw_tx_too_large", "Signed raw transaction is too large.")
    if len(value) % 2 != 0:
        raise InvalidRawTxError("invalid_raw_tx", "Signed raw transaction hex length must be even.")
    if SIGNED_RAW_TX_RE.fullmatch(value) is None:
        raise InvalidRawTxError("invalid_raw_tx", "Signed raw transaction must be hex.")
    return value.lower()


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
        raise TxUpstreamError("electrumx_error", {"message": str(exc)[:200]}) from exc
    except ElectrumXError as exc:
        raise TxUpstreamError("electrumx_error", {"message": str(exc)[:200]}) from exc
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


async def broadcast_signed_raw_tx(raw_tx: str) -> dict[str, Any]:
    signed_raw_tx = normalize_signed_raw_tx(raw_tx)
    settings = get_settings()
    client = ElectrumXClient(settings)
    try:
        await server_version(client)
        result = await transaction_broadcast(client, signed_raw_tx)
    except ElectrumXMethodError as exc:
        raise TxUpstreamError("broadcast_rejected", {"message": str(exc)[:200]}) from exc
    except ElectrumXError as exc:
        raise TxUpstreamError("electrumx_error", {"message": str(exc)[:200]}) from exc
    finally:
        await client.close()

    txid = result if isinstance(result, str) else None
    return {
        "ok": True,
        "txid": txid,
        "source": "electrumx",
    }
