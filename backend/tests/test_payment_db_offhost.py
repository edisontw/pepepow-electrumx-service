import importlib.util
import io
import pytest
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str, filename: str):
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load_script("payment_db_backup_j1", "payment_db_backup.py")
restore = load_script("payment_db_restore_j1", "payment_db_restore_drill.py")
sender = load_script("payment_db_offhost_sender", "payment_db_offhost_sender.py")
receiver = load_script("payment_db_offhost_receiver", "payment_db_offhost_receiver.py")


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


def create_pair(directory: Path, stamp: str) -> tuple[Path, Path]:
    source = directory / f"source-{stamp}.sqlite3"
    create_database(source)
    database = directory / f"payment-auto-{stamp}.sqlite3"
    manifest = Path(str(database) + ".manifest.json")
    backup.create_backup(source, database, manifest)
    return database, manifest


def create_phase_k_database(path: Path) -> None:
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
            CREATE TABLE payment_transactions (payment_id TEXT NOT NULL);
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
        connection.execute(
            """
            INSERT INTO webhook_endpoints(endpoint_id, merchant_id, enabled)
            VALUES ('w1', 'mrc_a', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO webhook_deliveries(delivery_id, event_id, endpoint_id)
            VALUES ('d1', 'e1', 'w1')
            """
        )


def test_select_latest_complete_pair_ignores_manual_orphans(tmp_path):
    first_db, first_manifest = create_pair(tmp_path, "20261001T010000Z")
    latest_db, latest_manifest = create_pair(tmp_path, "20261002T010000Z")

    (tmp_path / "payment-manual.sqlite3").write_bytes(b"manual")
    (tmp_path / "payment-auto-20261003T010000Z.sqlite3").write_bytes(b"orphan")
    malformed = tmp_path / "payment-auto-not-a-timestamp.sqlite3"
    malformed.write_bytes(b"x")
    Path(str(malformed) + ".manifest.json").write_text("{}", encoding="utf-8")

    assert sender.complete_auto_pairs(tmp_path) == [
        (first_db, first_manifest),
        (latest_db, latest_manifest),
    ]
    assert sender.select_latest_complete_pair(tmp_path) == (
        latest_db,
        latest_manifest,
    )


