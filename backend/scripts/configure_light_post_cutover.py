#!/usr/bin/env python3
"""Disable authoritative Payment Platform writers on VM-A after Phase F cutover.

The PEPEW Light service also serves legacy read-only Light API and wallet routes,
so it must be restarted after cutover with Payment Platform feature gates disabled
rather than left stopped. This helper preserves DB paths and secret values for
rollback, rewrites backend/.env atomically with mode 0600, and never prints secrets.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Environment file not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


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
            f"Refusing to change authority gates while {service_name} is active"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Disable VM-A Payment Platform writers while preserving Light API."
    )
    parser.add_argument(
        "--service-name",
        default="pepew-light.service",
        help="VM-A service that must be stopped while authority gates are changed.",
    )
    args = parser.parse_args()

    _require_service_stopped(args.service_name)

    lines = _read_lines(ENV_PATH)
    for key in (
        "PAYMENT_API_ENABLED",
        "PAYMENT_WATCHER_ENABLED",
        "PAYMENT_WEBHOOK_ENABLED",
    ):
        lines = _set_value(lines, key, "false")

    content = "\n".join(lines).rstrip() + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".env.post-cutover.",
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

    print("PAYMENT_API_ENABLED=false")
    print("PAYMENT_WATCHER_ENABLED=false")
    print("PAYMENT_WEBHOOK_ENABLED=false")
    print("Payment DB path and secret values preserved for rollback.")
    print(f"{ENV_PATH}: mode 0600")


if __name__ == "__main__":
    main()
