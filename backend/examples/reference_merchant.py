#!/usr/bin/env python3
"""Reference merchant-side integration for PEPEW Payment Platform v1.

This module is intentionally standalone example code. The production PEPEW
Payment Platform does not import it.

It demonstrates the merchant boundary:
- keep the Bearer API key and webhook signing secret server-side
- persist merchant_reference + Idempotency-Key before creating a payment
- create/recover payments through the authenticated merchant API
- expose only the high-entropy payment_id through the PepewPay checkout URL
- verify webhook signatures over the exact raw body
- enforce a replay window and deduplicate by event_id
- apply payment state by increasing payment_version, not by status ranking
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import sqlite3
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "https://pay.pepepow.net"
DEFAULT_CHECKOUT_BASE = "https://pay.pepepow.net/"
DEFAULT_WEBHOOK_REPLAY_WINDOW_SECONDS = 300

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MERCHANT_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PAYMENT_ID_RE = re.compile(r"^pay_[A-Za-z0-9_-]{8,92}$")


class ReferenceMerchantError(RuntimeError):
    pass


class MerchantApiError(ReferenceMerchantError):
    def __init__(self, status: int, code: str = "merchant_api_error"):
        super().__init__(f"PEPEW merchant API request failed with HTTP {status}: {code}")
        self.status = int(status)
        self.code = code


class MerchantTransportError(ReferenceMerchantError):
    pass


class WebhookVerificationError(ReferenceMerchantError):
    pass


class MerchantStateError(ReferenceMerchantError):
    pass


class UnknownMerchantOrderError(MerchantStateError):
    pass


def _require_https_origin(value: str, *, field: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be an HTTPS URL without credentials, query, or fragment")
    return value.rstrip("/")


def _require_merchant_reference(value: str) -> str:
    if not _MERCHANT_REFERENCE_RE.fullmatch(value):
        raise ValueError("merchant_reference does not satisfy the PEPEW v1 format")
    return value


def _require_idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise ValueError("Idempotency-Key does not satisfy the PEPEW v1 format")
    return value


def _require_payment_id(value: str) -> str:
    if not _PAYMENT_ID_RE.fullmatch(value):
        raise ValueError("payment_id does not satisfy the PEPEW capability format")
    return value


def build_checkout_url(
    payment_id: str,
    *,
    checkout_base: str = DEFAULT_CHECKOUT_BASE,
) -> str:
    """Return the public PepewPay capability URL.

    No merchant API key, webhook secret, merchant reference, wallet secret, or
    other private metadata is placed in this URL.
    """

    payment_id = _require_payment_id(payment_id)
    base = _require_https_origin(checkout_base, field="checkout_base")
    return f"{base}/?payment_id={quote(payment_id, safe='')}"


def _safe_api_error_code(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "merchant_api_error"
    if not isinstance(payload, dict):
        return "merchant_api_error"
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"][:96]
    return "merchant_api_error"


@dataclass
class MerchantApiClient:
    api_key: str
    api_base: str = DEFAULT_API_BASE
    timeout_seconds: float = 10.0
    opener: Callable[..., Any] = urlopen

    def __post_init__(self) -> None:
        self.api_base = _require_https_origin(self.api_base, field="api_base")
        if not self.api_key:
            raise ValueError("merchant API key is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        body = None
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        request = Request(
            f"{self.api_base}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            try:
                raw = response.read()
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except HTTPError as exc:
            raw = exc.read(4096)
            raise MerchantApiError(exc.code, _safe_api_error_code(raw)) from None
        except (URLError, OSError, TimeoutError) as exc:
            raise MerchantTransportError(type(exc).__name__) from None

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MerchantApiError(502, "invalid_json_response") from exc
        if not isinstance(decoded, dict):
            raise MerchantApiError(502, "invalid_json_response")
        return decoded

    def create_payment(
        self,
        *,
        merchant_reference: str,
        idempotency_key: str,
        address: str,
        amount: str,
        confirmations: int | None = None,
        expires_in: int | None = None,
        label: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        merchant_reference = _require_merchant_reference(merchant_reference)
        idempotency_key = _require_idempotency_key(idempotency_key)
        payload: dict[str, Any] = {
            "address": address,
            "amount": amount,
            "merchant_reference": merchant_reference,
        }
        if confirmations is not None:
            payload["confirmations"] = int(confirmations)
        if expires_in is not None:
            payload["expires_in"] = int(expires_in)
        if label is not None:
            payload["label"] = label
        if message is not None:
            payload["message"] = message

        result = self._request_json(
            "POST",
            "/api/v1/payments",
            payload=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        self._validate_merchant_payment(result, merchant_reference=merchant_reference)
        return result

    def recover_payment(self, merchant_reference: str) -> dict[str, Any] | None:
        merchant_reference = _require_merchant_reference(merchant_reference)
        query = urlencode({"merchant_reference": merchant_reference, "limit": 2})
        result = self._request_json("GET", f"/api/v1/payments?{query}")
        payments = result.get("payments")
        if not isinstance(payments, list):
            raise MerchantApiError(502, "invalid_payment_list_response")
        if not payments:
            return None
        if len(payments) != 1 or not isinstance(payments[0], dict):
            raise MerchantApiError(502, "merchant_reference_not_unique")
        payment = payments[0]
        self._validate_merchant_payment(payment, merchant_reference=merchant_reference)
        return payment

    @staticmethod
    def _validate_merchant_payment(
        payment: Mapping[str, Any],
        *,
        merchant_reference: str,
    ) -> None:
        if payment.get("merchant_reference") != merchant_reference:
            raise MerchantApiError(502, "merchant_reference_mismatch")
        payment_id = payment.get("payment_id")
        if not isinstance(payment_id, str):
            raise MerchantApiError(502, "invalid_payment_response")
        try:
            _require_payment_id(payment_id)
        except ValueError as exc:
            raise MerchantApiError(502, "invalid_payment_response") from exc
        if not isinstance(payment.get("status"), str):
            raise MerchantApiError(502, "invalid_payment_response")
        if not isinstance(payment.get("version"), int):
            raise MerchantApiError(502, "invalid_payment_response")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def verify_webhook(
    *,
    headers: Mapping[str, str],
    raw_body: bytes,
    signing_secret: str,
    now: int | None = None,
    replay_window_seconds: int = DEFAULT_WEBHOOK_REPLAY_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Verify one PEPEW webhook before JSON is trusted.

    Signature input is:
        <timestamp> + "." + <event_id> + "." + <exact body bytes>
    """

    if not signing_secret:
        raise WebhookVerificationError("webhook_signing_secret_missing")
    if replay_window_seconds < 1:
        raise ValueError("replay_window_seconds must be positive")

    event_id = _header(headers, "X-PepewPay-Event-Id")
    delivery_id = _header(headers, "X-PepewPay-Delivery-Id")
    timestamp_raw = _header(headers, "X-PepewPay-Timestamp")
    signature = _header(headers, "X-PepewPay-Signature")
    if not all((event_id, delivery_id, timestamp_raw, signature)):
        raise WebhookVerificationError("webhook_headers_missing")

    try:
        timestamp = int(timestamp_raw)
    except (TypeError, ValueError):
        raise WebhookVerificationError("webhook_timestamp_invalid") from None

    observed_now = int(time.time()) if now is None else int(now)
    if abs(observed_now - timestamp) > int(replay_window_seconds):
        raise WebhookVerificationError("webhook_timestamp_outside_replay_window")

    message = (
        str(timestamp).encode("ascii")
        + b"."
        + event_id.encode("utf-8")
        + b"."
        + raw_body
    )
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    expected = f"v1={digest}"
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("webhook_signature_invalid")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WebhookVerificationError("webhook_body_invalid") from None
    if not isinstance(event, dict):
        raise WebhookVerificationError("webhook_body_invalid")
    if event.get("schema_version") != 1:
        raise WebhookVerificationError("webhook_schema_unsupported")
    if event.get("event_id") != event_id:
        raise WebhookVerificationError("webhook_event_id_mismatch")
    if not isinstance(event.get("event_type"), str):
        raise WebhookVerificationError("webhook_event_type_invalid")
    if not isinstance(event.get("payment_id"), str):
        raise WebhookVerificationError("webhook_payment_id_invalid")
    if not isinstance(event.get("payment_version"), int):
        raise WebhookVerificationError("webhook_payment_version_invalid")
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("status"), str):
        raise WebhookVerificationError("webhook_payment_data_invalid")
    return event


