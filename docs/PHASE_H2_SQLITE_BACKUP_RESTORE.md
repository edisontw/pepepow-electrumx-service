# Phase H2 — SQLite Backup / Restore Operational Hardening

Status: **IN PROGRESS — online backup + non-destructive restore drill implemented; production acceptance pending**

VM-B (`pay.pepepow.net`) is the sole authoritative Payment Platform writer. This
runbook hardens backup and restore operations without adding Redis, PostgreSQL,
external backup daemons, or other infrastructure.

## Goals

- create a consistent SQLite backup while `pepew-pay.service` remains online
- verify SQLite integrity, required schema, table counts, and SHA-256
- write a small privacy-safe manifest beside each backup
- refuse accidental overwrite of an existing backup or manifest
- rehearse restore into a temporary database without touching live authority
- keep backup and restore operations explicit and low frequency on the current
  single-core VM
- preserve the rule that an older backup must not silently replace newer
  authoritative payments, events, webhook state, or deliveries

These tools never contain or print merchant API keys, webhook secrets, payment
addresses, payment IDs, transaction IDs, or row contents.

## Tooling

```text
backend/scripts/payment_db_backup.py
backend/scripts/payment_db_restore_drill.py
```

The earlier Phase F snapshot helpers remain available for cutover/history use.
H2 does not change their semantics.

## 1. Online production backup

The H2 backup helper uses SQLite's backup API. It does **not** require stopping
`pepew-pay.service`.

From VM-B:

```bash
cd /home/ubuntu/pepepow-electrumx-service

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p /var/lib/pepew-pay/backups

python3 backend/scripts/payment_db_backup.py \
  --output "/var/lib/pepew-pay/backups/payment-$STAMP.sqlite3"
```

Expected result:

```text
integrity_check=ok
payments=<count>
payment_transactions=<count>
events=<count>
webhook_endpoints=<count>
webhook_deliveries=<count>
payment_idempotency_keys=<count>   # when present
enabled_webhook_endpoints=<count>
size_bytes=<bytes>
sha256=<checksum>
BACKUP: PASS
```

The helper also creates:

```text
payment-<timestamp>.sqlite3.manifest.json
```

Both files are mode `0600`.

The manifest contains only:

- format version
- creation timestamp
- backup filename
- SHA-256
- byte size
- integrity result
- bounded table counts
- enabled webhook endpoint count

It does not contain row data or credentials.

### Failure behavior

The helper refuses to overwrite an existing backup or manifest. Partial
temporary files are removed on failure.

If source integrity fails, required tables are missing, the backup cannot be
created, or the completed backup fails integrity validation, the command exits
non-zero and must not be treated as a valid recovery point.

## 2. Non-destructive restore drill

Every recovery point should be periodically proven readable/restorable instead
of relying only on file existence.

Run:

```bash
python3 backend/scripts/payment_db_restore_drill.py \
  "/var/lib/pepew-pay/backups/payment-<timestamp>.sqlite3"
```

The drill:

1. reads the adjacent manifest
2. verifies the backup SHA-256
3. runs SQLite integrity and required-schema checks
4. verifies table counts against the manifest
5. restores the backup through SQLite's backup API into a temporary database
6. verifies the restored database again
7. deletes the temporary drill database

Expected final output:

```text
RESTORE DRILL: PASS
```

The drill never reads `PAYMENT_DB_PATH` and never replaces the live database.

## 3. Production restore boundary

A successful restore drill proves that the backup file is internally usable. It
does **not** authorize an automatic rollback.

Before restoring a backup into production, explicitly establish the recovery
point and data-loss boundary.

If VM-B has accepted any newer authoritative write after the backup was taken,
an old backup may omit:

- newly created payments
- new payment transaction observations
- payment state-change events
- webhook endpoint changes
- webhook deliveries/retry state
- idempotency mappings
- merchant references

Therefore:

**Do not automatically replace the live database with an older backup.**

A real production restore requires the operator to:

1. stop `pepew-pay.service`
2. preserve the current live DB/WAL/SHM files as recovery evidence
3. verify the selected backup and manifest
4. explicitly decide whether newer writes are absent, already reconciled, or
   intentionally being abandoned
5. install the selected database with the correct owner/mode
6. ensure stale WAL/SHM files from the previous database are not attached to the
   restored file
7. verify SQLite integrity before service start
8. start `pepew-pay.service`
9. verify health, watcher health, merchant API boundaries, and webhook worker
   state

H2 intentionally does not automate those destructive steps in this increment.

Moving Payment Platform authority back to VM-A is not a routine restore path.
VM-A remains Light-only.

## 4. Production acceptance for this increment

Before marking this H2 increment complete on VM-B:

1. pull the H2 commit
2. run the full Python 3.10 backend test suite
3. confirm `pepew-pay.service` and the ElectrumX tunnel remain active
4. create one online backup while the Payment Platform remains running
5. confirm `BACKUP: PASS`
6. run the restore drill against that backup
7. confirm `RESTORE DRILL: PASS`
8. confirm the live Payment API/watcher/webhook configuration was not changed
9. confirm VM-A writer gates remain disabled

Do not run a destructive live restore merely to satisfy acceptance.

## 5. Scheduling / retention

Automatic scheduling and retention policy are deliberately not enabled by this
increment. First verify the backup and restore-drill behavior on the production
VM-B filesystem.

A later H2 increment may add a lightweight systemd timer after deciding:

- backup frequency
- local retention count / maximum age
- whether a second-host or object-storage copy is required
- disk-space guardrails

No additional database or queue infrastructure is required.
