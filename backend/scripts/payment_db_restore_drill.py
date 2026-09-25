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

    if summary["table_counts"] != manifest["table_counts"]:
        raise RuntimeError("backup table counts do not match manifest")
    if (
        summary["enabled_webhook_endpoints"]
        != manifest.get("enabled_webhook_endpoints")
    ):
        raise RuntimeError("backup webhook count does not match manifest")
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
    for table, count in summary["table_counts"].items():
        print(f"{table}={count}")
    print(f"enabled_webhook_endpoints={summary['enabled_webhook_endpoints']}")
    print("RESTORE DRILL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
