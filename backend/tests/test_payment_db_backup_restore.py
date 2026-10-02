import importlib.util
import json
from pathlib import Path
import sqlite3
import stat


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load_script("payment_db_backup", "payment_db_backup.py")
restore = load_script("payment_db_restore_drill", "payment_db_restore_drill.py")


def create_database(path: Path, *, with_idempotency: bool = True) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
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
        connection.execute("INSERT INTO payments(payment_id) VALUES ('p1')")
        connection.execute("INSERT INTO events(event_id) VALUES ('e1')")


def test_online_backup_and_restore_drill_pass_with_wal_source(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)

    # Keep a WAL-mode connection open to model the live VM-B database remaining
    # available while SQLite's online backup API creates the snapshot.
    live = sqlite3.connect(source)
    try:
        live.execute("INSERT INTO payments(payment_id) VALUES ('p2')")
        live.commit()

        output = tmp_path / "backup.sqlite3"
        manifest = tmp_path / "backup.sqlite3.manifest.json"
        metadata = backup.create_backup(source, output, manifest)

        assert output.exists()
        assert manifest.exists()
        assert metadata["integrity_check"] == "ok"
        assert metadata["table_counts"]["payments"] == 2
        assert metadata["sha256"] == backup.sha256_file(output)
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        assert stat.S_IMODE(manifest.stat().st_mode) == 0o600

        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        assert manifest_data == metadata

        summary = restore.run_restore_drill(output, manifest)
        assert summary["integrity_check"] == "ok"
        assert summary["table_counts"]["payments"] == 2
    finally:
        live.close()


def test_backup_refuses_to_overwrite_existing_output(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)

    output = tmp_path / "backup.sqlite3"
    output.write_bytes(b"keep-me")
    manifest = tmp_path / "backup.sqlite3.manifest.json"

    try:
        backup.create_backup(source, output, manifest)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing backup output must not be overwritten")

    assert output.read_bytes() == b"keep-me"
    assert not manifest.exists()


def test_backup_accepts_pre_phase_g_schema(tmp_path):
    source = tmp_path / "pre-g.sqlite3"
    create_database(source, with_idempotency=False)

    output = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.sqlite3.manifest.json"
    metadata = backup.create_backup(source, output, manifest)

    assert "payment_idempotency_keys" not in metadata["table_counts"]
    assert restore.run_restore_drill(output, manifest)["integrity_check"] == "ok"


def test_restore_drill_rejects_tampered_backup(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)

    output = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.sqlite3.manifest.json"
    backup.create_backup(source, output, manifest)

    payload = bytearray(output.read_bytes())
    payload[-1] ^= 1
    output.write_bytes(payload)

    try:
        restore.run_restore_drill(output, manifest)
    except RuntimeError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("tampered backup must fail restore drill")


def test_restore_drill_rejects_manifest_count_mismatch(tmp_path):
    source = tmp_path / "payments.sqlite3"
    create_database(source)

    output = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.sqlite3.manifest.json"
    backup.create_backup(source, output, manifest)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["table_counts"]["payments"] += 1
    manifest.write_text(json.dumps(data), encoding="utf-8")

    try:
        restore.run_restore_drill(output, manifest)
    except RuntimeError as exc:
        assert "table counts" in str(exc)
    else:
        raise AssertionError("manifest count mismatch must fail restore drill")



def create_phase_k_database(path: Path, *, cross_owner: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE merchants (
                merchant_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE merchant_credentials (
                credential_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL
            );
            CREATE TABLE payment_transactions (
                payment_id TEXT NOT NULL
            );
            CREATE TABLE payment_idempotency_keys (
                merchant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                PRIMARY KEY (merchant_id, idempotency_key)
            );
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                payment_id TEXT NOT NULL
            );
            CREATE TABLE webhook_endpoints (
                endpoint_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE webhook_deliveries (
                delivery_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                endpoint_id TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO merchants(merchant_id, enabled) VALUES ('mrc_a', 1)"
        )
        connection.execute(
            "INSERT INTO merchants(merchant_id, enabled) VALUES ('mrc_b', 1)"
        )
        connection.execute(
            """
            INSERT INTO merchant_credentials(
                credential_id, merchant_id, token_hash, enabled
            ) VALUES ('mck_a', 'mrc_a', ?, 1)
            """,
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO payments(payment_id, merchant_id) VALUES ('p1', 'mrc_a')"
        )
        connection.execute(
            """
            INSERT INTO payment_idempotency_keys(
                merchant_id, idempotency_key, payment_id
            ) VALUES ('mrc_a', 'retry-1', 'p1')
            """
        )
        connection.execute(
            """
            INSERT INTO events(event_id, merchant_id, payment_id)
            VALUES ('e1', 'mrc_a', 'p1')
            """
        )
        endpoint_owner = "mrc_b" if cross_owner else "mrc_a"
        connection.execute(
            """
            INSERT INTO webhook_endpoints(
                endpoint_id, merchant_id, enabled
            ) VALUES ('w1', ?, 1)
            """,
            (endpoint_owner,),
        )
        connection.execute(
            """
            INSERT INTO webhook_deliveries(
                delivery_id, event_id, endpoint_id
            ) VALUES ('d1', 'e1', 'w1')
            """
        )


def test_phase_k_backup_restore_profile_validates_merchant_ownership(tmp_path):
    source = tmp_path / "phase-k.sqlite3"
    create_phase_k_database(source)

    output = tmp_path / "phase-k-backup.sqlite3"
    manifest = tmp_path / "phase-k-backup.sqlite3.manifest.json"
    metadata = backup.create_backup(source, output, manifest)

    assert metadata["schema_profile"] == "phase_k_merchant_v1"
    assert metadata["table_counts"]["merchants"] == 2
    assert metadata["table_counts"]["merchant_credentials"] == 1
    assert set(metadata["merchant_ownership_checks"].values()) == {0}

    summary = restore.run_restore_drill(output, manifest)
    assert summary["schema_profile"] == "phase_k_merchant_v1"
    assert summary["merchant_ownership_checks"] == metadata["merchant_ownership_checks"]


def test_phase_k_backup_rejects_cross_merchant_delivery_ownership(tmp_path):
    source = tmp_path / "phase-k-bad.sqlite3"
    create_phase_k_database(source, cross_owner=True)

    output = tmp_path / "phase-k-bad-backup.sqlite3"
    manifest = tmp_path / "phase-k-bad-backup.sqlite3.manifest.json"

    try:
        backup.create_backup(source, output, manifest)
    except RuntimeError as exc:
        assert "delivery_owner_mismatch" in str(exc)
    else:
        raise AssertionError("cross-merchant webhook delivery must fail backup validation")

    assert not output.exists()
    assert not manifest.exists()


def test_restore_accepts_historical_manifest_without_schema_profile(tmp_path):
    source = tmp_path / "legacy.sqlite3"
    create_database(source)

    output = tmp_path / "legacy-backup.sqlite3"
    manifest = tmp_path / "legacy-backup.sqlite3.manifest.json"
    backup.create_backup(source, output, manifest)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data.pop("schema_profile") == "legacy_payment_v1"
    manifest.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = restore.run_restore_drill(output, manifest)
    assert summary["schema_profile"] == "legacy_payment_v1"
