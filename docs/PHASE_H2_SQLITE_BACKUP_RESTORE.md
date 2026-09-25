# Phase H2 — SQLite Backup / Restore Operational Hardening

Status: **IN PROGRESS — online backup/restore production accepted; bounded automation implemented, production enablement pending**

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
backend/scripts/payment_db_backup_maintenance.py
deploy/systemd/pepew-pay-backup.service
deploy/systemd/pepew-pay-backup.timer
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

## 4. First-increment production acceptance

VM-B production acceptance passed on 2026-09-25 at GitHub main
`e5565f3caceb82009b63273cecfb2dc328d03f16`.

Verified production results:

- Python 3.10.12
- 233 backend tests passed
- `pepew-pay.service` remained active/running
- localhost ElectrumX tunnel remained active
- online backup completed with `BACKUP: PASS` while the Payment Platform stayed online
- backup SQLite integrity check passed
- backup size was 446,464 bytes
- SHA-256 was generated
- backup and manifest were both mode `0600`
- manifest privacy check passed
- non-destructive restore drill completed with `RESTORE DRILL: PASS`
- live authoritative database was not replaced
- local and public health remained healthy
- payment watcher remained enabled/running/connected/healthy, not degraded, and not stale
- no recent SQLite/service errors were observed
- production configuration was unchanged
- VM-A was not touched
- no automatic timer or retention policy was installed during the acceptance

This closes the manual online-backup/restore-drill increment.

## 5. Bounded automatic backup policy

The second H2 increment keeps automation intentionally small and local:

```text
frequency              daily
schedule               03:00 UTC + up to 30 minutes randomized delay
retention              14 complete automatic backup/manifest pairs
free-space guardrail   512 MiB before creating a new backup
verification           restore drill after every automatic backup
production downtime    none
external infrastructure none
```

The maintenance helper creates files named:

```text
payment-auto-YYYYMMDDTHHMMSSZ.sqlite3
payment-auto-YYYYMMDDTHHMMSSZ.sqlite3.manifest.json
```

A maintenance run performs this sequence:

1. validate the configured live database path exists
2. verify at least 512 MiB is free in the backup filesystem
3. create one online SQLite backup
4. verify its manifest/checksum/schema/integrity
5. perform a temporary non-destructive restore drill
6. only after those steps succeed, prune old complete automatic pairs down to 14

Retention deletion is deliberately narrow:

- only `payment-auto-*.sqlite3` files with their matching manifest are eligible
- manual H2 backups are not deleted
- Phase F/G snapshots are not deleted
- orphan database or manifest files are not automatically deleted
- a failed backup or restore drill does not trigger retention deletion

## 6. systemd automation

Repository units:

```text
deploy/systemd/pepew-pay-backup.service
deploy/systemd/pepew-pay-backup.timer
```

The oneshot service runs as `ubuntu`, uses a restrictive umask, low process
priority and idle I/O scheduling, has no network requirement, and can write only
to the backup directory under its hardened systemd filesystem policy.

The timer uses:

```text
OnCalendar=*-*-* 03:00:00 UTC
RandomizedDelaySec=30m
Persistent=true
```

Do not enable these units until the second-increment production acceptance has
verified the unit files, a manual oneshot run, retention behavior, free-space
guardrail visibility, and unchanged Payment Platform health.

### WAL-mode systemd sandbox note

The first VM-B automation acceptance attempt on 2026-09-25 failed safely before
creating an automatic backup. `pepew-pay.service` and the ElectrumX tunnel
remained healthy, the timer stayed disabled, and no production configuration or
database contents changed.

Root cause: the initial hardened backup unit exposed only the backup directory as
writable under `ProtectSystem=strict`. The authoritative SQLite database runs in
WAL mode, and a read-only SQLite connection may still need to create or update
the `payments.sqlite3-shm` coordination sidecar. The sandbox therefore prevented
SQLite from opening the live WAL database and the oneshot exited with
`unable to open database file`.

The corrected unit keeps the VM-B state directory writable only inside the
backup service's private mount namespace so SQLite can coordinate via `-shm`,
while explicitly pinning these authoritative files read-only in that namespace:

```text
payments.sqlite3
payments.sqlite3-wal       # when present
payments.sqlite3-journal   # when present
```

The `-shm` sidecar is intentionally not read-only. This change does not alter
the real filesystem permissions seen by `pepew-pay.service`, and it does not
make the live database writable through the backup script itself; the script
continues opening the source with SQLite `mode=ro`.

## 7. Off-host recovery copy

H2 does not yet add an object-storage dependency or another backup daemon.

Local automatic backups protect against application/database-level failures but
do not protect against loss of VM-B or its boot/block volume. An off-host copy is
therefore still a separate resilience decision.

The preferred next evaluation is a simple encrypted or provider-native
second-copy mechanism that does not add a continuously running service. It must
be assessed against actual VM-B/OCI capabilities and storage costs before being
enabled.

No additional database or queue infrastructure is required.
