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

    counted = list(REQUIRED_TABLES)
    counted.extend(table for table in OPTIONAL_TABLES if table in tables)
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in counted
    }
    enabled_webhooks = connection.execute(
        "SELECT COUNT(*) FROM webhook_endpoints WHERE enabled = 1"
    ).fetchone()[0]

    return {
        "integrity_check": "ok",
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
    for table, count in metadata["table_counts"].items():
        print(f"{table}={count}")
    print(f"enabled_webhook_endpoints={metadata['enabled_webhook_endpoints']}")
    print(f"size_bytes={metadata['size_bytes']}")
    print(f"sha256={metadata['sha256']}")
    print("BACKUP: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
