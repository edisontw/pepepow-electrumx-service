#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sqlite3
import sys


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a transferred Phase F SQLite snapshot before activation."
    )
    parser.add_argument("database", help="Transferred SQLite snapshot path.")
    parser.add_argument(
        "--sha256",
        help="Expected SHA-256 from the source snapshot host.",
    )
    parser.add_argument(
        "--require-no-enabled-webhooks",
        action="store_true",
        help="Fail if any enabled webhook endpoint exists.",
    )
    args = parser.parse_args()

    database = Path(args.database).expanduser()
    if not database.exists():
        print("VERIFY: FAIL: database does not exist", file=sys.stderr)
        return 1

    actual_sha256 = sha256_file(database)
    if args.sha256 and actual_sha256.lower() != args.sha256.lower():
        print("VERIFY: FAIL: SHA-256 mismatch", file=sys.stderr)
        return 1

    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print("VERIFY: FAIL: SQLite integrity check failed", file=sys.stderr)
            return 1

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [table for table in REQUIRED_TABLES if table not in tables]
        if missing:
            print(
                "VERIFY: FAIL: missing required tables: " + ",".join(missing),
                file=sys.stderr,
            )
            return 1

        counted = list(REQUIRED_TABLES)
        counted.extend(table for table in OPTIONAL_TABLES if table in tables)
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in counted
        }
        enabled = connection.execute(
            "SELECT COUNT(*) FROM webhook_endpoints WHERE enabled = 1"
        ).fetchone()[0]
    finally:
        connection.close()

    if args.require_no_enabled_webhooks and enabled:
        print("VERIFY: FAIL: enabled webhook endpoints exist", file=sys.stderr)
        return 2

    print("integrity_check=ok")
    for table, count in counts.items():
        print(f"{table}={count}")
    print(f"enabled_webhook_endpoints={enabled}")
    print(f"sha256={actual_sha256}")
    print("VERIFY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
