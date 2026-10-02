#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


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


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("backup manifest is unreadable") from exc

    if data.get("format_version") != MANIFEST_VERSION:
        raise RuntimeError("unsupported backup manifest version")
    if not isinstance(data.get("backup_file"), str):
        raise RuntimeError("backup manifest is missing backup filename")
    if not isinstance(data.get("sha256"), str):
        raise RuntimeError("backup manifest is missing sha256")
    if not isinstance(data.get("size_bytes"), int):
        raise RuntimeError("backup manifest is missing byte size")
    if not isinstance(data.get("table_counts"), dict):
        raise RuntimeError("backup manifest is missing table counts")
    profile = data.get("schema_profile")
    if profile is not None and profile not in {
        LEGACY_SCHEMA_PROFILE,
        PHASE_K_SCHEMA_PROFILE,
    }:
        raise RuntimeError("unsupported backup schema profile")
    if profile == PHASE_K_SCHEMA_PROFILE and not isinstance(
        data.get("merchant_ownership_checks"),
        dict,
    ):
        raise RuntimeError("Phase K backup manifest is missing ownership checks")
    return data


def verify_against_manifest(
    backup: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if backup.name != manifest["backup_file"]:
        raise RuntimeError("backup filename does not match manifest")
    if backup.stat().st_size != manifest["size_bytes"]:
        raise RuntimeError("backup byte size does not match manifest")

    checksum = sha256_file(backup)
    if checksum.lower() != manifest["sha256"].lower():
        raise RuntimeError("backup SHA-256 does not match manifest")

    uri = f"file:{backup.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        summary = database_summary(connection)
    finally:
        connection.close()

    manifest_profile = manifest.get("schema_profile")
    if (
        manifest_profile is not None
        and summary["schema_profile"] != manifest_profile
    ):
        raise RuntimeError("backup schema profile does not match manifest")
    if summary["table_counts"] != manifest["table_counts"]:
        raise RuntimeError("backup table counts do not match manifest")
    if (
        summary["enabled_webhook_endpoints"]
        != manifest.get("enabled_webhook_endpoints")
    ):
        raise RuntimeError("backup webhook count does not match manifest")
    if (
        manifest_profile == PHASE_K_SCHEMA_PROFILE
        and summary.get("merchant_ownership_checks")
        != manifest.get("merchant_ownership_checks")
    ):
        raise RuntimeError("backup merchant ownership checks do not match manifest")
    return summary


def run_restore_drill(
    backup: Path,
    manifest_path: Path,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    backup = backup.expanduser()
    manifest_path = manifest_path.expanduser()

    if not backup.exists():
        raise RuntimeError("backup database does not exist")
    if not manifest_path.exists():
        raise RuntimeError("backup manifest does not exist")

    manifest = load_manifest(manifest_path)
    backup_summary = verify_against_manifest(backup, manifest)

    temp_parent = str(work_dir.expanduser()) if work_dir is not None else None
    with tempfile.TemporaryDirectory(prefix="pepew-restore-drill-", dir=temp_parent) as temp:
        restored = Path(temp) / "restored.sqlite3"

        source_uri = f"file:{backup.resolve()}?mode=ro"
        source = sqlite3.connect(source_uri, uri=True, timeout=5.0)
        destination = sqlite3.connect(restored)
        try:
            source.backup(destination, pages=256, sleep=0.05)
            destination.commit()
            restored_summary = database_summary(destination)
        finally:
            destination.close()
            source.close()

        if restored_summary != backup_summary:
            raise RuntimeError("restored database summary differs from backup")

    return backup_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Perform a non-destructive restore drill for a PEPEW Payment Platform "
            "SQLite backup. The drill verifies the manifest, restores into a "
            "temporary database, runs integrity checks, then removes the temporary "
            "copy. It never replaces the live PAYMENT_DB_PATH."
        )
    )
    parser.add_argument("backup", help="Backup .sqlite3 file to rehearse.")
    parser.add_argument(
        "--manifest",
        help="Manifest path. Defaults to <backup>.manifest.json.",
    )
    parser.add_argument(
        "--work-dir",
        help="Optional parent directory for the temporary restore drill.",
    )
    args = parser.parse_args()

    backup = Path(args.backup)
    manifest = (
        Path(args.manifest)
        if args.manifest
        else Path(str(backup) + ".manifest.json")
    )
    work_dir = Path(args.work_dir) if args.work_dir else None

    try:
        summary = run_restore_drill(backup, manifest, work_dir)
    except Exception as exc:
        print(f"RESTORE DRILL: FAIL: {exc}", file=sys.stderr)
        return 1

    print("manifest_sha256=ok")
    print("integrity_check=ok")
    print(f"schema_profile={summary['schema_profile']}")
    for table, count in summary["table_counts"].items():
        print(f"{table}={count}")
    print(f"enabled_webhook_endpoints={summary['enabled_webhook_endpoints']}")
    print("RESTORE DRILL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