def test_transfer_protocol_round_trip_runs_destination_restore_drill(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    database, manifest = create_pair(source_dir, "20261002T020000Z")

    payload = io.BytesIO()
    header = sender.write_transfer_stream(database, manifest, payload)
    assert header["backup_file"] == database.name

    destination = tmp_path / "destination"
    payload.seek(0)
    metadata, removed = receiver.receive_stream(payload, destination)
    assert removed == []

    copied_database = destination / database.name
    copied_manifest = destination / manifest.name
    assert metadata["backup_file"] == database.name
    assert copied_database.exists()
    assert copied_manifest.exists()
    assert stat.S_IMODE(copied_database.stat().st_mode) == 0o600
    assert stat.S_IMODE(copied_manifest.stat().st_mode) == 0o600
    assert restore.run_restore_drill(copied_database, copied_manifest)[
        "integrity_check"
    ] == "ok"
    assert not list(destination.glob(".pepew-offhost-incoming-*"))


def test_sender_rejects_manifest_filename_mismatch(tmp_path):
    database, manifest = create_pair(tmp_path, "20261002T030000Z")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["backup_file"] = "payment-auto-20261001T000000Z.sqlite3"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    try:
        sender.validate_source_pair(database, manifest)
    except RuntimeError as exc:
        assert "filename" in str(exc)
    else:
        raise AssertionError("manifest filename mismatch must fail before transfer")


def test_receiver_rejects_corrupt_payload_and_cleans_partial_files(tmp_path):
    database, manifest = create_pair(tmp_path, "20261002T040000Z")
    payload = io.BytesIO()
    sender.write_transfer_stream(database, manifest, payload)

    raw = bytearray(payload.getvalue())
    raw[-1] ^= 1

    destination = tmp_path / "destination"
    try:
        receiver.receive_stream(io.BytesIO(raw), destination)
    except RuntimeError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("corrupt payload must fail destination verification")

    assert not (destination / database.name).exists()
    assert not (destination / manifest.name).exists()
    assert not list(destination.glob(".pepew-offhost-incoming-*"))


def test_receiver_refuses_overwrite_and_preserves_existing_pair(tmp_path):
    database, manifest = create_pair(tmp_path, "20261002T050000Z")
    payload = io.BytesIO()
    sender.write_transfer_stream(database, manifest, payload)

    destination = tmp_path / "destination"
    payload.seek(0)
    receiver.receive_stream(payload, destination)
    original = (destination / database.name).read_bytes()

    payload.seek(0)
    try:
        receiver.receive_stream(payload, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing off-host recovery point must not be overwritten")

    assert (destination / database.name).read_bytes() == original


def test_receiver_rejects_unsafe_filename_before_writing(tmp_path):
    header = {
        "protocol_version": 1,
        "backup_file": "../payments.sqlite3",
        "backup_size": 1,
        "manifest_file": "../payments.sqlite3.manifest.json",
        "manifest_size": 2,
    }
    payload = io.BytesIO(
        json.dumps(header).encode("utf-8") + b"\n{}x"
    )

    try:
        receiver.receive_stream(payload, tmp_path / "destination")
    except RuntimeError as exc:
        assert "filename" in str(exc)
    else:
        raise AssertionError("unsafe path-like filename must fail")


def test_ssh_command_uses_dedicated_noninteractive_hardening(tmp_path):
    identity = tmp_path / "j1-key"
    identity.write_text("not-a-real-key", encoding="utf-8")
    command = sender.build_ssh_command(
        ssh_bin="/usr/bin/ssh",
        host="backup@192.0.2.10",
        identity_file=identity,
        connect_timeout=10,
    )

    assert command[0:2] == ["/usr/bin/ssh", "-T"]
    assert "BatchMode=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert "PasswordAuthentication=no" in command
    assert "KbdInteractiveAuthentication=no" in command
    assert "StrictHostKeyChecking=yes" in command
    assert command[-2:] == ["backup@192.0.2.10", "receive"]



def test_receiver_retention_prunes_only_old_complete_managed_pairs(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()

    manual = destination / "payment-manual.sqlite3"
    manual.write_bytes(b"manual")
    orphan = destination / "payment-auto-20260901T000000Z.sqlite3"
    orphan.write_bytes(b"orphan")
    malformed = destination / "payment-auto-manual.sqlite3"
    malformed.write_bytes(b"x")
    Path(str(malformed) + ".manifest.json").write_text("{}", encoding="utf-8")

    for stamp in (
        "20261001T010000Z",
        "20261001T020000Z",
        "20261001T030000Z",
    ):
        database, manifest = create_pair(source_dir, stamp)
        payload = io.BytesIO()
        sender.write_transfer_stream(database, manifest, payload)
        payload.seek(0)
        receiver.receive_stream(payload, destination, keep=2)

    pairs = receiver.complete_received_pairs(destination)
    assert [database.name for database, _ in pairs] == [
        "payment-auto-20261001T020000Z.sqlite3",
        "payment-auto-20261001T030000Z.sqlite3",
    ]
    assert manual.exists()
    assert orphan.exists()
    assert malformed.exists()
    assert Path(str(malformed) + ".manifest.json").exists()


def test_sender_receiver_process_failure_preserves_source_pair(tmp_path):
    database, manifest = create_pair(tmp_path, "20261002T060000Z")
    database_before = database.read_bytes()
    manifest_before = manifest.read_bytes()
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdin.buffer.read(); raise SystemExit(7)",
    ]

    try:
        sender.send_pair(
            database=database,
            manifest_path=manifest,
            command=command,
            timeout=5,
        )
    except RuntimeError as exc:
        assert "receiver exited 7" in str(exc)
    else:
        raise AssertionError("receiver process failure must fail the off-host step")

    assert database.read_bytes() == database_before
    assert manifest.read_bytes() == manifest_before


def test_sender_timeout_preserves_source_pair(tmp_path):
    database, manifest = create_pair(tmp_path, "20261002T070000Z")
    database_before = database.read_bytes()
    manifest_before = manifest.read_bytes()
    command = [
        sys.executable,
        "-c",
        "import sys,time; sys.stdin.buffer.read(); time.sleep(2)",
    ]

    try:
        sender.send_pair(
            database=database,
            manifest_path=manifest,
            command=command,
            timeout=1,
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("transfer timeout must fail the off-host step")

    assert database.read_bytes() == database_before
    assert manifest.read_bytes() == manifest_before


def test_retention_rejects_invalid_keep_without_deleting(tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    database = destination / "payment-auto-20261001T010000Z.sqlite3"
    manifest = Path(str(database) + ".manifest.json")
    database.write_bytes(b"db")
    manifest.write_text("{}", encoding="utf-8")

    try:
        receiver.prune_complete_received_pairs(destination, keep=0)
    except RuntimeError as exc:
        assert "retention" in str(exc)
    else:
        raise AssertionError("invalid retention must fail closed")

    assert database.exists()
    assert manifest.exists()



def test_phase_k_profile_survives_offhost_round_trip(tmp_path):
    source_dir = tmp_path / "source-k"
    source_dir.mkdir()
    source = source_dir / "source-phase-k.sqlite3"
    create_phase_k_database(source)

    database = source_dir / "payment-auto-20261003T000000Z.sqlite3"
    manifest = Path(str(database) + ".manifest.json")
    metadata = backup.create_backup(source, database, manifest)
    assert metadata["schema_profile"] == "phase_k_merchant_v1"

    payload = io.BytesIO()
    sender.write_transfer_stream(database, manifest, payload)
    payload.seek(0)

    destination = tmp_path / "destination-k"
    received, removed = receiver.receive_stream(payload, destination)

    assert removed == []
    assert received["schema_profile"] == "phase_k_merchant_v1"
    copied_database = destination / database.name
    copied_manifest = destination / manifest.name
    summary = restore.run_restore_drill(copied_database, copied_manifest)
    assert summary["schema_profile"] == "phase_k_merchant_v1"
    assert set(summary["merchant_ownership_checks"].values()) == {0}



def test_receiver_reports_restore_contract_on_success(tmp_path, capsys):
    source = tmp_path / "source-contract"
    destination = tmp_path / "destination-contract"
    source.mkdir()
    database, manifest = create_pair(source, "20261003T040000Z")

    payload = io.BytesIO()
    sender.write_transfer_stream(database, manifest, payload)
    payload.seek(0)

    metadata, _removed = receiver.receive_stream(payload, destination)
    assert metadata["backup_file"] == database.name
    assert receiver.RESTORE_CONTRACT_VERSION == "phase_k_counts_v1"
    assert sender.EXPECTED_RECEIVER_RESTORE_CONTRACT == "phase_k_counts_v1"


def test_sender_rejects_success_without_restore_contract(tmp_path, monkeypatch):
    database, manifest = create_pair(tmp_path, "20261003T040001Z")

    class FakeProcess:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.returncode = 0

        def communicate(self, timeout):
            return (
                b"backup_file=x\nOFFHOST RECEIVE: PASS\n",
                b"",
            )

        def kill(self):
            pass

        def wait(self):
            return 0

    monkeypatch.setattr(sender.subprocess, "Popen", lambda *a, **k: FakeProcess())

    with pytest.raises(
        RuntimeError,
        match="receiver restore contract mismatch or missing",
    ):
        sender.send_pair(
            database=database,
            manifest_path=manifest,
            command=["ssh"],
            timeout=10,
        )
