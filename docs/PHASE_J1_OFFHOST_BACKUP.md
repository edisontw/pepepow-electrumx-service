# Phase J1 — Off-host Payment Backup

Status: **J1a DESIGN COMPLETE — implementation not yet deployed**

Last updated: 2026-10-02

This document defines the first off-host second-copy design for the authoritative
Payment Platform SQLite state.

GitHub `main` remains the source of truth. Production changes must not be made
until the corresponding implementation/tests and deployment acceptance steps are
ready.

## 1. Scope

VM-B (`pay.pepepow.net`) remains the sole authoritative Payment Platform writer.

The existing H2 process already creates a verified local backup pair:

```text
payment-auto-<timestamp>.sqlite3
payment-auto-<timestamp>.sqlite3.manifest.json
```

Each automatic H2 run performs:

```text
online SQLite backup
  -> integrity/schema/count verification
  -> SHA-256 + manifest
  -> non-destructive local restore drill
  -> bounded local retention
```

J1 adds one passive second copy **after** that local sequence succeeds.

J1 must not copy the live `payments.sqlite3` file directly.

## 2. Initial destination decision

Selected initial destination:

```text
VM-B authoritative Payment Platform
   |
   | one-shot SSH transfer of one completed H2 pair
   v
VM-A off-host backup directory
```

VM-A is selected for the first increment because:

- it is already an operator-controlled production host;
- it is a different host/failure domain from the authoritative VM-B filesystem;
- the backup is small enough that one daily bounded transfer is negligible;
- SSH already exists operationally between the hosts;
- no new database, object-storage client, queue, backup daemon, or continuously
  running process is required.

This first increment protects against loss of VM-B or its local volume. It is
not claimed to protect against a provider/account/region-wide loss.

A provider-native or separately administered third copy may be considered later
if the operational need justifies it.

## 3. Explicit exclusions

Do not use:

- edison2 / `pepepow.net` test host as production backup storage;
- the existing ElectrumX tunnel credential for backup transfer;
- public HTTP/HTTPS upload endpoints;
- a writable network share mounted into the Payment Platform;
- rsync/backup daemons that run continuously;
- Redis, PostgreSQL, object databases, queues, or other unrelated infrastructure.

The VM-A copy is recovery material only. VM-A must remain Light-only and must
not become a second Payment Platform writer.

## 4. SSH trust boundary

J1b must create a **dedicated backup-transfer credential**.

The credential must be separate from the ElectrumX tunnel key.

VM-A authorization should be restricted as far as the deployed OpenSSH version
allows:

- source restricted to the expected VM-B public source address where practical;
- no interactive shell;
- no PTY;
- no agent forwarding;
- no X11 forwarding;
- no port forwarding;
- forced receiver command or an equivalently narrow receive-only mechanism;
- write access only to the dedicated off-host backup directory.

The receiver must never execute arbitrary filenames or shell fragments supplied
by the sender.

No private SSH key, token, merchant API key, webhook key, wallet mnemonic, or
private key may be committed or printed.

## 5. Data selection

Only a complete, already-verified H2 automatic pair is eligible:

```text
payment-auto-YYYYMMDDTHHMMSSZ.sqlite3
payment-auto-YYYYMMDDTHHMMSSZ.sqlite3.manifest.json
```

Before transfer, J1b must require that:

1. both files exist;
2. the manifest parses;
3. the manifest names the selected database file;
4. source size and SHA-256 match the manifest;
5. the local H2 restore drill for that backup has succeeded in the same
   maintenance run.

Manual/Phase F/Phase G snapshots are not automatically selected.

## 6. Transfer semantics

The transfer must be one-shot and failure-isolated.

Required sequence:

```text
select newest successful H2 pair
  -> source re-verify
  -> transfer to VM-A temporary names
  -> destination fsync/close
  -> destination SHA-256/size/manifest verify
  -> atomically promote complete pair
  -> only then mark OFFHOST COPY: PASS
```

A connection failure, checksum mismatch, partial file, or VM-A failure must:

- leave the Payment Platform running;
- leave the successful local H2 backup intact;
- return non-zero for the off-host step;
- avoid pruning the source backup;
- avoid treating a partial destination file as a recovery point.

No payment, address, txid, webhook URL, merchant reference, or row data should
be printed by the transfer tooling.

## 7. Destination storage

Use a dedicated VM-A directory with restrictive ownership and permissions.

The exact path should be finalized during J1b deployment preparation, but it
must not be inside a public web root or PEPEPOWd/ElectrumX data directory.

Destination retention applies only to complete J1-managed backup/manifest pairs.
It must not delete unrelated files.

The first policy should remain bounded and simple. Reusing the H2 count of 14
complete daily pairs is the default unless VM-A free-space inspection shows a
reason to choose a different bound.

## 8. Restore verification

J1 is not complete merely because a file was copied.

At least one VM-A copy must be copied/read back into a temporary recovery
location and pass the existing non-destructive H2 checks:

- source manifest verification;
- SHA-256;
- SQLite integrity;
- required schema;
- bounded table counts;
- temporary SQLite backup/restore drill.

The live VM-B database must never be replaced during this acceptance.

## 9. Resource boundary

The production hosts are single-core ARM64 machines.

J1 must therefore use:

- one transfer per completed H2 cycle at most;
- low CPU/I/O priority where practical;
- bounded timeouts;
- no continuous polling;
- no background compression unless measurement shows it materially helps;
- no Node.js runtime requirement.

The observed H2 backup size was about 446 KiB during first acceptance, so
compression is not initially justified.

## 10. J1b implementation acceptance

Before production deployment, repository tests must cover at least:

- selection of only complete automatic H2 pairs;
- refusal of manifest/filename mismatch;
- checksum/size mismatch;
- transfer timeout/failure;
- cleanup or quarantine of partial destination files;
- destination verification before promotion;
- bounded retention that ignores unrelated/manual files;
- command/filename validation for the receive-only SSH boundary;
- no change to Payment API/watcher/webhook behavior on transfer failure.

Production acceptance must verify:

1. local H2 backup + restore drill PASS;
2. one off-host pair transferred to VM-A;
3. source and destination SHA-256/size/manifest agree;
4. destination files have restrictive permissions;
5. VM-B Payment API, watcher, webhook worker, and ElectrumX tunnel remain healthy;
6. VM-A Light/ElectrumX/PEPEPOWd services remain healthy;
7. an independent non-destructive restore drill from the off-host copy passes;
8. no temporary secret or unrestricted SSH access remains.

## 11. Rollback

J1 adds no payment authority.

Rollback is therefore limited to disabling/removing the off-host transfer
oneshot/timer and its dedicated credential after preserving any valid copies.

Do not modify the authoritative VM-B database as part of J1 rollback.
