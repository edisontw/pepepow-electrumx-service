#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

REQUIRED_TABLES = (
    "payments",
    "payment_transactions",
    "events",
    "webhook_endpoints",
    "webhook_deliveries",
)

OPTIONAL_TABLES = (
    "payment_idempotency_keys",
)

MANIFEST_VERSION = 1
PHASE_K_SCHEMA_PROFILE = "phase_k_merchant_v1"
LEGACY_SCHEMA_PROFILE = "legacy_payment_v1"

PHASE_K_TABLES = (
    "merchants",
    "merchant_credentials",
)

PHASE_K_OWNERSHIP_COLUMNS = {
    "payments": "merchant_id",
    "payment_idempotency_keys": "merchant_id",
    "events": "merchant_id",
    "webhook_endpoints": "merchant_id",
}


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _phase_k_schema_present(
    connection: sqlite3.Connection,
    tables: set[str],
) -> bool:
    if any(table in tables for table in PHASE_K_TABLES):
        return True
    for table, column in PHASE_K_OWNERSHIP_COLUMNS.items():
        if table in tables and column in _table_columns(connection, table):
            return True
    return False


def _phase_k_summary(
    connection: sqlite3.Connection,
    tables: set[str],
) -> dict[str, Any]:
    missing_tables = [table for table in PHASE_K_TABLES if table not in tables]
    if missing_tables:
        raise RuntimeError(
            "incomplete Phase K merchant schema: missing "
            + ",".join(missing_tables)
        )

    missing_columns: list[str] = []
    for table, column in PHASE_K_OWNERSHIP_COLUMNS.items():
        if table not in tables:
            missing_columns.append(f"{table}.{column}")
            continue
        if column not in _table_columns(connection, table):
            missing_columns.append(f"{table}.{column}")
    if missing_columns:
        raise RuntimeError(
            "incomplete Phase K merchant schema: missing "
            + ",".join(missing_columns)
        )

    merchant_columns = _table_columns(connection, "merchants")
    credential_columns = _table_columns(connection, "merchant_credentials")
    for required in ("merchant_id", "enabled"):
        if required not in merchant_columns:
            raise RuntimeError(
                f"incomplete Phase K merchant schema: missing merchants.{required}"
            )
    for required in ("credential_id", "merchant_id", "token_hash", "enabled"):
        if required not in credential_columns:
            raise RuntimeError(
                "incomplete Phase K merchant schema: missing "
                f"merchant_credentials.{required}"
            )

    checks = {
        "payment_owner_orphans": connection.execute(
            """
            SELECT COUNT(*)
            FROM payments AS p
            LEFT JOIN merchants AS m ON m.merchant_id = p.merchant_id
            WHERE p.merchant_id IS NULL
               OR p.merchant_id = ''
               OR m.merchant_id IS NULL
            """
        ).fetchone()[0],
        "idempotency_owner_orphans": connection.execute(
            """
            SELECT COUNT(*)
            FROM payment_idempotency_keys AS i
            LEFT JOIN merchants AS m ON m.merchant_id = i.merchant_id
            WHERE i.merchant_id IS NULL
               OR i.merchant_id = ''
               OR m.merchant_id IS NULL
            """
        ).fetchone()[0],
        "idempotency_payment_owner_mismatch": connection.execute(
            """
            SELECT COUNT(*)
            FROM payment_idempotency_keys AS i
            JOIN payments AS p ON p.payment_id = i.payment_id
            WHERE i.merchant_id != p.merchant_id
            """
        ).fetchone()[0],
        "event_owner_orphans": connection.execute(
            """
            SELECT COUNT(*)
            FROM events AS e
            LEFT JOIN merchants AS m ON m.merchant_id = e.merchant_id
            WHERE e.merchant_id IS NULL
               OR e.merchant_id = ''
               OR m.merchant_id IS NULL
            """
        ).fetchone()[0],
        "event_payment_owner_mismatch": connection.execute(
            """
            SELECT COUNT(*)
            FROM events AS e
            JOIN payments AS p ON p.payment_id = e.payment_id
            WHERE e.merchant_id != p.merchant_id
            """
        ).fetchone()[0],
        "endpoint_owner_orphans": connection.execute(
            """
            SELECT COUNT(*)
            FROM webhook_endpoints AS w
            LEFT JOIN merchants AS m ON m.merchant_id = w.merchant_id
            WHERE w.merchant_id IS NULL
               OR w.merchant_id = ''
               OR m.merchant_id IS NULL
            """
        ).fetchone()[0],
        "credential_owner_orphans": connection.execute(
            """
            SELECT COUNT(*)
            FROM merchant_credentials AS c
            LEFT JOIN merchants AS m ON m.merchant_id = c.merchant_id
            WHERE c.merchant_id IS NULL
               OR c.merchant_id = ''
               OR m.merchant_id IS NULL
            """
        ).fetchone()[0],
        "delivery_owner_mismatch": connection.execute(
            """
            SELECT COUNT(*)
            FROM webhook_deliveries AS d
            JOIN events AS e ON e.event_id = d.event_id
            JOIN webhook_endpoints AS w ON w.endpoint_id = d.endpoint_id
            WHERE e.merchant_id != w.merchant_id
            """
        ).fetchone()[0],
    }
    failed = [name for name, count in checks.items() if int(count) != 0]
    if failed:
        raise RuntimeError(
            "Phase K merchant ownership check failed: " + ",".join(failed)
        )
    return {
        "schema_profile": PHASE_K_SCHEMA_PROFILE,
        "merchant_ownership_checks": {
            key: int(value)
            for key, value in checks.items()
        },
    }


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def database_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError("SQLite integrity check failed")

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = [table for table in REQUIRED_TABLES if table not in tables]
    if missing:
        raise RuntimeError("missing required tables: " + ",".join(missing))

    phase_k = _phase_k_schema_present(connection, tables)
    profile_summary = (
        _phase_k_summary(connection, tables)
        if phase_k
        else {"schema_profile": LEGACY_SCHEMA_PROFILE}
    )

    counted = list(REQUIRED_TABLES)
    counted.extend(table for table in OPTIONAL_TABLES if table in tables)
    if phase_k:
        counted.extend(table for table in PHASE_K_TABLES if table in tables)
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in counted
    }
    enabled_webhooks = connection.execute(
        "SELECT COUNT(*) FROM webhook_endpoints WHERE enabled = 1"
    ).fetchone()[0]

    return {
        "integrity_check": "ok",
        **profile_summary,
        "table_counts": counts,
        "enabled_webhook_endpoints": enabled_webhooks,
    }


