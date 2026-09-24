#!/usr/bin/env python3
"""Run Phase G merchant-integration production acceptance on VM-B.

The helper reads the merchant API key and SQLite path from backend/.env, exercises
only the authoritative Payment Platform API, and never prints secret credentials
or payment capability URLs.

It creates one one-atom test payment with a unique merchant_reference and a
unique Idempotency-Key. The payment is intentionally not funded and expires
after 10 minutes. By default the script refuses to create the test payment while
any webhook endpoint is enabled, preventing accidental delivery of test events
to a real merchant receiver.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_API_BASE = "https://pay.pepepow.net"
TEST_AMOUNT = "0.00000001"
CHANGED_AMOUNT = "0.00000002"


class AcceptanceError(RuntimeError):
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


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request_headers = {"Accept": "application/json"}
    request_headers.update(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        response_headers = dict(exc.headers.items())

    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{method} {url}: non-JSON HTTP {status}") from exc
    if not isinstance(payload, dict):
        raise AcceptanceError(f"{method} {url}: JSON response was not an object")
    return status, payload, response_headers


def error_code(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def header_value(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise AcceptanceError("Configured payment database does not exist")
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def latest_payment_address(db_path: Path) -> str:
    connection = open_readonly(db_path)
    try:
        row = connection.execute(
            """
            SELECT address
            FROM payments
            ORDER BY created_at DESC, payment_id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None or not isinstance(row["address"], str) or not row["address"]:
        raise AcceptanceError("No existing payment address is available for test reuse")
    return str(row["address"])


def enabled_webhook_count(db_path: Path) -> int:
    connection = open_readonly(db_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM webhook_endpoints WHERE enabled = 1"
        ).fetchone()
    finally:
        connection.close()
    return 0 if row is None else int(row["count"])


def verify_merchant_reference_schema(db_path: Path) -> None:
    connection = open_readonly(db_path)
    try:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(payments)").fetchall()
        }
        indexes = {
            str(row["name"])
            for row in connection.execute("PRAGMA index_list(payments)").fetchall()
        }
    finally:
        connection.close()

    require(
        "merchant_reference" in columns,
        "SQLite migration missing payments.merchant_reference",
    )
    require(
        "idx_payments_merchant_reference" in indexes,
        "SQLite migration missing merchant_reference unique index",
    )


