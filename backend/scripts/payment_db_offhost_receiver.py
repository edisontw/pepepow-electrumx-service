#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import BinaryIO

from payment_db_restore_drill import (\n    RESTORE_CONTRACT_VERSION,\n    load_manifest,\n    run_restore_drill,\n)\n

PROTOCOL_VERSION = 1
AUTO_NAME_RE = re.compile(r"^payment-auto-\d{8}T\d{6}Z\.sqlite3$")
MAX_HEADER_BYTES = 4096
MAX_MANIFEST_BYTES = 128 * 1024
MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


def complete_received_pairs(directory: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for database in sorted(directory.glob("payment-auto-*.sqlite3")):
        if not AUTO_NAME_RE.fullmatch(database.name):
            continue
        manifest = Path(str(database) + ".manifest.json")
        if manifest.is_file():
            pairs.append((database, manifest))
    return pairs


def prune_complete_received_pairs(directory: Path, keep: int) -> list[str]:
    if keep < 1:
        raise RuntimeError("off-host retention count must be at least 1")
    pairs = complete_received_pairs(directory)
    if len(pairs) <= keep:
        return []

    removed: list[str] = []
    for database, manifest in pairs[: len(pairs) - keep]:
        database.unlink()
        manifest.unlink()
        removed.append(database.name)
    _fsync_directory(directory)
    return removed


def _positive_int(value: object, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{name} is invalid")
    if value < 1 or value > maximum:
        raise RuntimeError(f"{name} is outside allowed range")
    return value


def read_header(stream: BinaryIO) -> dict:
    raw = stream.readline(MAX_HEADER_BYTES + 1)
    if not raw or not raw.endswith(b"\n"):
        raise RuntimeError("transfer header is missing or truncated")
    if len(raw) > MAX_HEADER_BYTES:
        raise RuntimeError("transfer header exceeds size limit")
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("transfer header is invalid") from exc
    if not isinstance(header, dict):
        raise RuntimeError("transfer header is invalid")

    if header.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("unsupported off-host transfer protocol")

    backup_file = header.get("backup_file")
    manifest_file = header.get("manifest_file")
    if not isinstance(backup_file, str) or not AUTO_NAME_RE.fullmatch(backup_file):
        raise RuntimeError("backup filename is invalid")
    if manifest_file != f"{backup_file}.manifest.json":
        raise RuntimeError("manifest filename does not match backup filename")

    _positive_int(header.get("backup_size"), "backup size", MAX_BACKUP_BYTES)
    _positive_int(header.get("manifest_size"), "manifest size", MAX_MANIFEST_BYTES)
    return header


def _copy_exact(stream: BinaryIO, output: BinaryIO, remaining: int) -> None:
    while remaining:
        block = stream.read(min(COPY_CHUNK_BYTES, remaining))
        if not block:
            raise RuntimeError("transfer payload is truncated")
        output.write(block)
        remaining -= len(block)
    output.flush()
    os.fsync(output.fileno())


def _link_pair_without_overwrite(
    staged_database: Path,
    staged_manifest: Path,
    final_database: Path,
    final_manifest: Path,
) -> None:
    linked_database = False
    try:
        os.link(staged_database, final_database)
        linked_database = True
        os.link(staged_manifest, final_manifest)
    except Exception:
        if linked_database:
            final_database.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def receive_stream(
    stream: BinaryIO,
    destination_dir: Path,
    keep: int | None = None,
) -> tuple[dict, list[str]]:
    header = read_header(stream)
    destination_dir = destination_dir.expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)

    final_database = destination_dir / header["backup_file"]
    final_manifest = destination_dir / header["manifest_file"]
    if final_database.exists() or final_manifest.exists():
        raise FileExistsError("destination backup or manifest already exists")

    staging = Path(
        tempfile.mkdtemp(prefix=".pepew-offhost-incoming-", dir=destination_dir)
    )
    os.chmod(staging, 0o700)
    staged_database = staging / header["backup_file"]
    staged_manifest = staging / header["manifest_file"]

    try:
        with staged_manifest.open("xb") as handle:
            os.chmod(staged_manifest, 0o600)
            _copy_exact(stream, handle, header["manifest_size"])

        manifest = load_manifest(staged_manifest)
        if manifest["backup_file"] != header["backup_file"]:
            raise RuntimeError("backup filename does not match manifest")
        if manifest["size_bytes"] != header["backup_size"]:
            raise RuntimeError("backup size does not match manifest")

        with staged_database.open("xb") as handle:
            os.chmod(staged_database, 0o600)
            _copy_exact(stream, handle, header["backup_size"])

        if stream.read(1):
            raise RuntimeError("transfer payload contains unexpected trailing data")

        # Stronger than a checksum-only destination check: this validates SHA-256,
        # SQLite integrity/schema/counts and performs a temporary restore drill.
        run_restore_drill(staged_database, staged_manifest, work_dir=staging)

        _link_pair_without_overwrite(
            staged_database,
            staged_manifest,
            final_database,
            final_manifest,
        )
        _fsync_directory(destination_dir)
        removed = (
            prune_complete_received_pairs(destination_dir, keep)
            if keep is not None
            else []
        )
        return manifest, removed
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Receive one PEPEW H2 automatic backup pair over stdin. Intended for "
            "use as a forced SSH command with no shell or forwarding privileges."
        )
    )
    parser.add_argument(
        "--destination-dir",
        default="/var/lib/pepew-pay-offhost",
        help="Dedicated non-public VM-A directory for J1 backup pairs.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="Retain this many complete J1-managed automatic backup pairs.",
    )
    args = parser.parse_args()

    original_command = os.environ.get("SSH_ORIGINAL_COMMAND")
    if original_command is not None and original_command.strip() != "receive":
        print("OFFHOST RECEIVE: FAIL: unsupported SSH command", file=sys.stderr)
        return 1

    try:
        metadata, removed = receive_stream(
            sys.stdin.buffer,
            Path(args.destination_dir),
            keep=args.keep,
        )
    except FileExistsError:
        print(
            "OFFHOST RECEIVE: FAIL: destination backup or manifest already exists",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            "OFFHOST RECEIVE: FAIL: "
            f"restore_contract={RESTORE_CONTRACT_VERSION}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"backup_file={metadata['backup_file']}")
    print(f"size_bytes={metadata['size_bytes']}")
    print(f"sha256={metadata['sha256']}")
    print(f"restore_contract={RESTORE_CONTRACT_VERSION}")
    print("destination_restore_drill=pass")
    print(f"retained_complete_backups={len(complete_received_pairs(Path(args.destination_dir)))}")
    print(f"pruned_complete_backups={len(removed)}")
    print("OFFHOST RECEIVE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
