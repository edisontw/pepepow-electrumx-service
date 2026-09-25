from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy" / "systemd" / "pepew-pay-backup.service"


def test_backup_unit_allows_wal_reader_coordination_without_writable_live_db():
    text = UNIT.read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in text
    assert "ProtectHome=read-only" in text

    # A SQLite WAL reader may need writable access to the -shm coordination
    # sidecar. The unit therefore exposes the state directory read/write inside
    # its private mount namespace, while pinning authoritative DB/WAL files
    # read-only.
    assert "ReadWritePaths=/var/lib/pepew-pay" in text
    assert "ReadOnlyPaths=/var/lib/pepew-pay/payments.sqlite3" in text
    assert "ReadOnlyPaths=-/var/lib/pepew-pay/payments.sqlite3-wal" in text
    assert "ReadOnlyPaths=-/var/lib/pepew-pay/payments.sqlite3-journal" in text

    # Do not accidentally make the SQLite shared-memory sidecar read-only: WAL
    # readers may need to create or update it even when the DB is opened mode=ro.
    assert "ReadOnlyPaths=/var/lib/pepew-pay/payments.sqlite3-shm" not in text
    assert "ReadOnlyPaths=-/var/lib/pepew-pay/payments.sqlite3-shm" not in text