def _reserve_path(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)


def create_backup(source: Path, output: Path, manifest: Path) -> dict[str, Any]:
    source = source.expanduser()
    output = output.expanduser()
    manifest = manifest.expanduser()

    if not source.exists():
        raise RuntimeError("source payment database does not exist")
    if source.resolve() == output.resolve():
        raise RuntimeError("backup output must differ from source database")

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    temp_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    temp_manifest = manifest.with_name(f".{manifest.name}.tmp-{os.getpid()}")
    reserved_output = False
    reserved_manifest = False

    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None

    try:
        _reserve_path(output)
        reserved_output = True
        _reserve_path(manifest)
        reserved_manifest = True

        source_uri = f"file:{source.resolve()}?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=5.0)
        # Validate the live source before taking the backup. Its counts are not
        # compared afterward because concurrent production writes may legitimately
        # advance while SQLite creates a consistent point-in-time backup.
        database_summary(source_connection)

        destination_connection = sqlite3.connect(temp_output)
        source_connection.backup(destination_connection, pages=256, sleep=0.05)
        destination_connection.commit()
        backup_summary = database_summary(destination_connection)

        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None

        os.chmod(temp_output, 0o600)
        checksum = sha256_file(temp_output)
        metadata = {
            "format_version": MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "backup_file": output.name,
            "sha256": checksum,
            "size_bytes": temp_output.stat().st_size,
            **backup_summary,
        }

        temp_manifest.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temp_manifest, 0o600)

        os.replace(temp_output, output)
        os.replace(temp_manifest, manifest)
        return metadata
    except Exception:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        temp_output.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)
        if reserved_output:
            output.unlink(missing_ok=True)
        if reserved_manifest:
            manifest.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a verified online SQLite backup of the authoritative PEPEW "
            "Payment Platform database. This uses SQLite's backup API and does not "
            "require stopping pepew-pay.service."
        )
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New backup .sqlite3 path. Existing files are never overwritten.",
    )
    parser.add_argument(
        "--manifest",
        help=(
            "Manifest path. Defaults to <output>.manifest.json. Existing files are "
            "never overwritten."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help="Backend environment file used only to locate PAYMENT_DB_PATH.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser()
    if not env_path.exists():
        print("BACKUP: FAIL: backend environment file not found", file=sys.stderr)
        return 1

    env = read_env(env_path)
    source_raw = env.get("PAYMENT_DB_PATH")
    if not source_raw:
        print("BACKUP: FAIL: PAYMENT_DB_PATH is not configured", file=sys.stderr)
        return 1

    output = Path(args.output).expanduser()
    manifest = (
        Path(args.manifest).expanduser()
        if args.manifest
        else Path(str(output) + ".manifest.json")
    )

    try:
        metadata = create_backup(Path(source_raw), output, manifest)
    except FileExistsError:
        print("BACKUP: FAIL: output or manifest already exists", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"BACKUP: FAIL: {exc}", file=sys.stderr)
        return 1

    print("integrity_check=ok")
    print(f"schema_profile={metadata['schema_profile']}")
    for table, count in metadata["table_counts"].items():
        print(f"{table}={count}")
    print(f"enabled_webhook_endpoints={metadata['enabled_webhook_endpoints']}")
    print(f"size_bytes={metadata['size_bytes']}")
    print(f"sha256={metadata['sha256']}")
    print("BACKUP: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
