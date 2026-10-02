#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import BinaryIO

from payment_db_restore_drill import load_manifest, run_restore_drill


PROTOCOL_VERSION = 1
AUTO_NAME_RE = re.compile(r"^payment-auto-\d{8}T\d{6}Z\.sqlite3$")
MAX_MANIFEST_BYTES = 128 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
REMOTE_COMMAND = "receive"


def complete_auto_pairs(directory: Path) -> list[tuple[Path, Path]]:
    directory = directory.expanduser()
    pairs: list[tuple[Path, Path]] = []
    for database in sorted(directory.glob("payment-auto-*.sqlite3")):
        if not AUTO_NAME_RE.fullmatch(database.name):
            continue
        manifest = Path(str(database) + ".manifest.json")
        if manifest.is_file():
            pairs.append((database, manifest))
    return pairs


def select_latest_complete_pair(directory: Path) -> tuple[Path, Path]:
    pairs = complete_auto_pairs(directory)
    if not pairs:
        raise RuntimeError("no complete automatic H2 backup pair is available")
    return pairs[-1]


def validate_source_pair(database: Path, manifest_path: Path) -> dict:
    database = database.expanduser()
    manifest_path = manifest_path.expanduser()

    if not AUTO_NAME_RE.fullmatch(database.name):
        raise RuntimeError("backup filename is not an automatic H2 backup")
    expected_manifest = f"{database.name}.manifest.json"
    if manifest_path.name != expected_manifest:
        raise RuntimeError("manifest filename does not match backup filename")
    if not database.is_file() or not manifest_path.is_file():
        raise RuntimeError("backup or manifest is missing")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise RuntimeError("backup manifest exceeds size limit")

    manifest = load_manifest(manifest_path)
    if manifest["backup_file"] != database.name:
        raise RuntimeError("backup filename does not match manifest")
    if manifest["size_bytes"] != database.stat().st_size:
        raise RuntimeError("backup byte size does not match manifest")

    # Re-run the non-destructive H2 restore drill immediately before transfer.
    # This makes J1 independent from stale timer/log evidence.
    run_restore_drill(database, manifest_path)
    return manifest


def transfer_header(database: Path, manifest_path: Path) -> dict:
    validate_source_pair(database, manifest_path)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "backup_file": database.name,
        "backup_size": database.stat().st_size,
        "manifest_file": manifest_path.name,
        "manifest_size": manifest_path.stat().st_size,
    }


def write_transfer_stream(
    database: Path,
    manifest_path: Path,
    output: BinaryIO,
) -> dict:
    header = transfer_header(database, manifest_path)
    encoded_header = (
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    output.write(encoded_header)

    with manifest_path.open("rb") as handle:
        shutil.copyfileobj(handle, output, length=COPY_CHUNK_BYTES)
    with database.open("rb") as handle:
        shutil.copyfileobj(handle, output, length=COPY_CHUNK_BYTES)
    return header


def build_ssh_command(
    *,
    ssh_bin: str,
    host: str,
    identity_file: Path,
    connect_timeout: int,
) -> list[str]:
    if not host or any(char.isspace() for char in host):
        raise RuntimeError("SSH host must be one non-whitespace argument")
    if connect_timeout < 1 or connect_timeout > 120:
        raise RuntimeError("SSH connect timeout must be between 1 and 120 seconds")
    return [
        ssh_bin,
        "-T",
        "-i",
        str(identity_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        host,
        REMOTE_COMMAND,
    ]


def send_pair(
    *,
    database: Path,
    manifest_path: Path,
    command: list[str],
    timeout: int,
) -> tuple[str, str]:
    if timeout < 1:
        raise RuntimeError("transfer timeout must be positive")

    # Validate before opening a network connection.
    validate_source_pair(database, manifest_path)

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        write_transfer_stream(database, manifest_path, process.stdin)
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=timeout)
    except Exception:
        process.kill()
        process.wait()
        raise

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if process.returncode != 0:
        safe = err.strip() or out.strip() or f"receiver exited {process.returncode}"
        raise RuntimeError(safe)
    return out, err


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Transfer the newest complete, re-verified H2 automatic Payment "
            "Platform backup pair to the forced-command off-host receiver."
        )
    )
    parser.add_argument(
        "--backup-dir",
        default="/var/lib/pepew-pay/backups",
        help="Directory containing H2 payment-auto-* backup/manifest pairs.",
    )
    parser.add_argument(
        "--host",
        required=True,
        help="SSH destination in host or user@host form.",
    )
    parser.add_argument(
        "--identity-file",
        required=True,
        help="Dedicated J1 backup-transfer private key. Do not reuse tunnel keys.",
    )
    parser.add_argument("--ssh-bin", default="/usr/bin/ssh")
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--transfer-timeout", type=int, default=120)
    args = parser.parse_args()

    try:
        database, manifest = select_latest_complete_pair(Path(args.backup_dir))
        identity = Path(args.identity_file).expanduser()
        if not identity.is_file():
            raise RuntimeError("dedicated SSH identity file does not exist")
        command = build_ssh_command(
            ssh_bin=args.ssh_bin,
            host=args.host,
            identity_file=identity,
            connect_timeout=args.connect_timeout,
        )
        stdout, _ = send_pair(
            database=database,
            manifest_path=manifest,
            command=command,
            timeout=args.transfer_timeout,
        )
        metadata = load_manifest(manifest)
    except Exception as exc:
        print(f"OFFHOST COPY: FAIL: {exc}", file=sys.stderr)
        return 1

    for line in stdout.splitlines():
        if line.startswith(("backup_file=", "size_bytes=", "sha256=", "OFFHOST RECEIVE:")):
            print(f"receiver_{line}")
    print(f"backup_file={database.name}")
    print(f"size_bytes={metadata['size_bytes']}")
    print(f"sha256={metadata['sha256']}")
    print("source_restore_drill=pass")
    print("OFFHOST COPY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
