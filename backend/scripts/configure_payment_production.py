#!/usr/bin/env python3
"""Safely prepare VM-B Payment API/watcher production settings.

This helper is intended for the Phase F authority cutover after a verified
SQLite snapshot has been installed at the production path. It refuses to run
while the VM-B service is active, validates the SQLite database read-only,
generates a merchant API key only when one is absent, and rewrites backend/.env
atomically with mode 0600. Secret values are never printed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
EXPECTED_TABLES = (
    "payments",
    "payment_idempotency_keys",
    "payment_transactions",
    "events",
    "webhook_endpoints",
    "webhook_deliveries",
)


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Environment file not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _value(lines: list[str], key: str) -> str | None:
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip().strip('"').strip("'")
    return None


def _set_value(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    result: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith(prefix):
            result.append(f"{key}={value}")
            replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"{key}={value}")
    return result


def _require_service_stopped(service_name: str) -> None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise SystemExit("Could not verify service state") from exc
    if result.returncode == 0:
        raise SystemExit(
            f"Refusing to modify production settings while {service_name} is active"
        )


def _verify_database(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Production payment database does not exist: {path}")
    uri = f"file:{path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit("Production payment database integrity check failed")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [table for table in EXPECTED_TABLES if table not in tables]
        if missing:
            raise SystemExit(
                "Production payment database is missing required tables: "
                + ",".join(missing)
            )
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare VM-B Payment API/watcher production settings safely."
    )
    parser.add_argument(
        "--db-path",
        default="/var/lib/pepew-pay/payments.sqlite3",
        help="Verified production SQLite database path.",
    )
    parser.add_argument(
        "--service-name",
        default="pepew-pay.service",
        help="VM-B service that must be stopped while settings are changed.",
    )
    args = parser.parse_args()

    _require_service_stopped(args.service_name)

    db_path = Path(args.db_path).expanduser()
    _verify_database(db_path)

    lines = _read_lines(ENV_PATH)
    existing_key = _value(lines, "PAYMENT_CREATE_API_KEY")
    if existing_key and len(existing_key) < 32:
        raise SystemExit(
            "Existing PAYMENT_CREATE_API_KEY is too short; clear or replace it before cutover"
        )

    generated = not bool(existing_key)
    api_key = existing_key or secrets.token_urlsafe(48)

    lines = _set_value(lines, "PAYMENT_DB_PATH", str(db_path))
    lines = _set_value(lines, "PAYMENT_CREATE_API_KEY", api_key)
    lines = _set_value(lines, "PAYMENT_API_ENABLED", "true")
    lines = _set_value(lines, "PAYMENT_WATCHER_ENABLED", "true")

    content = "\n".join(lines).rstrip() + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".env.payment.",
        dir=str(ENV_PATH.parent),
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, ENV_PATH)
        os.chmod(ENV_PATH, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    print(f"PAYMENT_DB_PATH={db_path}")
    print("PAYMENT_API_ENABLED=true")
    print("PAYMENT_WATCHER_ENABLED=true")
    print(
        "PAYMENT_CREATE_API_KEY="
        + ("generated (value not printed)" if generated else "preserved (value not printed)")
    )
    print(f"{ENV_PATH}: mode 0600")


if __name__ == "__main__":
    main()
