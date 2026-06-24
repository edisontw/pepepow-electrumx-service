from fastapi.responses import JSONResponse


ERROR_MESSAGES = {
    "invalid_address": "Invalid PEPEPOW address.",
    "empty_address": "Please enter a PEPEPOW address.",
    "invalid_address_checksum": "Address checksum is invalid.",
    "unsupported_address_prefix": "Address prefix is not supported.",
    "electrumx_error": "ElectrumX is temporarily unavailable.",
    "transaction_lookup_unavailable": "Transaction lookup is not available yet.",
    "payment_check_unavailable": "Payment checking is not available yet.",
}


def api_error_payload(code: str, message: str | None = None) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message or ERROR_MESSAGES.get(code, "Request failed."),
        },
    }


def api_error_response(status_code: int, code: str, message: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=api_error_payload(code, message))
