#!/usr/bin/env python3
"""Create the final Phase F real-payment acceptance invoice without exposing secrets.

The helper reuses the address from the most recent confirmed authoritative payment,
reads the merchant API key from backend/.env, creates a small new payment against
the local VM-B API, and prints only the new payment capability/check-out URLs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request
from typing import Any


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_API_BASE = "http://127.0.0.1:8088"
DEFAULT_CHECKOUT_BASE = "https://pay.pepepow.net/"


class FinalPaymentError(RuntimeError):
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


def select_reuse_address(db_path: Path) -> str:
    if not db_path.exists():
        raise FinalPaymentError("Configured payment database does not exist")
    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        row = connection.execute(
            """
            SELECT address
            FROM payments
            WHERE status IN ('paid_confirmed', 'overpaid')
            ORDER BY updated_at DESC, payment_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            row = connection.execute(
                """
                SELECT address
                FROM payments
                ORDER BY updated_at DESC, payment_id DESC
                LIMIT 1
                """
            ).fetchone()
    finally:
        connection.close()

    if not row or not isinstance(row[0], str) or not row[0]:
        raise FinalPaymentError("No existing payment address is available for reuse")
    return row[0]


def create_payment(
    *,
    api_base: str,
    api_key: str,
    address: str,
    amount: str,
) -> dict[str, Any]:
    body = json.dumps(
        {
            "address": address,
            "amount": amount,
            "confirmations": 1,
            "expires_in": 1800,
            "label": "Phase F final acceptance",
            "message": "VM-B watcher paid_unconfirmed -> paid_confirmed test",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/v1/payments",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalPaymentError(f"Payment create returned non-JSON HTTP {status}") from exc

    if status != 201 or not isinstance(payload, dict):
        code = None
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            code = payload["error"].get("code")
        suffix = f" {code}" if code else ""
        raise FinalPaymentError(f"Payment create failed: HTTP {status}{suffix}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default="0.01")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--checkout-base", default=DEFAULT_CHECKOUT_BASE)
    args = parser.parse_args()

    if not ENV_PATH.exists():
        raise FinalPaymentError(f"Environment file not found: {ENV_PATH}")
    env = read_env(ENV_PATH)

    if env.get("PAYMENT_API_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        raise FinalPaymentError("PAYMENT_API_ENABLED is not true")
    if env.get("PAYMENT_WATCHER_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        raise FinalPaymentError("PAYMENT_WATCHER_ENABLED is not true")

    api_key = env.get("PAYMENT_CREATE_API_KEY")
    if not api_key or len(api_key) < 32:
        raise FinalPaymentError("PAYMENT_CREATE_API_KEY is not configured")

    db_raw = env.get("PAYMENT_DB_PATH")
    if not db_raw:
        raise FinalPaymentError("PAYMENT_DB_PATH is not configured")
    address = select_reuse_address(Path(db_raw).expanduser())

    payment = create_payment(
        api_base=args.api_base,
        api_key=api_key,
        address=address,
        amount=args.amount,
    )
    payment_id = payment.get("payment_id")
    status = payment.get("status")
    if not isinstance(payment_id, str) or not payment_id:
        raise FinalPaymentError("Payment create response is missing payment_id")

    checkout = args.checkout_base.rstrip("/") + "/?payment_id=" + payment_id
    compatibility = "https://light.pepepow.net/pay/?payment_id=" + payment_id
    status_url = "https://pay.pepepow.net/api/v1/payments/" + payment_id

    print("PHASE F FINAL PAYMENT CREATED: PASS")
    print(f"payment_id={payment_id}")
    print(f"amount={args.amount}")
    print(f"initial_status={status}")
    print(f"checkout_url={checkout}")
    print(f"compatibility_url={compatibility}")
    print(f"status_url={status_url}")
    print("Merchant API key was used in memory only and was not printed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FinalPaymentError, OSError, urllib.error.URLError) as exc:
        print(f"PHASE F FINAL PAYMENT CREATED: FAIL: {exc}")
        raise SystemExit(1)