def verify_single_created_event(
    db_path: Path,
    payment_id: str,
    merchant_reference: str,
) -> None:
    connection = open_readonly(db_path)
    try:
        rows = connection.execute(
            """
            SELECT event_type, payload_json
            FROM events
            WHERE payment_id = ?
            ORDER BY rowid
            """,
            (payment_id,),
        ).fetchall()
    finally:
        connection.close()

    require(len(rows) == 1, f"Expected exactly one event for acceptance payment, got {len(rows)}")
    require(str(rows[0]["event_type"]) == "payment.created", "Acceptance event is not payment.created")
    raw_payload = rows[0]["payload_json"]
    require(isinstance(raw_payload, str), "payment.created event has no persisted JSON body")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise AcceptanceError("payment.created event JSON is invalid") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    require(isinstance(data, dict), "payment.created event has no data object")
    require(
        data.get("merchant_reference") == merchant_reference,
        "payment.created event did not persist merchant_reference",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Phase G production acceptance against authoritative VM-B."
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_API_BASE,
        help="Payment API base. Defaults to public pay.pepepow.net to include Nginx.",
    )
    parser.add_argument(
        "--allow-active-webhooks",
        action="store_true",
        help="Allow the test payment to emit payment.created to currently enabled webhook endpoints.",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        raise AcceptanceError(f"Environment file not found: {ENV_PATH}")
    env = read_env(ENV_PATH)

    require(truthy(env.get("PAYMENT_API_ENABLED")), "PAYMENT_API_ENABLED is not true")
    require(truthy(env.get("PAYMENT_WATCHER_ENABLED")), "PAYMENT_WATCHER_ENABLED is not true")

    api_key = env.get("PAYMENT_CREATE_API_KEY")
    require(bool(api_key) and len(api_key or "") >= 32, "PAYMENT_CREATE_API_KEY is not configured")

    db_raw = env.get("PAYMENT_DB_PATH")
    require(bool(db_raw), "PAYMENT_DB_PATH is not configured")
    db_path = Path(str(db_raw)).expanduser()

    payment_address = latest_payment_address(db_path)
    active_webhooks = enabled_webhook_count(db_path)
    if active_webhooks and not args.allow_active_webhooks:
        raise AcceptanceError(
            f"Refusing to create acceptance payment while {active_webhooks} webhook endpoint(s) are enabled; "
            "disable them temporarily or rerun with --allow-active-webhooks after confirming test delivery is acceptable"
        )
    print("1/10 VM-B production config + safe test preflight: PASS")

    api_base = args.api_base.rstrip("/")
    auth = {"Authorization": f"Bearer {api_key}"}

    status, payload, _ = json_request("GET", f"{api_base}/api/health")
    require(status == 200 and payload.get("ok") is True, f"health expected 200 ok, got HTTP {status}")
    status, payload, _ = json_request("GET", f"{api_base}/api/status")
    electrumx = payload.get("electrumx")
    require(
        status == 200
        and payload.get("ok") is True
        and isinstance(electrumx, dict)
        and electrumx.get("connected") is True,
        f"status expected connected ElectrumX, got HTTP {status}",
    )
    print("2/10 pay health + ElectrumX status: PASS")

    status, payload, headers = json_request("GET", f"{api_base}/api/v1/payments")
    require(
        status == 401 and error_code(payload) == "payment_auth_required",
        f"anonymous merchant listing expected 401 payment_auth_required, got HTTP {status} {error_code(payload)}",
    )
    require(
        (header_value(headers, "WWW-Authenticate") or "").lower() == "bearer",
        "anonymous merchant listing missing WWW-Authenticate: Bearer",
    )
    print("3/10 anonymous merchant enumeration blocked: PASS")

    status, payload, _ = json_request(
        "GET",
        f"{api_base}/api/v1/payments?limit=1",
        headers=auth,
    )
    require(status == 200 and payload.get("ok") is True, f"authenticated listing failed: HTTP {status}")
    verify_merchant_reference_schema(db_path)
    print("4/10 authenticated listing + SQLite merchant_reference migration: PASS")

    suffix = f"{int(time.time())}-{secrets.token_hex(6)}"
    merchant_reference = f"phase-g-acceptance/{suffix}"
    idempotency_key = f"phase-g-acceptance:{suffix}"

    create_body = {
        "address": payment_address,
        "amount": TEST_AMOUNT,
        "confirmations": 1,
        "expires_in": 600,
        "label": "Phase G production acceptance",
        "message": "Idempotency/recovery/reference test; intentionally unfunded",
        "merchant_reference": merchant_reference,
    }
    create_headers = {**auth, "Idempotency-Key": idempotency_key}

    status, created, _ = json_request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=create_headers,
        json_body=create_body,
    )
    require(status == 201, f"initial create expected HTTP 201, got {status} {error_code(created)}")
    payment_id = created.get("payment_id")
    require(isinstance(payment_id, str) and bool(payment_id), "create response missing payment_id")
    require(created.get("merchant_reference") == merchant_reference, "create response lost merchant_reference")
    require(created.get("idempotency_key") == idempotency_key, "create response lost Idempotency-Key metadata")
    print("5/10 merchant_reference + Idempotency-Key create: PASS")

    status, replay, _ = json_request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=create_headers,
        json_body=create_body,
    )
    require(status == 201, f"idempotent replay expected HTTP 201, got {status} {error_code(replay)}")
    require(replay.get("payment_id") == payment_id, "idempotent retry created a different payment")
    print("6/10 same-key/same-request replay returns original payment: PASS")

    changed_body = dict(create_body)
    changed_body["amount"] = CHANGED_AMOUNT
    status, conflict, _ = json_request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=create_headers,
        json_body=changed_body,
    )
    require(
        status == 409 and error_code(conflict) == "payment_idempotency_conflict",
        f"changed request expected 409 payment_idempotency_conflict, got HTTP {status} {error_code(conflict)}",
    )
    print("7/10 same-key/different-request conflict: PASS")

    different_retry_headers = {
        **auth,
        "Idempotency-Key": f"{idempotency_key}:different",
    }
    status, conflict, _ = json_request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=different_retry_headers,
        json_body=create_body,
    )
    require(
        status == 409 and error_code(conflict) == "payment_merchant_reference_conflict",
        f"duplicate reference expected 409 payment_merchant_reference_conflict, got HTTP {status} {error_code(conflict)}",
    )
    print("8/10 duplicate merchant_reference conflict: PASS")

    query = urllib.parse.urlencode(
        {
            "merchant_reference": merchant_reference,
            "limit": 10,
        }
    )
    status, recovered, _ = json_request(
        "GET",
        f"{api_base}/api/v1/payments?{query}",
        headers=auth,
    )
    payments = recovered.get("payments")
    require(status == 200 and isinstance(payments, list), f"reference recovery failed: HTTP {status}")
    require(len(payments) == 1, f"reference recovery expected one payment, got {len(payments)}")
    recovered_payment = payments[0]
    require(isinstance(recovered_payment, dict), "reference recovery item is malformed")
    require(recovered_payment.get("payment_id") == payment_id, "reference recovery returned wrong payment")
    require(recovered_payment.get("merchant_reference") == merchant_reference, "reference recovery lost merchant_reference")
    require(recovered_payment.get("idempotency_key") == idempotency_key, "reference recovery lost idempotency metadata")

    status, public_payment, _ = json_request(
        "GET",
        f"{api_base}/api/v1/payments/{urllib.parse.quote(payment_id)}",
    )
    require(status == 200 and public_payment.get("payment_id") == payment_id, "public capability status failed")
    require("merchant_reference" not in public_payment, "public status exposed merchant_reference")
    require("idempotency_key" not in public_payment, "public status exposed Idempotency-Key")
    print("9/10 authenticated recovery + public metadata privacy: PASS")

    verify_single_created_event(db_path, payment_id, merchant_reference)
    print("10/10 exactly one payment.created event with merchant reference: PASS")

    print()
    print("PHASE G PRODUCTION ACCEPTANCE: PASS")
    print("One unfunded one-atom acceptance payment was persisted and will expire normally.")
    print("Merchant API key, payment capability ID, capability URL, and address were not printed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        print(f"PHASE G PRODUCTION ACCEPTANCE: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
