#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.merchant_store import MerchantStore  # noqa: E402
from payment_db_backup import (  # noqa: E402
    PHASE_K_SCHEMA_PROFILE,
    database_summary,
    read_env,
)


ENV_PATH = BACKEND_DIR / ".env"


class ScopedAuthConfigError(RuntimeError):
    pass


def require_service_stopped(service_name: str) -> None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ScopedAuthConfigError("could not verify service state") from exc
    if result.returncode == 0:
        raise ScopedAuthConfigError(
            f"refusing config change while {service_name} is active"
        )


def _replace_env_value(
    content: str,
    key: str,
    value: str,
) -> str:
    lines = content.splitlines()
    prefix = f"{key}="
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def write_gate(
    env_path: Path,
    *,
    enabled: bool,
) -> None:
    content = env_path.read_text(encoding="utf-8")
    updated = _replace_env_value(
        content,
        "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED",
        "true" if enabled else "false",
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".env.phase-k.",
        dir=str(env_path.parent),
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, env_path)
        os.chmod(env_path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def verify_phase_k_database(db_path: Path) -> None:
    if not db_path.exists():
        raise ScopedAuthConfigError("payment database does not exist")
    uri = f"file:{db_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        summary = database_summary(connection)
    finally:
        connection.close()
    if summary.get("schema_profile") != PHASE_K_SCHEMA_PROFILE:
        raise ScopedAuthConfigError(
            "Phase K merchant ownership schema is not verified"
        )


def configure(
    env_path: Path,
    *,
    enabled: bool,
) -> None:
    if not env_path.exists():
        raise ScopedAuthConfigError("backend environment file not found")
    env = read_env(env_path)
    db_raw = env.get("PAYMENT_DB_PATH")
    if not db_raw:
        raise ScopedAuthConfigError("PAYMENT_DB_PATH is not configured")
    db_path = Path(db_raw).expanduser()

    verify_phase_k_database(db_path)

    legacy_key = (env.get("PAYMENT_CREATE_API_KEY") or "").strip()
    if len(legacy_key) < 32:
        raise ScopedAuthConfigError(
            "legacy PAYMENT_CREATE_API_KEY must remain configured during K5"
        )

    if enabled and not MerchantStore(str(db_path)).has_active_credentials():
        raise ScopedAuthConfigError(
            "no active database-backed merchant credential exists"
        )

    write_gate(env_path, enabled=enabled)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Safely enable or disable Phase K database-backed merchant auth. "
            "The Payment Platform service must be stopped. Legacy auth remains "
            "configured during K5."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true")
    group.add_argument("--disable", action="store_true")
    parser.add_argument(
        "--service-name",
        default="pepew-pay.service",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
    )
    args = parser.parse_args()

    enabled = bool(args.enable)
    try:
        require_service_stopped(args.service_name)
        env_path = Path(args.env_file).expanduser()
        configure(env_path, enabled=enabled)
    except Exception as exc:
        print(f"PHASE K AUTH CONFIG: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        "PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED="
        + ("true" if enabled else "false")
    )
    print("legacy_payment_create_api_key=preserved")
    print("environment_mode=0600")
    print("PHASE K AUTH CONFIG: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
