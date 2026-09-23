#!/usr/bin/env python3
"""Read-only/public Phase F post-cutover acceptance checks.

No merchant API key or webhook secret is required. The script does not create
payments. It verifies that authoritative Payment Platform v1 routes are live on
pay.pepepow.net, compatibility-proxied from light.pepepow.net, and that the
legacy stateless payment monitor remains local to PEPEW Light.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


PAY_BASE = "https://pay.pepepow.net"
LIGHT_BASE = "https://light.pepepow.net"
FAKE_PAYMENT_ID = "pay_phase_f_acceptance_missing"
CANONICAL_ADDRESS = "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"


class AcceptanceError(RuntimeError):
    pass


def request(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, bytes, dict[str, str]]:
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def json_payload(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{label}: response was not JSON") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label}: response JSON was not an object")
    return value


def error_code(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        return code if isinstance(code, str) else None
    return None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def main() -> int:
    status, raw, _ = request("GET", f"{PAY_BASE}/api/health")
    payload = json_payload(raw, "pay health")
    require(status == 200 and payload.get("ok") is True and payload.get("app") == "pepew-pay",
            f"pay health: expected 200 pepew-pay ok, got HTTP {status}")
    print("1/8 pay health: PASS")

    status, raw, _ = request("GET", f"{PAY_BASE}/api/status")
    payload = json_payload(raw, "pay status")
    electrumx = payload.get("electrumx")
    require(
        status == 200
        and payload.get("ok") is True
        and isinstance(electrumx, dict)
        and electrumx.get("connected") is True,
        f"pay status: ElectrumX not connected (HTTP {status})",
    )
    print("2/8 pay ElectrumX status: PASS")

    status, raw, _ = request("GET", f"{PAY_BASE}/")
    require(status == 200 and b"<title>PepewPay</title>" in raw,
            f"PepewPay root: expected static UI, got HTTP {status}")
    print("3/8 PepewPay static root: PASS")

    missing_url = f"{PAY_BASE}/api/v1/payments/{FAKE_PAYMENT_ID}"
    status, raw, _ = request("GET", missing_url)
    payload = json_payload(raw, "pay missing payment")
    require(status == 404 and error_code(payload) == "payment_not_found",
            f"pay payment API: expected 404 payment_not_found, got HTTP {status} {error_code(payload)}")
    print("4/8 pay authoritative payment API: PASS")

    light_missing_url = f"{LIGHT_BASE}/api/v1/payments/{FAKE_PAYMENT_ID}"
    status, raw, _ = request("GET", light_missing_url)
    payload = json_payload(raw, "light compatibility payment")
    require(status == 404 and error_code(payload) == "payment_not_found",
            f"light compatibility proxy: expected 404 payment_not_found, got HTTP {status} {error_code(payload)}")
    print("5/8 light -> pay payment compatibility proxy: PASS")

    status, raw, headers = request(
        "POST",
        f"{PAY_BASE}/api/v1/payments",
        json_body={
            "address": CANONICAL_ADDRESS,
            "amount": "1",
            "confirmations": 1,
            "expires_in": 600,
        },
    )
    payload = json_payload(raw, "unauthenticated create")
    require(status == 401 and error_code(payload) == "payment_auth_required",
            f"unauthenticated create: expected 401 payment_auth_required, got HTTP {status} {error_code(payload)}")
    require(headers.get("WWW-Authenticate", "").lower() == "bearer",
            "unauthenticated create: missing WWW-Authenticate: Bearer")
    print("6/8 unauthenticated create boundary: PASS")

    legacy_query = urllib.parse.urlencode({"address": "x", "amount": "1"})
    status, raw, _ = request("GET", f"{LIGHT_BASE}/api/payment/check?{legacy_query}")
    payload = json_payload(raw, "light legacy payment monitor")
    require(status == 400 and error_code(payload) in {"invalid_address", "invalid_address_checksum", "unsupported_address_prefix"},
            f"light legacy monitor: expected local validation 400, got HTTP {status} {error_code(payload)}")
    print("7/8 Light legacy payment/check remains local: PASS")

    status, _raw, _ = request("GET", f"{PAY_BASE}/api/payment/check?{legacy_query}")
    require(status == 404, f"pay legacy API boundary: expected 404, got HTTP {status}")
    print("8/8 pay domain excludes legacy Light API: PASS")

    print()
    print("PHASE F POST-CUTOVER ACCEPTANCE: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceError, OSError, urllib.error.URLError) as exc:
        print(f"PHASE F POST-CUTOVER ACCEPTANCE: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
