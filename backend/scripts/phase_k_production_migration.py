#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.merchant_store import LEGACY_MERCHANT_ID  # noqa: E402
from app.services.payment_store import PaymentStore  # noqa: E402
from payment_db_backup import (  # noqa: E402
    PHASE_K_SCHEMA_PROFILE,
    database_summary,
    read_env,
)
from payment_db_offhost_sender import (  # noqa: E402
    select_latest_complete_pair,
    validate_source_pair,
)


ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_BACKUP_DIR = Path("/var/lib/pepew-pay/backups")
AUTHORITY_TABLES = (
    "payments",
    "payment_idempotency_keys",
    "payment_baseline_transactions",
    "payment_transactions",
    "events",
    "webhook_endpoints",
    "webhook_deliveries",
    "chain_state",
)


class MigrationError(RuntimeError):
    pass


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def require_service_stopped(service_name: str) -> None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise MigrationError("could not verify service state") from exc
    if result.returncode == 0:
        raise MigrationError(
            f"refusing migration while {service_name} is active"
        )


def _fingerprint_rows(
    connection: sqlite3.Connection,
    query: str,
) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(query).fetchall():
        for value in row:
            encoded = b"" if value is None else str(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def authority_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MigrationError("payment database does not exist")
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError("pre-migration SQLite integrity check failed")

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [table for table in AUTHORITY_TABLES if table not in tables]
        if missing:
            raise MigrationError(
                "pre-migration database is missing required tables: "
                + ",".join(missing)
            )

        counts = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in AUTHORITY_TABLES
        }
        event_fingerprint = _fingerprint_rows(
            connection,
            """
            SELECT event_id, payload_json
            FROM events
            ORDER BY event_id
            """,
        )
        endpoint_fingerprint = _fingerprint_rows(
            connection,
            """
            SELECT endpoint_id, url, event_types_json, enabled,
                   created_at, updated_at
            FROM webhook_endpoints
            ORDER BY endpoint_id
            """,
        )
        payment_fingerprint = _fingerprint_rows(
            connection,
            """
            SELECT payment_id, address, scripthash, amount_sats,
                   confirmations_required, created_at, created_height,
                   expires_at, status, version, received_sats,
                   confirmed_sats, policy_confirmed_sats,
                   label, message, merchant_reference, updated_at
            FROM payments
            ORDER BY payment_id
            """,
        )
        return {
            "counts": counts,
            "event_fingerprint": event_fingerprint,
            "endpoint_fingerprint": endpoint_fingerprint,
            "payment_fingerprint": payment_fingerprint,
        }
    finally:
        connection.close()


def verify_post_migration(
    path: Path,
    before: dict[str, Any],
    *,
    require_legacy_only: bool,
) -> dict[str, Any]:
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        summary = database_summary(connection)
        if summary.get("schema_profile") != PHASE_K_SCHEMA_PROFILE:
            raise MigrationError("Phase K schema profile was not established")

        after_counts = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in AUTHORITY_TABLES
        }
        if after_counts != before["counts"]:
            raise MigrationError("authoritative row counts changed during migration")

        if _fingerprint_rows(
            connection,
            """
            SELECT event_id, payload_json
            FROM events
            ORDER BY event_id
            """,
        ) != before["event_fingerprint"]:
            raise MigrationError("historical event payloads changed during migration")

        if _fingerprint_rows(
            connection,
            """
            SELECT endpoint_id, url, event_types_json, enabled,
                   created_at, updated_at
            FROM webhook_endpoints
            ORDER BY endpoint_id
            """,
        ) != before["endpoint_fingerprint"]:
            raise MigrationError("webhook endpoint records changed during migration")

        if _fingerprint_rows(
            connection,
            """
            SELECT payment_id, address, scripthash, amount_sats,
                   confirmations_required, created_at, created_height,
                   expires_at, status, version, received_sats,
                   confirmed_sats, policy_confirmed_sats,
                   label, message, merchant_reference, updated_at
            FROM payments
            ORDER BY payment_id
            """,
        ) != before["payment_fingerprint"]:
            raise MigrationError("payment business data changed during migration")

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise MigrationError("foreign-key check failed after migration")

        if require_legacy_only:
            owner_checks = {
                "payments": """
                    SELECT COUNT(*) FROM payments
                    WHERE merchant_id != ?
                """,
                "payment_idempotency_keys": """
                    SELECT COUNT(*) FROM payment_idempotency_keys
                    WHERE merchant_id != ?
                """,
                "events": """
                    SELECT COUNT(*) FROM events
                    WHERE merchant_id != ?
                """,
                "webhook_endpoints": """
                    SELECT COUNT(*) FROM webhook_endpoints
                    WHERE merchant_id != ?
                """,
            }
            for label, query in owner_checks.items():
                count = int(
                    connection.execute(
                        query,
                        (LEGACY_MERCHANT_ID,),
                    ).fetchone()[0]
                )
                if count:
                    raise MigrationError(
                        f"unexpected non-legacy ownership in {label}"
                    )

        return summary
    finally:
        connection.close()


def migrate_database(
    path: Path,
    *,
    require_legacy_only: bool = True,
) -> dict[str, Any]:
    before = authority_snapshot(path)
    PaymentStore(str(path)).initialize()
    summary = verify_post_migration(
        path,
        before,
        require_legacy_only=require_legacy_only,
    )
    return {
        "before": before,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Perform the explicit Phase K merchant-ownership SQLite migration "
            "after a verified H2 recovery point. The Payment Platform service "
            "must be stopped. Scoped merchant auth remains disabled."
        )
    )
    parser.add_argument(
        "--service-name",
        default="pepew-pay.service",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
    )
    parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
    )
    parser.add_argument(
        "--allow-existing-scoped-data",
        action="store_true",
        help=(
            "Allow a rerun after non-legacy merchant rows exist. Do not use for "
            "the initial K5 production migration."
        ),
    )
    args = parser.parse_args()

    try:
        require_service_stopped(args.service_name)

        env_path = Path(args.env_file).expanduser()
        if not env_path.exists():
            raise MigrationError("backend environment file not found")
        env = read_env(env_path)

        if truthy(env.get("PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED")):
            raise MigrationError(
                "scoped merchant auth must remain disabled during migration"
            )
        legacy_key = (env.get("PAYMENT_CREATE_API_KEY") or "").strip()
        if len(legacy_key) < 32:
            raise MigrationError(
                "legacy PAYMENT_CREATE_API_KEY is not configured"
            )

        db_raw = env.get("PAYMENT_DB_PATH")
        if not db_raw:
            raise MigrationError("PAYMENT_DB_PATH is not configured")
        db_path = Path(db_raw).expanduser()

        backup_dir = Path(args.backup_dir).expanduser()
        backup_path, manifest_path = select_latest_complete_pair(backup_dir)
        validate_source_pair(backup_path, manifest_path)

        result = migrate_database(
            db_path,
            require_legacy_only=not args.allow_existing_scoped_data,
        )
        summary = result["summary"]
    except Exception as exc:
        print(f"PHASE K MIGRATION: FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"recovery_backup={backup_path.name}")
    print("local_recovery_restore_drill=pass")
    print(f"schema_profile={summary['schema_profile']}")
    print("sqlite_integrity=ok")
    print("foreign_key_check=ok")
    print("authoritative_row_counts=preserved")
    print("payment_business_data=preserved")
    print("historical_event_payloads=preserved")
    print("webhook_endpoint_records=preserved")
    print("merchant_ownership_checks=pass")
    print("scoped_merchant_auth=still_disabled")
    print("PHASE K MIGRATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
