from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body, Path, Query, status
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

BROADCAST_ALLOWED_FIELDS = {"raw_tx"}
BROADCAST_FORBIDDEN_SECRET_FIELDS = {
    "mnemonic",
    "seed",
    "_".join(["private", "key"]),
    "privateKey",
    "priv" + "key",
    "w" + "if",
    "x" + "prv",
    "password",
    "passphrase",
}
BROADCAST_INVALID_PAYLOAD_MESSAGE = "Broadcast accepts only signed raw_tx hex and rejects signing material."
MAX_VERBOSE_HISTORY_DETAILS = 25


def _extract_broadcast_raw_tx(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise InvalidRawTxError("invalid_broadcast_payload", BROADCAST_INVALID_PAYLOAD_MESSAGE)

    keys = set(payload.keys())
    extra_keys = keys - BROADCAST_ALLOWED_FIELDS
    forbidden_keys = keys & BROADCAST_FORBIDDEN_SECRET_FIELDS
    if extra_keys or forbidden_keys:
        raise InvalidRawTxError("invalid_broadcast_payload", BROADCAST_INVALID_PAYLOAD_MESSAGE)

    return payload.get("raw_tx")


async def _call_with_optional_fresh(
    func: Callable[..., Awaitable[dict[str, Any]]],
    *args: Any,
    fresh: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return await func(*args, fresh=fresh, **kwargs)
    except TypeError as exc:
        if "fresh" not in str(exc):
            raise
        return await func(*args, **kwargs)


def _format_pepew_from_atoms(value: int) -> str:
    settings = get_settings()
    return format_pepew_amount_from_sats(value, settings.pepew_decimals)


def _tx_addresses_from_script(script: Any) -> list[str]:
    if not isinstance(script, dict):
        return []
    addresses: list[str] = []
    address = script.get("address")
    if isinstance(address, str):
        addresses.append(address)
    raw_addresses = script.get("addresses")
    if isinstance(raw_addresses, list):
        addresses.extend(item for item in raw_addresses if isinstance(item, str))
    return addresses


def _tx_output_value_atoms(output: Any) -> int:
    if not isinstance(output, dict):
        return 0
    for key in ("valueSat", "value_sats", "satoshis", "atoms"):
        if key in output:
            try:
                return int(output.get(key) or 0)
            except (TypeError, ValueError):
                return 0
    value = output.get("value")
    try:
        if isinstance(value, int):
            return value if abs(value) >= 1_000_000 else value * 100_000_000
        if isinstance(value, float):
            return round(value * 100_000_000)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            if "." in stripped:
                return round(float(stripped) * 100_000_000)
            parsed = int(stripped)
            return parsed if abs(parsed) >= 1_000_000 else parsed * 100_000_000
    except (TypeError, ValueError):
        return 0
    return 0


def _outputs_to_address(tx_data: Any, address: str) -> int:
    if not isinstance(tx_data, dict):
        return 0
    outputs = tx_data.get("vout")
    if not isinstance(outputs, list):
        outputs = tx_data.get("outputs")
    if not isinstance(outputs, list):
        return 0

    total = 0
    for output in outputs:
        if not isinstance(output, dict):
            continue
        script = output.get("scriptPubKey") or output.get("script_pub_key") or output.get("script")
        if address in _tx_addresses_from_script(script):
            total += _tx_output_value_atoms(output)
    return total


def _tx_output_by_index(tx_data: Any, index: int) -> Any | None:
    if not isinstance(tx_data, dict):
        return None
    outputs = tx_data.get("vout")
    if not isinstance(outputs, list):
        outputs = tx_data.get("outputs")
    if not isinstance(outputs, list):
        return None
    for output in outputs:
        if not isinstance(output, dict):
            continue
        n = output.get("n")
        try:
            if n is not None and int(n) == index:
                return output
        except (TypeError, ValueError):
            pass
    if 0 <= index < len(outputs):
        return outputs[index]
    return None


async def _inputs_from_address(tx_data: Any, address: str) -> int:
    if not isinstance(tx_data, dict):
        return 0
    inputs = tx_data.get("vin")
    if not isinstance(inputs, list):
        inputs = tx_data.get("inputs")
    if not isinstance(inputs, list):
        return 0

    total = 0
    prev_cache: dict[str, dict[str, Any]] = {}
    for tx_input in inputs:
        if not isinstance(tx_input, dict):
            continue
        prev_txid = tx_input.get("txid") or tx_input.get("tx_hash")
        prev_vout = tx_input.get("vout") or tx_input.get("tx_pos") or tx_input.get("outputIndex")
        if not isinstance(prev_txid, str):
            continue
        try:
            prev_index = int(prev_vout)
        except (TypeError, ValueError):
            continue

        if prev_txid not in prev_cache:
            try:
                prev_cache[prev_txid] = (await get_transaction_details(prev_txid, verbose=True)).get("data", {})
            except TxLookupError:
                prev_cache[prev_txid] = {}
        prev_output = _tx_output_by_index(prev_cache[prev_txid], prev_index)
        if not isinstance(prev_output, dict):
            continue
        script = prev_output.get("scriptPubKey") or prev_output.get("script_pub_key") or prev_output.get("script")
        if address in _tx_addresses_from_script(script):
            total += _tx_output_value_atoms(prev_output)
    return total


def _basic_history_row(item: dict[str, Any], *, is_mempool: bool = False) -> dict[str, Any]:
    return {
        "txid": item.get("tx_hash") or item.get("txid"),
        "height": item.get("height"),
        "is_mempool": is_mempool,
    }


async def _verbose_history_row(item: dict[str, Any], address: str, *, is_mempool: bool = False) -> dict[str, Any]:
    row = _basic_history_row(item, is_mempool=is_mempool)
    txid = row.get("txid")
    if not isinstance(txid, str):
        return row

    try:
        tx_detail = await get_transaction_details(txid, verbose=True)
        tx_data = tx_detail.get("data", {})
        received_atoms = _outputs_to_address(tx_data, address)
        spent_atoms = await _inputs_from_address(tx_data, address)
        delta_atoms = received_atoms - spent_atoms
        if delta_atoms > 0:
            direction = "received"
        elif delta_atoms < 0:
            direction = "sent"
        elif received_atoms > 0 and spent_atoms > 0:
            direction = "self"
        else:
            direction = "unknown"
        row.update({
            "direction": direction,
            "amount_atoms": abs(delta_atoms),
            "amount_pepew": _format_pepew_from_atoms(abs(delta_atoms)),
            "address_delta_atoms": delta_atoms,
            "address_delta_pepew": _format_pepew_from_atoms(delta_atoms),
            "received_atoms": received_atoms,
            "spent_atoms": spent_atoms,
            "timestamp": tx_data.get("blocktime") or tx_data.get("time"),
            "confirmations": tx_data.get("confirmations"),
        })
    except TxLookupError:
        row.update({
            "direction": "unknown",
            "amount_atoms": 0,
            "amount_pepew": "0",
            "address_delta_atoms": 0,
            "address_delta_pepew": "0",
        })
    return row


async def _map_history_rows(
    items: list[dict[str, Any]],
    address: str,
    *,
    verbose: bool,
    detail_limit: int,
    is_mempool: bool = False,
) -> list[dict[str, Any]]:
    if not verbose:
        return [_basic_history_row(item, is_mempool=is_mempool) for item in items]

    mapped: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if index < detail_limit:
            mapped.append(await _verbose_history_row(item, address, is_mempool=is_mempool))
        else:
            mapped.append(_basic_history_row(item, is_mempool=is_mempool))
    return mapped


@router.get("/address/{address}")
async def wallet_address_lookup(
    address: str = Path(...),
    fresh: bool = Query(default=False),
    verbose_history: bool = Query(default=False),
    detail_limit: int = Query(default=10, ge=0, le=MAX_VERBOSE_HISTORY_DETAILS),
) -> JSONResponse:
    try:
        summary = await _call_with_optional_fresh(get_address_summary, address, fresh=fresh)
        history_data = await _call_with_optional_fresh(get_address_history, address, limit=50, offset=0, fresh=fresh)

        confirmed_sats = summary["balance"]["confirmed"]
        unconfirmed_sats = summary["balance"]["unconfirmed"]

        settings = get_settings()
        confirmed_pepew = format_pepew_amount_from_sats(confirmed_sats, settings.pepew_decimals)
        unconfirmed_pepew = format_pepew_amount_from_sats(unconfirmed_sats, settings.pepew_decimals)

        mapped_history = await _map_history_rows(
            history_data.get("history", []),
            summary["address"],
            verbose=verbose_history,
            detail_limit=detail_limit,
        )

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
    verbose: bool = Query(default=True),
    detail_limit: int = Query(default=10, ge=0, le=MAX_VERBOSE_HISTORY_DETAILS),
) -> JSONResponse:
    try:
        history_data = await _call_with_optional_fresh(get_address_history, address, limit=limit, offset=offset, fresh=fresh)

        mapped_history = await _map_history_rows(
            history_data.get("history", []),
            history_data["address"],
            verbose=verbose,
            detail_limit=detail_limit,
        )
        mapped_mempool = await _map_history_rows(
            history_data.get("mempool", []),
            history_data["address"],
            verbose=verbose,
            detail_limit=detail_limit,
            is_mempool=True,
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "address": history_data["address"],
                "history": mapped_history,
                "mempool": mapped_mempool,
                "source": "electrumx",
                "read_only": True,
                "verbose": verbose,
                "detail_limit": detail_limit if verbose else 0,
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
async def wallet_broadcast_signed_raw_tx(payload: Any = Body(...)) -> JSONResponse:
    try:
        raw_tx = _extract_broadcast_raw_tx(payload)
        result = await broadcast_signed_raw_tx(raw_tx)
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
