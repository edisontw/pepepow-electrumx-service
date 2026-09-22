#!/usr/bin/env python3
"""Safely enable PEPEW production webhooks in backend/.env.

This script never prints the webhook master key. Existing non-empty master keys
are preserved. The environment file is rewritten atomically with mode 0600.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import tempfile


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


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


def main() -> None:
    lines = _read_lines(ENV_PATH)
    existing_key = _value(lines, "PAYMENT_WEBHOOK_MASTER_KEY")
    generated = not bool(existing_key)
    master_key = existing_key or secrets.token_urlsafe(48)

    lines = _set_value(lines, "PAYMENT_WEBHOOK_MASTER_KEY", master_key)
    lines = _set_value(lines, "PAYMENT_WEBHOOK_ENABLED", "true")

    content = "\n".join(lines).rstrip() + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".env.webhook.",
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

    print("PAYMENT_WEBHOOK_ENABLED=true")
    print(
        "PAYMENT_WEBHOOK_MASTER_KEY="
        + ("generated (value not printed)" if generated else "preserved (value not printed)")
    )
    print(f"{ENV_PATH}: mode 0600")


if __name__ == "__main__":
    main()