class ReferenceMerchantStore:
    """Tiny merchant-side SQLite store used only by this reference example."""

    def __init__(self, path: str):
        self.path = path
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS merchant_orders (
                    order_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payment_id TEXT UNIQUE,
                    payment_status TEXT,
                    payment_version INTEGER,
                    checkout_url TEXT,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL,
                    payment_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def reserve_order(
        self,
        order_id: str,
        idempotency_key: str,
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        order_id = _require_merchant_reference(order_id)
        idempotency_key = _require_idempotency_key(idempotency_key)
        timestamp = int(time.time()) if now is None else int(now)

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM merchant_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO merchant_orders (
                        order_id, idempotency_key, updated_at
                    ) VALUES (?, ?, ?)
                    """,
                    (order_id, idempotency_key, timestamp),
                )
            elif existing["idempotency_key"] != idempotency_key:
                raise MerchantStateError("order_idempotency_key_conflict")

        return self.get_order(order_id)

    def bind_payment(
        self,
        order_id: str,
        payment: Mapping[str, Any],
        *,
        checkout_base: str = DEFAULT_CHECKOUT_BASE,
        now: int | None = None,
    ) -> dict[str, Any]:
        order_id = _require_merchant_reference(order_id)
        if payment.get("merchant_reference") != order_id:
            raise MerchantStateError("merchant_reference_mismatch")
        payment_id = payment.get("payment_id")
        status = payment.get("status")
        version = payment.get("version")
        if not isinstance(payment_id, str) or not isinstance(status, str) or not isinstance(version, int):
            raise MerchantStateError("invalid_payment_response")
        checkout_url = build_checkout_url(payment_id, checkout_base=checkout_base)
        timestamp = int(time.time()) if now is None else int(now)

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payment_id FROM merchant_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                raise UnknownMerchantOrderError("unknown_merchant_order")
            if row["payment_id"] not in (None, payment_id):
                raise MerchantStateError("order_payment_id_conflict")
            connection.execute(
                """
                UPDATE merchant_orders
                SET payment_id = ?,
                    payment_status = ?,
                    payment_version = ?,
                    checkout_url = ?,
                    updated_at = ?
                WHERE order_id = ?
                """,
                (payment_id, status, version, checkout_url, timestamp, order_id),
            )

        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM merchant_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        if row is None:
            raise UnknownMerchantOrderError("unknown_merchant_order")
        return dict(row)

    def apply_verified_event(
        self,
        event: Mapping[str, Any],
        *,
        received_at: int | None = None,
    ) -> str:
        """Durably deduplicate and apply a signature-verified webhook event.

        A higher payment_version always wins, even if its status appears to move
        backward because of a reorg. Never rank statuses as monotonic.
        """

        event_id = event.get("event_id")
        event_type = event.get("event_type")
        payment_id = event.get("payment_id")
        payment_version = event.get("payment_version")
        data = event.get("data")
        if (
            not isinstance(event_id, str)
            or not isinstance(event_type, str)
            or not isinstance(payment_id, str)
            or not isinstance(payment_version, int)
            or not isinstance(data, Mapping)
            or not isinstance(data.get("status"), str)
        ):
            raise MerchantStateError("invalid_verified_event")

        merchant_reference = data.get("merchant_reference")
        if merchant_reference is not None and not isinstance(merchant_reference, str):
            raise MerchantStateError("invalid_merchant_reference")
        timestamp = int(time.time()) if received_at is None else int(received_at)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                "SELECT * FROM merchant_orders WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
            if order is None and merchant_reference is not None:
                order = connection.execute(
                    "SELECT * FROM merchant_orders WHERE order_id = ?",
                    (merchant_reference,),
                ).fetchone()
            if order is None:
                raise UnknownMerchantOrderError("unknown_merchant_order")
            if merchant_reference is not None and merchant_reference != order["order_id"]:
                raise MerchantStateError("merchant_reference_mismatch")
            if order["payment_id"] not in (None, payment_id):
                raise MerchantStateError("order_payment_id_conflict")

            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO webhook_events (
                    event_id, payment_id, payment_version, event_type, received_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, payment_id, payment_version, event_type, timestamp),
            )
            if inserted.rowcount == 0:
                connection.commit()
                return "duplicate"

            current_version = order["payment_version"]
            if current_version is None or payment_version > int(current_version):
                connection.execute(
                    """
                    UPDATE merchant_orders
                    SET payment_id = ?,
                        payment_status = ?,
                        payment_version = ?,
                        checkout_url = COALESCE(checkout_url, ?),
                        updated_at = ?
                    WHERE order_id = ?
                    """,
                    (
                        payment_id,
                        data["status"],
                        payment_version,
                        build_checkout_url(payment_id),
                        timestamp,
                        order["order_id"],
                    ),
                )
                connection.commit()
                return "applied"

            connection.commit()
            return "stale"
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
