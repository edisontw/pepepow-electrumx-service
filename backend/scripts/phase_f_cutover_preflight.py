#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def bool_value(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Phase F Payment Platform cutover preflight."
    )
    parser.add_argument(
        "--require-no-enabled-webhooks",
        action="store_true",
        help="Fail if any enabled webhook endpoint exists.",
    )
    args = parser.parse_args()

    if not ENV_PATH.exists():
        print("PRECHECK: FAIL: backend/.env not found", file=sys.stderr)
        return 1

    env = read_env(ENV_PATH)
    db_path_raw = env.get("PAYMENT_DB_PATH")
    if not db_path_raw:
        print("PRECHECK: FAIL: PAYMENT_DB_PATH is not configured", file=sys.stderr)
        return 1

    db_path = Path(db_path_raw).expanduser()
    if not db_path.exists():
        print("PRECHECK: FAIL: payment database does not exist", file=sys.stderr)
        return 1

    print(
        "feature_gates="
        f"api:{str(bool_value(env.get('PAYMENT_API_ENABLED'))).lower()},"
        f"watcher:{str(bool_value(env.get('PAYMENT_WATCHER_ENABLED'))).lower()},"
        f"webhook:{str(bool_value(env.get('PAYMENT_WEBHOOK_ENABLED'))).lower()}"
    )

    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"integrity_check={integrity}")
        if integrity != "ok":
            print("PRECHECK: FAIL: SQLite integrity check failed", file=sys.stderr)
            return 1

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        expected = [
            "payments",
            "payment_transactions",
            "events",
            "webhook_endpoints",
            "webhook_deliveries",
        ]
        for table in expected:
            if table not in tables:
                print(f"{table}=missing")
                continue
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}={count}")

        enabled_endpoints = 0
        if "webhook_endpoints" in tables:
            enabled_endpoints = connection.execute(
                "SELECT COUNT(*) FROM webhook_endpoints WHERE enabled = 1"
            ).fetchone()[0]
            print(f"enabled_webhook_endpoints={enabled_endpoints}")

        if "webhook_deliveries" in tables:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM webhook_deliveries
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
            summary = ",".join(f"{row['status']}:{row['count']}" for row in rows)
            print(f"webhook_delivery_statuses={summary or 'none'}")

        if args.require_no_enabled_webhooks and enabled_endpoints:
            print(
                "PRECHECK: FAIL: enabled webhook endpoints exist; do not rotate "
                "the webhook master key silently",
                file=sys.stderr,
            )
            return 2
    finally:
        connection.close()

    print("PRECHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
