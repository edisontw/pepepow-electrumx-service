import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maintenance = load_script(
    "payment_db_backup_maintenance",
    "payment_db_backup_maintenance.py",
)


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE payments (payment_id TEXT PRIMARY KEY);
            CREATE TABLE payment_transactions (payment_id TEXT NOT NULL);
            CREATE TABLE events (event_id TEXT PRIMARY KEY);
            CREATE TABLE webhook_endpoints (
                endpoint_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE webhook_deliveries (delivery_id TEXT PRIMARY KEY);
            CREATE TABLE payment_idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO payments(payment_id) VALUES ('p1')")


def write_env(path: Path, database: Path) -> None:
    path.write_text(f"PAYMENT_DB_PATH={database}\n", encoding="utf-8")


def test_maintenance_creates_verified_backup_and_retains_only_keep_count(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)
    env = tmp_path / ".env"
    write_env(env, source)
    backup_dir = tmp_path / "backups"

    times = [
        datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc),
    ]
    for now in times:
        maintenance.maintenance_run(
            backup_dir=backup_dir,
            keep=2,
            min_free_mib=1,
            env_file=env,
            now=now,
        )

    pairs = maintenance.complete_auto_backups(backup_dir)
    assert [database.name for database, _ in pairs] == [
        "payment-auto-20260926T010000Z.sqlite3",
        "payment-auto-20260927T010000Z.sqlite3",
    ]


def test_retention_never_deletes_manual_or_orphan_files(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    manual = backup_dir / "payment-h2-manual.sqlite3"
    manual.write_bytes(b"manual")

    orphan = backup_dir / "payment-auto-20260920T010000Z.sqlite3"
    orphan.write_bytes(b"orphan")

    for day in (21, 22):
        database = backup_dir / f"payment-auto-202609{day:02d}T010000Z.sqlite3"
        database.write_bytes(b"db")
        Path(str(database) + ".manifest.json").write_text("{}", encoding="utf-8")

    removed = maintenance.prune_complete_auto_backups(backup_dir, keep=1)

    assert removed == ["payment-auto-20260921T010000Z.sqlite3"]
    assert manual.exists()
    assert orphan.exists()
    assert (backup_dir / "payment-auto-20260922T010000Z.sqlite3").exists()


def test_low_disk_guardrail_fails_before_backup(tmp_path, monkeypatch):
    source = tmp_path / "payments.sqlite3"
    create_database(source)
    env = tmp_path / ".env"
    write_env(env, source)
    backup_dir = tmp_path / "backups"

    class Usage:
        total = 10
        used = 9
        free = 1

    monkeypatch.setattr(maintenance.shutil, "disk_usage", lambda _: Usage())

    try:
        maintenance.maintenance_run(
            backup_dir=backup_dir,
            keep=14,
            min_free_mib=512,
            env_file=env,
            now=datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
    except RuntimeError as exc:
        assert "guardrail" in str(exc)
    else:
        raise AssertionError("low free space must fail maintenance")

    assert maintenance.complete_auto_backups(backup_dir) == []


def test_invalid_retention_refuses_to_run(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)
    env = tmp_path / ".env"
    write_env(env, source)

    try:
        maintenance.maintenance_run(
            backup_dir=tmp_path / "backups",
            keep=0,
            min_free_mib=1,
            env_file=env,
        )
    except RuntimeError as exc:
        assert "retention" in str(exc)
    else:
        raise AssertionError("invalid retention count must fail")
