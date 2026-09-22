#!/usr/bin/env python3
"""Run a production PEPEW webhook delivery E2E without exposing secrets.

The test uses an anonymous Webhook.site token as a temporary public HTTPS
receiver. Only test payment metadata is sent to that third-party receiver.

Flow:
- verify the production SSRF guard rejects a loopback webhook target
- create a temporary receiver that initially returns HTTP 503
- register a payment.created webhook endpoint
- create a one-atom persisted test payment to emit an event
- verify the first delivery is persisted as retry
- verify the captured request HMAC from exact raw body bytes
- switch the receiver to HTTP 204
- verify the same event/delivery/body is retried and delivered
- verify endpoint/delivery list APIs do not expose signing_secret
- disable the endpoint and delete the temporary receiver token

Neither PAYMENT_CREATE_API_KEY, PAYMENT_WEBHOOK_MASTER_KEY nor the derived
endpoint signing secret is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
WEBHOOK_SITE_API = "https://webhook.site"


class E2EError(RuntimeError):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def http_bytes(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: float = 10.0,
) -> tuple[int, bytes]:
    request_headers = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request_headers.setdefault("Accept", "application/json")
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    status, raw = http_bytes(
        method,
        url,
        headers=headers,
        json_body=json_body,
        timeout=timeout,
    )
    if not raw:
        return status, {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E2EError(f"Non-JSON response from {url}: HTTP {status}") from exc
    if not isinstance(payload, dict):
        raise E2EError(f"Unexpected JSON response from {url}: HTTP {status}")
    return status, payload


def require_status(status: int, expected: int, payload: dict[str, Any], label: str) -> None:
    if status != expected:
        code = payload.get("error", {}).get("code") if isinstance(payload.get("error"), dict) else None
        suffix = f" ({code})" if code else ""
        raise E2EError(f"{label}: expected HTTP {expected}, got {status}{suffix}")


def header_value(headers: dict[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() != wanted:
            continue
        if isinstance(value, list):
            return str(value[0]) if value else None
        return str(value)
    return None


def verify_capture(
    token_id: str,
    request_meta: dict[str, Any],
    signing_secret: str,
) -> tuple[str, str, bytes]:
    headers = request_meta.get("headers")
    if not isinstance(headers, dict):
        raise E2EError("Webhook.site capture has no header map")

    event_id = header_value(headers, "X-PepewPay-Event-Id")
    delivery_id = header_value(headers, "X-PepewPay-Delivery-Id")
    timestamp = header_value(headers, "X-PepewPay-Timestamp")
    signature = header_value(headers, "X-PepewPay-Signature")
    request_id = request_meta.get("uuid")
    if not all((event_id, delivery_id, timestamp, signature, request_id)):
        raise E2EError("Captured webhook is missing PEPEW signature headers")

    raw_url = f"{WEBHOOK_SITE_API}/token/{token_id}/request/{request_id}/raw"
    status, raw_body = http_bytes("GET", raw_url)
    if status != 200:
        raise E2EError(f"Could not fetch exact captured body: HTTP {status}")

    signed = f"{timestamp}.{event_id}.".encode("utf-8") + raw_body
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        signed,
        hashlib.sha256,
    ).hexdigest()
    received = signature.removeprefix("v1=")
    if not hmac.compare_digest(expected, received):
        raise E2EError("Webhook HMAC verification failed")

    return event_id, delivery_id, raw_body


def list_captures(token_id: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"sorting": "newest", "per_page": 20})
    status, payload = json_request(
        "GET",
        f"{WEBHOOK_SITE_API}/token/{token_id}/requests?{query}",
    )
    require_status(status, 200, payload, "Fetch Webhook.site captures")
    data = payload.get("data", [])
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def captures_for_event(token_id: str, event_id: str) -> list[dict[str, Any]]:
    result = []
    for item in list_captures(token_id):
        headers = item.get("headers")
        if isinstance(headers, dict) and header_value(headers, "X-PepewPay-Event-Id") == event_id:
            result.append(item)
    return result


def wait_for_capture(token_id: str, event_id: str, minimum: int, deadline: float) -> list[dict[str, Any]]:
    while time.monotonic() < deadline:
        captures = captures_for_event(token_id, event_id)
        if len(captures) >= minimum:
            return captures
        time.sleep(1)
    raise E2EError(f"Timed out waiting for {minimum} captured webhook request(s)")


def list_deliveries(
    api_base: str,
    auth: dict[str, str],
    endpoint_id: str,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"endpoint_id": endpoint_id, "limit": 20})
    status, payload = json_request(
        "GET",
        f"{api_base}/api/v1/webhook-deliveries?{query}",
        headers=auth,
    )
    require_status(status, 200, payload, "List webhook deliveries")
    data = payload.get("deliveries", [])
    if not isinstance(data, list):
        raise E2EError("Webhook delivery response is malformed")
    for delivery in data:
        if isinstance(delivery, dict) and "signing_secret" in delivery:
            raise E2EError("Delivery log unexpectedly exposed signing_secret")
    return [item for item in data if isinstance(item, dict)]


def wait_for_delivery(
    api_base: str,
    auth: dict[str, str],
    endpoint_id: str,
    wanted_status: str,
    minimum_attempts: int,
    deadline: float,
) -> dict[str, Any]:
    while time.monotonic() < deadline:
        for item in list_deliveries(api_base, auth, endpoint_id):
            if (
                item.get("status") == wanted_status
                and int(item.get("attempt_count") or 0) >= minimum_attempts
            ):
                return item
        time.sleep(1)
    raise E2EError(
        f"Timed out waiting for delivery status={wanted_status} attempts>={minimum_attempts}"
    )


def resolve_address(
    api_base: str,
    *,
    address: str | None,
    reference_payment_id: str | None,
) -> str:
    if address:
        return address
    assert reference_payment_id
    status, payload = json_request(
        "GET",
        f"{api_base}/api/v1/payments/{urllib.parse.quote(reference_payment_id)}",
    )
    require_status(status, 200, payload, "Read reference payment")
    value = payload.get("address")
    if not isinstance(value, str) or not value:
        raise E2EError("Reference payment did not contain an address")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--address")
    source.add_argument("--reference-payment-id")
    parser.add_argument("--api-base", default="http://127.0.0.1:8088")
    parser.add_argument("--amount", default="0.00000001")
    parser.add_argument("--timeout-seconds", type=int, default=100)
    args = parser.parse_args()

    if not ENV_PATH.exists():
        raise E2EError(f"Environment file not found: {ENV_PATH}")
    env = read_env(ENV_PATH)
    if env.get("PAYMENT_WEBHOOK_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        raise E2EError("PAYMENT_WEBHOOK_ENABLED is not true in backend/.env")
    if not env.get("PAYMENT_WEBHOOK_MASTER_KEY"):
        raise E2EError("PAYMENT_WEBHOOK_MASTER_KEY is not configured")
    api_key = env.get("PAYMENT_CREATE_API_KEY")
    if not api_key:
        raise E2EError("PAYMENT_CREATE_API_KEY is not configured")

    api_base = args.api_base.rstrip("/")
    auth = {"Authorization": f"Bearer {api_key}"}
    endpoint_id: str | None = None
    token_id: str | None = None

    print("1/8 production webhook configuration: PASS")

    # Production SSRF guard: this must fail before any endpoint is created.
    status, payload = json_request(
        "POST",
        f"{api_base}/api/v1/webhook-endpoints",
        headers=auth,
        json_body={
            "url": "https://127.0.0.1/webhook-e2e-must-be-blocked",
            "event_types": ["payment.created"],
        },
    )
    code = payload.get("error", {}).get("code") if isinstance(payload.get("error"), dict) else None
    if status != 400 or code != "unsafe_webhook_target":
        raise E2EError(
            f"SSRF guard preflight failed: expected 400 unsafe_webhook_target, got {status} {code}"
        )
    print("2/8 SSRF loopback rejection: PASS")

    try:
        status, token = json_request(
            "POST",
            f"{WEBHOOK_SITE_API}/token",
            json_body={
                "default_status": 503,
                "default_content": "temporary failure",
                "default_content_type": "text/plain",
            },
        )
        require_status(status, 201, token, "Create temporary Webhook.site token")
        token_id_value = token.get("uuid")
        if not isinstance(token_id_value, str) or not token_id_value:
            raise E2EError("Webhook.site token response did not contain uuid")
        token_id = token_id_value

        status, endpoint = json_request(
            "POST",
            f"{api_base}/api/v1/webhook-endpoints",
            headers=auth,
            json_body={
                "url": f"https://webhook.site/{token_id}",
                "event_types": ["payment.created"],
            },
        )
        require_status(status, 201, endpoint, "Create PEPEW webhook endpoint")
        endpoint_id_value = endpoint.get("endpoint_id")
        signing_secret_value = endpoint.get("signing_secret")
        if not isinstance(endpoint_id_value, str) or not endpoint_id_value:
            raise E2EError("Endpoint response did not contain endpoint_id")
        if not isinstance(signing_secret_value, str) or not signing_secret_value:
            raise E2EError("Endpoint response did not contain one-time signing_secret")
        endpoint_id = endpoint_id_value
        signing_secret = signing_secret_value

        status, listed = json_request(
            "GET",
            f"{api_base}/api/v1/webhook-endpoints",
            headers=auth,
        )
        require_status(status, 200, listed, "List webhook endpoints")
        endpoints = listed.get("endpoints", [])
        match = next(
            (
                item for item in endpoints
                if isinstance(item, dict) and item.get("endpoint_id") == endpoint_id
            ),
            None,
        )
        if not match:
            raise E2EError("Created webhook endpoint was not returned by list API")
        if "signing_secret" in match:
            raise E2EError("Endpoint list unexpectedly exposed signing_secret")
        print(f"3/8 endpoint create/list secret boundary: PASS ({endpoint_id})")

        payment_address = resolve_address(
            api_base,
            address=args.address,
            reference_payment_id=args.reference_payment_id,
        )
        status, payment = json_request(
            "POST",
            f"{api_base}/api/v1/payments",
            headers=auth,
            json_body={
                "address": payment_address,
                "amount": args.amount,
                "confirmations": 1,
                "expires_in": 600,
                "label": "Webhook production E2E",
                "message": "Automated payment.created delivery test",
            },
        )
        require_status(status, 201, payment, "Create webhook E2E payment")
        payment_id = payment.get("payment_id")
        if not isinstance(payment_id, str):
            raise E2EError("Payment create response did not contain payment_id")
        print(f"4/8 payment.created event trigger: PASS ({payment_id})")

        deadline = time.monotonic() + args.timeout_seconds
        first = wait_for_delivery(
            api_base,
            auth,
            endpoint_id,
            "retry",
            1,
            deadline,
        )
        if int(first.get("http_status") or 0) != 503:
            raise E2EError(f"First delivery did not persist HTTP 503: {first.get('http_status')}")
        event_id = first.get("event_id")
        delivery_id = first.get("delivery_id")
        if not isinstance(event_id, str) or not isinstance(delivery_id, str):
            raise E2EError("Delivery log is missing stable event/delivery IDs")
        print("5/8 persisted 503 -> retry transition: PASS")

        captures = wait_for_capture(token_id, event_id, 1, deadline)
        first_event_id, first_delivery_id, first_body = verify_capture(
            token_id,
            captures[-1],
            signing_secret,
        )
        if first_event_id != event_id or first_delivery_id != delivery_id:
            raise E2EError("Captured first request IDs do not match delivery log")
        print("6/8 first real receiver HMAC verification: PASS")

        status, updated = json_request(
            "PUT",
            f"{WEBHOOK_SITE_API}/token/{token_id}",
            json_body={
                "default_status": 204,
                "default_content": "",
                "default_content_type": "text/plain",
            },
        )
        require_status(status, 200, updated, "Switch temporary receiver to HTTP 204")

        delivered = wait_for_delivery(
            api_base,
            auth,
            endpoint_id,
            "delivered",
            2,
            deadline,
        )
        if int(delivered.get("http_status") or 0) != 204:
            raise E2EError(
                f"Retry delivery did not persist HTTP 204: {delivered.get('http_status')}"
            )

        captures = wait_for_capture(token_id, event_id, 2, deadline)
        verified = [
            verify_capture(token_id, item, signing_secret)
            for item in captures[:2]
        ]
        if any(item[0] != event_id or item[1] != delivery_id for item in verified):
            raise E2EError("Retry changed event_id or delivery_id")
        if any(item[2] != first_body for item in verified):
            raise E2EError("Retry changed the persisted event body")
        print("7/8 retry -> 204 delivered; stable IDs/body + HMAC: PASS")

        # A final delivery-log read also checks that secrets are absent.
        log = list_deliveries(api_base, auth, endpoint_id)
        current = next((item for item in log if item.get("delivery_id") == delivery_id), None)
        if not current or current.get("status") != "delivered":
            raise E2EError("Final delivery log did not report delivered")
        print("8/8 authenticated delivery log: PASS")

        print()
        print("WEBHOOK PRODUCTION E2E: PASS")
        print(f"payment_id={payment_id}")
        print(f"event_id={event_id}")
        print(f"delivery_id={delivery_id}")
        print("Secrets were used in memory only and were not printed.")
        return 0
    finally:
        if endpoint_id:
            try:
                json_request(
                    "DELETE",
                    f"{api_base}/api/v1/webhook-endpoints/{urllib.parse.quote(endpoint_id)}",
                    headers=auth,
                )
            except Exception:
                print("WARN: could not disable temporary webhook endpoint", file=sys.stderr)
        if token_id:
            try:
                http_bytes("DELETE", f"{WEBHOOK_SITE_API}/token/{token_id}")
            except Exception:
                print("WARN: could not delete temporary Webhook.site token", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (E2EError, OSError, urllib.error.URLError) as exc:
        print(f"WEBHOOK PRODUCTION E2E: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
