#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sqlite3
import sys


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
EXPECTED_TABLES = (
    "payments",
    "payment_transactions",
    "events",
    "webhook_endpoints",
    "webhook_deliveries",
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = [table for table in EXPECTED_TABLES if table not in tables]
    if missing:
        raise RuntimeError("missing required tables: " + ",".join(missing))
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in EXPECTED_TABLES
    }


def enabled_webhooks(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM webhook_endpoints WHERE enabled = 1"
    ).fetchone()[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a consistent Phase F SQLite snapshot with integrity, row-count, "
            "and SHA-256 verification. Stop authoritative writers before using the "
            "snapshot for cutover."
        )
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New snapshot path. Refuses to overwrite an existing file.",
    )
    parser.add_argument(
        "--require-no-enabled-webhooks",
        action="store_true",
        help="Fail if the source contains any enabled webhook endpoint.",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        print("SNAPSHOT: FAIL: backend/.env not found", file=sys.stderr)
        return 1

    env = read_env(ENV_PATH)
    source_raw = env.get("PAYMENT_DB_PATH")
    if not source_raw:
        print("SNAPSHOT: FAIL: PAYMENT_DB_PATH is not configured", file=sys.stderr)
        return 1

    source = Path(source_raw).expanduser()
    output = Path(args.output).expanduser()

    if not source.exists():
        print("SNAPSHOT: FAIL: source payment database does not exist", file=sys.stderr)
        return 1
    if output.exists():
        print("SNAPSHOT: FAIL: output already exists", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)

    source_uri = f"file:{source.resolve()}?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True, timeout=5.0)
    destination_connection: sqlite3.Connection | None = None
    try:
        integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print("SNAPSHOT: FAIL: source SQLite integrity check failed", file=sys.stderr)
            return 1

        before_counts = table_counts(source_connection)
        before_enabled = enabled_webhooks(source_connection)
        if args.require_no_enabled_webhooks and before_enabled:
            print(
                "SNAPSHOT: FAIL: enabled webhook endpoints exist",
                file=sys.stderr,
            )
            return 2

        destination_connection = sqlite3.connect(output)
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination_connection.commit()

        snapshot_integrity = destination_connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        if snapshot_integrity != "ok":
            print("SNAPSHOT: FAIL: snapshot SQLite integrity check failed", file=sys.stderr)
            return 1

        snapshot_counts = table_counts(destination_connection)
        snapshot_enabled = enabled_webhooks(destination_connection)

        after_counts = table_counts(source_connection)
        after_enabled = enabled_webhooks(source_connection)

        if before_counts != after_counts or before_enabled != after_enabled:
            print(
                "SNAPSHOT: FAIL: source changed during snapshot; ensure writers are stopped",
                file=sys.stderr,
            )
            return 3
        if snapshot_counts != before_counts or snapshot_enabled != before_enabled:
            print("SNAPSHOT: FAIL: snapshot row counts differ from source", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"SNAPSHOT: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if destination_connection is not None:
            destination_connection.close()
        source_connection.close()

    os.chmod(output, 0o600)
    checksum = sha256_file(output)

    print("integrity_check=ok")
    for table in EXPECTED_TABLES:
        print(f"{table}={snapshot_counts[table]}")
    print(f"enabled_webhook_endpoints={snapshot_enabled}")
    print(f"sha256={checksum}")
    print("SNAPSHOT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
