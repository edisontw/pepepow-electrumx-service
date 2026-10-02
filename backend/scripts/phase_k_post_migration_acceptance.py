#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from payment_db_backup import (  # noqa: E402
    PHASE_K_SCHEMA_PROFILE,
    database_summary,
    read_env,
)


ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_API_BASE = "https://pay.pepepow.net"


class AcceptanceError(RuntimeError):
    pass


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
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
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


def header_value(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def latest_payment_id(db_path: Path) -> str:
    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        row = connection.execute(
            """
            SELECT payment_id
            FROM payments
            ORDER BY created_at DESC, payment_id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise AcceptanceError("no existing payment is available for capability check")
    return str(row[0])


def verify_database(db_path: Path) -> None:
    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        summary = database_summary(connection)
        require(
            summary.get("schema_profile") == PHASE_K_SCHEMA_PROFILE,
            "Phase K schema profile is not active",
        )
        require(
            set(summary.get("merchant_ownership_checks", {}).values()) == {0},
            "merchant ownership checks did not pass",
        )

        owner_queries = (
            "SELECT COUNT(*) FROM payments WHERE merchant_id != 'mrc_legacy_v1'",
            "SELECT COUNT(*) FROM payment_idempotency_keys WHERE merchant_id != 'mrc_legacy_v1'",
            "SELECT COUNT(*) FROM events WHERE merchant_id != 'mrc_legacy_v1'",
            "SELECT COUNT(*) FROM webhook_endpoints WHERE merchant_id != 'mrc_legacy_v1'",
        )
        for query in owner_queries:
            require(
                int(connection.execute(query).fetchone()[0]) == 0,
                "unexpected non-legacy production ownership before scoped enable",
            )
        require(
            connection.execute("PRAGMA foreign_key_check").fetchall() == [],
            "foreign-key check failed",
        )
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Phase K post-migration acceptance while scoped merchant "
            "auth is still disabled."
        )
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--env-file", default=str(ENV_PATH))
    args = parser.parse_args()

    try:
        env_path = Path(args.env_file).expanduser()
        require(env_path.exists(), "backend environment file not found")
        env = read_env(env_path)

        require(
            not truthy(env.get("PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED")),
            "scoped merchant auth must still be disabled for this acceptance",
        )
        require(
            truthy(env.get("PAYMENT_API_ENABLED")),
            "PAYMENT_API_ENABLED is not true",
        )
        require(
            truthy(env.get("PAYMENT_WATCHER_ENABLED")),
            "PAYMENT_WATCHER_ENABLED is not true",
        )

        legacy_key = (env.get("PAYMENT_CREATE_API_KEY") or "").strip()
        require(
            len(legacy_key) >= 32,
            "legacy PAYMENT_CREATE_API_KEY is not configured",
        )
        db_raw = env.get("PAYMENT_DB_PATH")
        require(bool(db_raw), "PAYMENT_DB_PATH is not configured")
        db_path = Path(str(db_raw)).expanduser()
        require(db_path.exists(), "payment database does not exist")

        verify_database(db_path)
        payment_id = latest_payment_id(db_path)

        api_base = args.api_base.rstrip("/")
        status, payload, _ = json_request("GET", f"{api_base}/api/health")
        require(
            status == 200 and payload.get("ok") is True,
            f"health check failed: HTTP {status}",
        )

        status, payload, _ = json_request("GET", f"{api_base}/api/status")
        electrumx = payload.get("electrumx")
        require(
            status == 200
            and payload.get("ok") is True
            and isinstance(electrumx, dict)
            and electrumx.get("connected") is True,
            f"ElectrumX status failed: HTTP {status}",
        )

        status, payload, headers = json_request(
            "GET",
            f"{api_base}/api/v1/payments?limit=1",
        )
        require(
            status == 401 and error_code(payload) == "payment_auth_required",
            "anonymous merchant listing was not rejected",
        )
        require(
            (header_value(headers, "WWW-Authenticate") or "").lower() == "bearer",
            "anonymous listing did not advertise Bearer auth",
        )

        auth = {"Authorization": f"Bearer {legacy_key}"}
        status, payload, _ = json_request(
            "GET",
            f"{api_base}/api/v1/payments?limit=1",
            headers=auth,
        )
        require(
            status == 200 and payload.get("ok") is True,
            f"legacy authenticated listing failed: HTTP {status}",
        )

        quoted = urllib.parse.quote(payment_id, safe="")
        status, public, _ = json_request(
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
    except Exception as exc:
        print(f"PHASE K POST-MIGRATION ACCEPTANCE: FAIL: {exc}", file=sys.stderr)
        return 1

    print("schema_profile=phase_k_merchant_v1")
    print("merchant_ownership_checks=pass")
    print("legacy_rows_owned_by=mrc_legacy_v1")
    print("foreign_key_check=ok")
    print("pay_health=pass")
    print("electrumx_status=connected")
    print("anonymous_merchant_listing=blocked")
    print("legacy_merchant_auth=pass")
    print("public_capability_privacy=pass")
    print("scoped_merchant_auth=still_disabled")
    print("PHASE K POST-MIGRATION ACCEPTANCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
