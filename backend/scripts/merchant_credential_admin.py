#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.merchant_store import (  # noqa: E402
    LEGACY_MERCHANT_ID,
    MerchantStore,
    MerchantStoreError,
)


DEFAULT_ENV = BACKEND_DIR / ".env"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def unix_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def load_store(env_file: Path) -> MerchantStore:
    if not env_file.exists():
        raise MerchantStoreError("backend environment file not found")
    db_path = read_env(env_file).get("PAYMENT_DB_PATH")
    if not db_path:
        raise MerchantStoreError("PAYMENT_DB_PATH is not configured")
    return MerchantStore(db_path)


def write_secret_file(path: Path, token: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only PEPEW merchant credential administration. "
            "Secrets are never stored in plaintext in SQLite."
        )
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_merchant = subparsers.add_parser("create-merchant")
    create_merchant.add_argument("--display-name", required=True)

    create_credential = subparsers.add_parser("create-credential")
    create_credential.add_argument("--merchant-id", required=True)
    create_credential.add_argument("--label")
    create_credential.add_argument("--secret-file", required=True)

    list_credentials = subparsers.add_parser("list-credentials")
    list_credentials.add_argument("--merchant-id", required=True)

    disable_credential = subparsers.add_parser("disable-credential")
    disable_credential.add_argument("--credential-id", required=True)

    subparsers.add_parser("show-legacy-merchant")

    args = parser.parse_args()

    try:
        store = load_store(Path(args.env_file))
        if args.command == "create-merchant":
            merchant = store.create_merchant(
                display_name=args.display_name,
                now=unix_now(),
            )
            print(f"merchant_id={merchant['merchant_id']}")
            print("MERCHANT CREATE: PASS")
        elif args.command == "create-credential":
            metadata, token = store.create_credential(
                merchant_id=args.merchant_id,
                label=args.label,
                now=unix_now(),
            )
            write_secret_file(Path(args.secret_file), token)
            print(f"merchant_id={metadata['merchant_id']}")
            print(f"credential_id={metadata['credential_id']}")
            print("secret_output=written_once")
            print("CREDENTIAL CREATE: PASS")
        elif args.command == "list-credentials":
            credentials = store.list_credentials(args.merchant_id)
            print(f"merchant_id={args.merchant_id}")
            print(f"credential_count={len(credentials)}")
            for item in credentials:
                state = "enabled" if item["enabled"] else "disabled"
                print(f"credential_id={item['credential_id']} state={state}")
            print("CREDENTIAL LIST: PASS")
        elif args.command == "disable-credential":
            store.disable_credential(args.credential_id, now=unix_now())
            print(f"credential_id={args.credential_id}")
            print("CREDENTIAL DISABLE: PASS")
        elif args.command == "show-legacy-merchant":
            merchant = store.get_merchant(LEGACY_MERCHANT_ID)
            print(f"merchant_id={merchant['merchant_id']}")
            print("LEGACY MERCHANT: PASS")
        return 0
    except FileExistsError:
        print("MERCHANT ADMIN: FAIL: secret output file already exists", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"MERCHANT ADMIN: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
