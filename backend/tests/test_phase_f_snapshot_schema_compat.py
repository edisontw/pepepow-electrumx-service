import importlib.util
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snapshot = load_script("phase_f_sqlite_snapshot", "phase_f_sqlite_snapshot.py")
verify = load_script("phase_f_verify_snapshot", "phase_f_verify_snapshot.py")


def create_database(path: Path, *, with_idempotency: bool) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY
            );

            CREATE TABLE payment_transactions (
                payment_id TEXT NOT NULL
            );

            CREATE TABLE events (
                event_id TEXT PRIMARY KEY
            );

            CREATE TABLE webhook_endpoints (
                endpoint_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE webhook_deliveries (
                delivery_id TEXT PRIMARY KEY
            );
            """
        )
        if with_idempotency:
            connection.execute(
                """
                CREATE TABLE payment_idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    payment_id TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL
                )
                """
            )


def test_snapshot_counts_accept_pre_phase_g_schema(tmp_path):
    path = tmp_path / "pre-g.sqlite3"
    create_database(path, with_idempotency=False)

    with sqlite3.connect(path) as connection:
        counts = snapshot.table_counts(connection)

    assert set(counts) == set(snapshot.REQUIRED_TABLES)
    assert "payment_idempotency_keys" not in counts


def test_snapshot_counts_include_phase_g_optional_table(tmp_path):
    path = tmp_path / "post-g.sqlite3"
    create_database(path, with_idempotency=True)

    with sqlite3.connect(path) as connection:
        counts = snapshot.table_counts(connection)

    assert set(counts) == set(snapshot.REQUIRED_TABLES) | {"payment_idempotency_keys"}


def test_verify_accepts_pre_phase_g_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "pre-g.sqlite3"
    create_database(path, with_idempotency=False)

    monkeypatch.setattr(sys, "argv", ["phase_f_verify_snapshot.py", str(path)])
    assert verify.main() == 0


def test_verify_accepts_post_phase_g_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "post-g.sqlite3"
    create_database(path, with_idempotency=True)

    monkeypatch.setattr(sys, "argv", ["phase_f_verify_snapshot.py", str(path)])
    assert verify.main() == 0
