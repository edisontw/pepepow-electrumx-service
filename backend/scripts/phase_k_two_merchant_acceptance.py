#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.merchant_store import (  # noqa: E402
    LEGACY_MERCHANT_ID,
    MerchantStore,
)
from payment_db_backup import (  # noqa: E402
    PHASE_K_SCHEMA_PROFILE,
    database_summary,
    read_env,
)


ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_API_BASE = "https://pay.pepepow.net"
TEST_AMOUNT = "0.00000001"
HEAVY_REQUEST_MIN_INTERVAL_SECONDS = 0.55
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = 1.0


class AcceptanceError(RuntimeError):
    pass


class HeavyRequestController:
    def __init__(
        self,
        *,
        min_interval: float = HEAVY_REQUEST_MIN_INTERVAL_SECONDS,
        max_rate_limit_retries: int = MAX_RATE_LIMIT_RETRIES,
        backoff_seconds: float = RATE_LIMIT_BACKOFF_SECONDS,
    ) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.max_rate_limit_retries = max(0, int(max_rate_limit_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._last_request_at: float | None = None

    def _pace(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _retry_after_seconds(headers: dict[str, str]) -> float | None:
        for key, value in headers.items():
            if key.lower() != "retry-after":
                continue
            try:
                parsed = float(value.strip())
            except (TypeError, ValueError):
                return None
            return max(0.0, parsed)
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        for attempt in range(self.max_rate_limit_retries + 1):
            self._pace()
            status, payload, response_headers = json_request(
                method,
                url,
                headers=headers,
                json_body=json_body,
                timeout=timeout,
            )
            self._last_request_at = time.monotonic()
            if status != 429:
                return status, payload, response_headers
            if attempt >= self.max_rate_limit_retries:
                return status, payload, response_headers

            retry_after = self._retry_after_seconds(response_headers)
            backoff = self.backoff_seconds * (2 ** attempt)
            time.sleep(max(self.min_interval, retry_after or 0.0, backoff))

        raise AssertionError("unreachable")


HEAVY_REQUESTS = HeavyRequestController()


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


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
        data = json.dumps(
            json_body,
            separators=(",", ":"),
        ).encode("utf-8")
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
        if status == 429:
            payload = {}
        else:
            raise AcceptanceError(
                f"{method} request returned non-JSON HTTP {status}"
            ) from exc
    if not isinstance(payload, dict):
        raise AcceptanceError("HTTP JSON response was not an object")
    return status, payload, response_headers


def error_code(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def load_secret(path: Path) -> str:
    if not path.is_file():
        raise AcceptanceError("scoped credential secret file does not exist")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise AcceptanceError(
            "scoped credential secret file must not be group/world accessible"
        )
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise AcceptanceError("scoped credential secret file is invalid")
    return token


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise AcceptanceError("payment database does not exist")
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
        raise AcceptanceError("no existing payment address is available")
    return str(row["address"])


def payment_created_recipient_count(
    db_path: Path,
    *,
    merchant_ids: set[str],
) -> int:
    if not merchant_ids:
        return 0

    connection = open_readonly(db_path)
    try:
        placeholders = ",".join("?" for _ in merchant_ids)
        rows = connection.execute(
            f"""
            SELECT merchant_id, event_types_json
            FROM webhook_endpoints
            WHERE enabled = 1
              AND merchant_id IN ({placeholders})
            """,
            tuple(sorted(merchant_ids)),
        ).fetchall()
    finally:
        connection.close()

    recipients = 0
    for row in rows:
        raw_types = row["event_types_json"]
        if raw_types is None:
            recipients += 1
            continue
        if not isinstance(raw_types, str):
            continue
        try:
            event_types = json.loads(raw_types)
        except json.JSONDecodeError:
            continue
        if isinstance(event_types, list) and "payment.created" in event_types:
            recipients += 1
    return recipients


def find_existing_acceptance_pair(
    db_path: Path,
    *,
    merchant_id_a: str,
    merchant_id_b: str,
) -> tuple[str, str, str, str]:
    connection = open_readonly(db_path)
    try:
        rows = connection.execute(
            """
            SELECT
                p.payment_id,
                p.merchant_id,
                p.merchant_reference,
                p.created_at,
                i.idempotency_key
            FROM payments AS p
            JOIN payment_idempotency_keys AS i
              ON i.payment_id = p.payment_id
             AND i.merchant_id = p.merchant_id
            WHERE p.merchant_id IN (?, ?)
              AND p.merchant_reference LIKE 'phase-k-acceptance/%'
            ORDER BY p.created_at DESC, p.payment_id DESC
            """,
            (merchant_id_a, merchant_id_b),
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[tuple[str, str], dict[str, tuple[str, int]]] = {}
    for row in rows:
        reference = row["merchant_reference"]
        idempotency_key = row["idempotency_key"]
        if not isinstance(reference, str) or not isinstance(idempotency_key, str):
            continue
        key = (reference, idempotency_key)
        grouped.setdefault(key, {})[str(row["merchant_id"])] = (
            str(row["payment_id"]),
            int(row["created_at"]),
        )

    candidates: list[tuple[int, str, str, str, str]] = []
    for (reference, idempotency_key), by_merchant in grouped.items():
        if merchant_id_a not in by_merchant or merchant_id_b not in by_merchant:
            continue
        payment_a, created_a = by_merchant[merchant_id_a]
        payment_b, created_b = by_merchant[merchant_id_b]
        if payment_a == payment_b:
            continue
        candidates.append(
            (
                max(created_a, created_b),
                payment_a,
                payment_b,
                reference,
                idempotency_key,
            )
        )

    if not candidates:
        raise AcceptanceError(
            "no completed two-merchant acceptance payment pair is available to resume"
        )

    _created_at, payment_a, payment_b, reference, idempotency_key = max(
        candidates,
        key=lambda item: item[0],
    )
    return payment_a, payment_b, reference, idempotency_key


def verify_payment_event_owner(
    db_path: Path,
    *,
    payment_id: str,
    merchant_id: str,
) -> None:
    connection = open_readonly(db_path)
    try:
        payment = connection.execute(
            """
            SELECT merchant_id
            FROM payments
            WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()
        require(payment is not None, "acceptance payment is missing from SQLite")
        require(
            str(payment["merchant_id"]) == merchant_id,
            "acceptance payment has wrong merchant owner",
        )

        created = connection.execute(
            """
            SELECT merchant_id
            FROM events
            WHERE payment_id = ? AND event_type = 'payment.created'
            ORDER BY rowid
            """,
            (payment_id,),
        ).fetchall()
        require(
            len(created) == 1,
            "acceptance payment does not have exactly one payment.created event",
        )
        require(
            str(created[0]["merchant_id"]) == merchant_id,
            "payment.created event has wrong merchant owner",
        )
    finally:
        connection.close()


def verify_global_ownership(db_path: Path) -> None:
    connection = open_readonly(db_path)
    try:
        try:
            summary = database_summary(connection)
        except RuntimeError as exc:
            raise AcceptanceError(
                "merchant ownership consistency check failed"
            ) from exc
    finally:
        connection.close()
    require(
        summary.get("schema_profile") == PHASE_K_SCHEMA_PROFILE,
        "Phase K schema profile is not active",
    )
    require(
        set(summary.get("merchant_ownership_checks", {}).values()) == {0},
        "merchant ownership consistency check failed",
    )


def one_reference_payment(
    api_base: str,
    *,
    auth: dict[str, str],
    payment_address: str,
    merchant_reference: str,
    idempotency_key: str,
) -> str:
    body = {
        "address": payment_address,
        "amount": TEST_AMOUNT,
        "confirmations": 1,
        "expires_in": 600,
        "label": "Phase K production acceptance",
        "message": "Multi-merchant isolation test; intentionally unfunded",
        "merchant_reference": merchant_reference,
    }
    headers = {
        **auth,
        "Idempotency-Key": idempotency_key,
    }
    status, created, _ = HEAVY_REQUESTS.request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=headers,
        json_body=body,
    )
    require(
        status == 201,
        f"payment create failed: HTTP {status} {error_code(created)}",
    )
    payment_id = created.get("payment_id")
    require(
        isinstance(payment_id, str) and bool(payment_id),
        "payment create response is missing payment_id",
    )
    require(
        created.get("merchant_reference") == merchant_reference,
        "payment create response lost merchant_reference",
    )
    require(
        created.get("idempotency_key") == idempotency_key,
        "payment create response lost idempotency metadata",
    )

    status, replay, _ = HEAVY_REQUESTS.request(
        "POST",
        f"{api_base}/api/v1/payments",
        headers=headers,
        json_body=body,
    )
    require(
        status == 201 and replay.get("payment_id") == payment_id,
        "idempotent replay did not return the original merchant payment",
    )
    return payment_id


def recover_exact(
    api_base: str,
    *,
    auth: dict[str, str],
    merchant_reference: str,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "merchant_reference": merchant_reference,
            "limit": 10,
        }
    )
    status, payload, _ = HEAVY_REQUESTS.request(
        "GET",
        f"{api_base}/api/v1/payments?{query}",
        headers=auth,
    )
    payments = payload.get("payments")
    require(
        status == 200 and isinstance(payments, list),
        f"merchant recovery failed: HTTP {status}",
    )
    return [
        item
        for item in payments
        if isinstance(item, dict)
    ]


def endpoint_ids(
    api_base: str,
    *,
    auth: dict[str, str],
) -> set[str]:
    status, payload, _ = HEAVY_REQUESTS.request(
        "GET",
        f"{api_base}/api/v1/webhook-endpoints",
        headers=auth,
    )
    endpoints = payload.get("endpoints")
    require(
        status == 200 and isinstance(endpoints, list),
        f"webhook endpoint listing failed: HTTP {status}",
    )
    return {
        str(item["endpoint_id"])
        for item in endpoints
        if isinstance(item, dict) and isinstance(item.get("endpoint_id"), str)
    }


def delivery_ids(
    api_base: str,
    *,
    auth: dict[str, str],
) -> set[str]:
    status, payload, _ = HEAVY_REQUESTS.request(
        "GET",
        f"{api_base}/api/v1/webhook-deliveries?limit=500",
        headers=auth,
    )
    deliveries = payload.get("deliveries")
    require(
        status == 200 and isinstance(deliveries, list),
        f"webhook delivery listing failed: HTTP {status}",
    )
    return {
        str(item["delivery_id"])
        for item in deliveries
        if isinstance(item, dict) and isinstance(item.get("delivery_id"), str)
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded two-merchant Phase K production isolation acceptance. "
            "Creates two unfunded one-atom payments using the same merchant "
            "reference/idempotency key in two temporary scoped merchant namespaces."
        )
    )
    parser.add_argument(
        "--scoped-secret-file-a",
        required=True,
        help="Mode-0600 file containing temporary scoped credential A.",
    )
    parser.add_argument(
        "--scoped-secret-file-b",
        required=True,
        help="Mode-0600 file containing temporary scoped credential B.",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--env-file", default=str(ENV_PATH))
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Reuse the latest already-persisted acceptance payment/idempotency "
            "pair for these two merchants instead of creating new payments."
        ),
    )
    args = parser.parse_args()

    try:
        env_path = Path(args.env_file).expanduser()
        require(env_path.exists(), "backend environment file not found")
        env = read_env(env_path)
        require(
            truthy(env.get("PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED")),
            "scoped merchant auth is not enabled",
        )
        require(
            truthy(env.get("PAYMENT_API_ENABLED")),
            "PAYMENT_API_ENABLED is not true",
        )
        require(
            truthy(env.get("PAYMENT_WEBHOOK_ENABLED")),
            "PAYMENT_WEBHOOK_ENABLED is not true",
        )

        legacy_key = (env.get("PAYMENT_CREATE_API_KEY") or "").strip()
        require(
            len(legacy_key) >= 32,
            "legacy PAYMENT_CREATE_API_KEY is not configured",
        )
        db_raw = env.get("PAYMENT_DB_PATH")
        require(bool(db_raw), "PAYMENT_DB_PATH is not configured")
        db_path = Path(str(db_raw)).expanduser()

        scoped_token_a = load_secret(
            Path(args.scoped_secret_file_a).expanduser()
        )
        scoped_token_b = load_secret(
            Path(args.scoped_secret_file_b).expanduser()
        )
        require(
            scoped_token_a != scoped_token_b,
            "acceptance credentials must be distinct",
        )

        store = MerchantStore(str(db_path))
        authenticated_a = store.authenticate_credential(scoped_token_a)
        authenticated_b = store.authenticate_credential(scoped_token_b)
        require(
            authenticated_a is not None and authenticated_b is not None,
            "both scoped credentials must authenticate against SQLite",
        )
        assert authenticated_a is not None
        assert authenticated_b is not None
        merchant_id_a = authenticated_a["merchant_id"]
        merchant_id_b = authenticated_b["merchant_id"]
        require(
            merchant_id_a != LEGACY_MERCHANT_ID
            and merchant_id_b != LEGACY_MERCHANT_ID,
            "acceptance credentials must belong to non-legacy merchants",
        )
        require(
            merchant_id_a != merchant_id_b,
            "acceptance credentials must belong to two merchants",
        )

        recipients = payment_created_recipient_count(
            db_path,
            merchant_ids={merchant_id_a, merchant_id_b},
        )
        require(
            recipients == 0,
            "acceptance merchants have enabled webhook endpoints that would "
            "receive payment.created",
        )

        verify_global_ownership(db_path)
        payment_address = latest_payment_address(db_path)

        api_base = args.api_base.rstrip("/")
        status, payload, _ = json_request(
            "GET",
            f"{api_base}/api/health",
        )
        require(
            status == 200 and payload.get("ok") is True,
            f"health check failed: HTTP {status}",
        )

        status, payload, _ = json_request(
            "GET",
            f"{api_base}/api/status",
        )
        electrumx = payload.get("electrumx")
        require(
            status == 200
            and payload.get("ok") is True
            and isinstance(electrumx, dict)
            and electrumx.get("connected") is True,
            f"ElectrumX status failed: HTTP {status}",
        )

        auth_a = {
            "Authorization": f"Bearer {scoped_token_a}",
        }
        auth_b = {
            "Authorization": f"Bearer {scoped_token_b}",
        }

        if args.resume_existing:
            (
                payment_id_a,
                payment_id_b,
                merchant_reference,
                idempotency_key,
            ) = find_existing_acceptance_pair(
                db_path,
                merchant_id_a=merchant_id_a,
                merchant_id_b=merchant_id_b,
            )
        else:
            suffix = f"{int(time.time())}-{secrets.token_hex(6)}"
            merchant_reference = f"phase-k-acceptance/{suffix}"
            idempotency_key = f"phase-k-acceptance:{suffix}"

            payment_id_a = one_reference_payment(
                api_base,
                auth=auth_a,
                payment_address=payment_address,
                merchant_reference=merchant_reference,
                idempotency_key=idempotency_key,
            )
            payment_id_b = one_reference_payment(
                api_base,
                auth=auth_b,
                payment_address=payment_address,
                merchant_reference=merchant_reference,
                idempotency_key=idempotency_key,
            )
            require(
                payment_id_a != payment_id_b,
                "two merchants unexpectedly received the same payment identity",
            )

        rows_a = recover_exact(
            api_base,
            auth=auth_a,
            merchant_reference=merchant_reference,
        )
        rows_b = recover_exact(
            api_base,
            auth=auth_b,
            merchant_reference=merchant_reference,
        )
        require(
            [row.get("payment_id") for row in rows_a] == [payment_id_a],
            "merchant A recovery crossed merchant boundary",
        )
        require(
            [row.get("payment_id") for row in rows_b] == [payment_id_b],
            "merchant B recovery crossed merchant boundary",
        )

        for payment_id in (payment_id_a, payment_id_b):
            quoted = urllib.parse.quote(payment_id, safe="")
            status, public, _ = HEAVY_REQUESTS.request(
                "GET",
                f"{api_base}/api/v1/payments/{quoted}",
            )
            require(
                status == 200 and public.get("payment_id") == payment_id,
                "public capability status failed",
            )
            for private_field in (
                "merchant_id",
                "merchant_reference",
                "idempotency_key",
                "credential_id",
            ):
                require(
                    private_field not in public,
                    f"public capability exposed {private_field}",
                )

        endpoints_a = endpoint_ids(api_base, auth=auth_a)
        endpoints_b = endpoint_ids(api_base, auth=auth_b)
        require(
            endpoints_a.isdisjoint(endpoints_b),
            "webhook endpoint listing crossed merchant boundary",
        )

        deliveries_a = delivery_ids(api_base, auth=auth_a)
        deliveries_b = delivery_ids(api_base, auth=auth_b)
        require(
            deliveries_a.isdisjoint(deliveries_b),
            "webhook delivery listing crossed merchant boundary",
        )

        verify_payment_event_owner(
            db_path,
            payment_id=payment_id_a,
            merchant_id=merchant_id_a,
        )
        verify_payment_event_owner(
            db_path,
            payment_id=payment_id_b,
            merchant_id=merchant_id_b,
        )
        verify_global_ownership(db_path)
    except Exception as exc:
        print(
            f"PHASE K TWO-MERCHANT ACCEPTANCE: FAIL: {exc}",
            file=sys.stderr,
        )
        return 1

    print("pay_health=pass")
    print("acceptance_payments=" + ("reused" if args.resume_existing else "created"))
    print("acceptance_payment_created_webhook_recipients=0")
    print("electrumx_status=connected")
    print("same_reference_across_merchants=pass")
    print("same_idempotency_key_across_merchants=pass")
    print("merchant_payment_recovery_isolation=pass")
    print("public_capability_privacy=pass")
    print("webhook_endpoint_listing_isolation=pass")
    print("webhook_delivery_listing_isolation=pass")
    print("payment_event_ownership=pass")
    print("global_delivery_owner_mismatch=0")
    print("PHASE K TWO-MERCHANT ACCEPTANCE: PASS")
    print(
        "Two unfunded one-atom acceptance payments were persisted and will "
        "expire normally; no credential or payment capability ID was printed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
