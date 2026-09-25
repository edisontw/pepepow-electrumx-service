#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

from payment_db_backup import ENV_PATH, create_backup, read_env
from payment_db_restore_drill import run_restore_drill


AUTO_PREFIX = "payment-auto-"
AUTO_SUFFIX = ".sqlite3"


def complete_auto_backups(directory: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for database in sorted(directory.glob(f"{AUTO_PREFIX}*{AUTO_SUFFIX}")):
        manifest = Path(str(database) + ".manifest.json")
        if manifest.exists():
            pairs.append((database, manifest))
    return pairs


def prune_complete_auto_backups(directory: Path, keep: int) -> list[str]:
    pairs = complete_auto_backups(directory)
    if len(pairs) <= keep:
        return []

    removed: list[str] = []
    for database, manifest in pairs[: len(pairs) - keep]:
        database.unlink()
        manifest.unlink()
        removed.append(database.name)
    return removed


def maintenance_run(
    *,
    backup_dir: Path,
    keep: int,
    min_free_mib: int,
    env_file: Path = ENV_PATH,
    now: datetime | None = None,
) -> tuple[Path, list[str]]:
    if keep < 1:
        raise RuntimeError("retention count must be at least 1")
    if min_free_mib < 1:
        raise RuntimeError("minimum free space must be at least 1 MiB")
    if not env_file.exists():
        raise RuntimeError("backend environment file not found")

    env = read_env(env_file)
    source_raw = env.get("PAYMENT_DB_PATH")
    if not source_raw:
        raise RuntimeError("PAYMENT_DB_PATH is not configured")
    source = Path(source_raw).expanduser()
    if not source.exists():
        raise RuntimeError("source payment database does not exist")

    backup_dir = backup_dir.expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)

    free_bytes = shutil.disk_usage(backup_dir).free
    minimum = min_free_mib * 1024 * 1024
    if free_bytes < minimum:
        raise RuntimeError(
            f"free disk space is below the configured {min_free_mib} MiB guardrail"
        )

    stamp_time = now or datetime.now(timezone.utc)
    stamp = stamp_time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = backup_dir / f"{AUTO_PREFIX}{stamp}{AUTO_SUFFIX}"
    manifest = Path(str(output) + ".manifest.json")

    create_backup(source, output, manifest)
    run_restore_drill(output, manifest)

    removed = prune_complete_auto_backups(backup_dir, keep)
    return output, removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create, restore-drill, and retain bounded automatic PEPEW Payment "
            "Platform SQLite backups. Only complete payment-auto-* backup/manifest "
            "pairs are eligible for retention deletion."
        )
    )
    parser.add_argument(
        "--backup-dir",
        default="/var/lib/pepew-pay/backups",
        help="Directory for automatic backup/manifest pairs.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=14,
        help="Number of complete automatic backup pairs to retain.",
    )
    parser.add_argument(
        "--min-free-mib",
        type=int,
        default=512,
        help="Refuse a new backup when free space is below this threshold.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help="Backend environment file used only to locate PAYMENT_DB_PATH.",
    )
    args = parser.parse_args()

    try:
        output, removed = maintenance_run(
            backup_dir=Path(args.backup_dir),
            keep=args.keep,
            min_free_mib=args.min_free_mib,
            env_file=Path(args.env_file),
        )
    except Exception as exc:
        print(f"BACKUP MAINTENANCE: FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"backup_file={output.name}")
    print("restore_drill=pass")
    print(f"retained_complete_backups={len(complete_auto_backups(output.parent))}")
    print(f"pruned_complete_backups={len(removed)}")
    for name in removed:
        print(f"pruned={name}")
    print("BACKUP MAINTENANCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
