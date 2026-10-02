# Phase J1 — Off-host Payment Backup

Status: **J1b/J1c COMPLETE — J1d split-host production acceptance in progress; VM-B attempt stopped safely because VM-A administrative access was unavailable**

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

Implemented tooling:

```text
backend/scripts/payment_db_offhost_sender.py
backend/scripts/payment_db_offhost_receiver.py
backend/tests/test_payment_db_offhost.py
```

The sender:

- selects only exact complete `payment-auto-*.sqlite3` + manifest pairs;
- chooses the newest complete pair;
- reruns the existing H2 non-destructive restore drill before opening SSH;
- uses a fixed non-interactive SSH command shape with a dedicated identity;
- streams a bounded protocol over stdin rather than supplying remote paths.

The forced receiver:

- accepts only the exact automatic H2 filename shape;
- rejects path-like/arbitrary names and unexpected SSH commands;
- stages files with modes `0700` / `0600`;
- checks manifest filename/size, SHA-256, SQLite integrity/schema/counts, and
  performs a destination restore drill before promotion;
- refuses overwrite of an existing recovery point;
- removes staging/partial data on failure;
- does not expose a shell or payment authority.

Repository tests cover:

- [x] selection of only complete automatic H2 pairs
- [x] refusal of manifest/filename mismatch
- [x] checksum corruption detection
- [x] cleanup of partial/staging files after verification failure
- [x] destination restore drill before successful promotion
- [x] overwrite refusal
- [x] command/filename validation for the receive-only SSH boundary
- [x] hardened non-interactive sender SSH option construction
- [x] transfer timeout/process failure isolation (J1c)
- [x] bounded off-host retention that ignores unrelated/manual/orphan files (J1c)
- [ ] production Payment API/watcher/webhook failure-isolation acceptance (J1d)

CI closeout (2026-10-02):

- J1b implementation commit: `c17b64f276a1a0e6124d9e643809aaf941ca8692`
- J1c retention/failure-isolation implementation plus fixes culminated in `fd4d75b0945cfef1db2f5681919f8825b6c75920`
- production helper script compilation: PASS on Python 3.10
- full backend suite: **259 passed**
- no production VM-A/VM-B configuration was changed


Production acceptance must verify:

1. local H2 backup + restore drill PASS;
2. one off-host pair transferred to VM-A;
3. source and destination SHA-256/size/manifest agree;
4. destination files have restrictive permissions;
5. VM-B Payment API, watcher, webhook worker, and ElectrumX tunnel remain healthy;
6. VM-A Light/ElectrumX/PEPEPOWd services remain healthy;
7. an independent non-destructive restore drill from the off-host copy passes;
8. no temporary secret or unrestricted SSH access remains.

## 11. VM-A forced-command setup shape

J1b intentionally does not create a production key in GitHub.

During deployment, generate a new dedicated Ed25519 key on VM-B and install only
its public key on VM-A. Do not reuse the ElectrumX tunnel key.

The VM-A authorized-key entry should use a forced command equivalent to:

```text
command="/usr/bin/python3 /home/ubuntu/pepepow-electrumx-service/backend/scripts/payment_db_offhost_receiver.py --destination-dir /var/lib/pepew-pay-offhost",restrict,from="<VM-B-source-IP>" ssh-ed25519 <PUBLIC_KEY> pepew-pay-offhost
```

If the deployed OpenSSH does not support `restrict`, use explicit
`no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding` restrictions
instead. Confirm actual OpenSSH behavior on VM-A before installing the key.

The destination directory must be non-public, owned by the forced-command user,
and mode `0700` (or equivalently restrictive). The receiver writes recovery
files mode `0600`.

The receiver defaults to `--keep 14`. Retention is deliberately narrow: only
complete exact-name `payment-auto-*.sqlite3` + manifest pairs are eligible.
Manual files, malformed names, and orphan database/manifest files are ignored.
Retention runs only after the new pair has passed destination verification and
promotion.

No key is installed and no production SSH configuration is changed by the J1b
repository increment.

## 12. J1d production acceptance runbook

J1d is intentionally a **manual one-shot acceptance**. Do not install or enable
a timer during this step.

First J1d attempt (2026-10-02) stopped safely before production changes:

- VM-B SHA was `c90b8ecdab3434ec9ce2574655d7d50cd350a402`, Python 3.10.12;
- VM-B baseline health passed;
- VM-A administrative SSH access was unavailable from VM-B;
- VM-A exposed only the existing restricted ElectrumX tunnel credential/path;
- that credential was not reused because J1 requires a separate receive-only backup key;
- H2 backup, J1 key creation, authorized-key changes, off-host transfer, and timer work were not started;
- no production files, feature gates, SSH policy, or timers changed.

J1d-A VM-A receiver preparation completed on 2026-10-02:

- VM-A SHA: `a019653b747e2fc19b0a11fefafd988669feed35`
- Python 3.10.12; OpenSSH 8.9p1 with `restrict` support
- `pepew-light.service` active
- ElectrumX remained private at `127.0.0.1:50001`
- PEPEPOWd RPC remained private at `127.0.0.1:8834`
- receiver script compile PASS
- `/var/lib/pepew-pay-offhost` prepared as `ubuntu:ubuntu` mode `0700`, empty
- Payment API/watcher/webhook feature gates remained disabled on VM-A
- no timer installed
- no J1 public key installed yet

This is an access/topology prerequisite, not a backup-tooling failure. J1d is
therefore split into two host-local stages:

```text
J1d-A  VM-A local Codex:
       prepare receiver directory, inspect OpenSSH restrictions, and install
       the dedicated VM-B J1 public key as a forced receive-only key

J1d-B  VM-B local Codex:
       create/confirm the dedicated private key, run a fresh H2 backup,
       perform the one-shot copy, negative shell test, and health acceptance
```

Do not grant the J1 key unrestricted shell access merely to make deployment
easier.


J1d-B1 VM-B dedicated key preparation completed on 2026-10-02:

- VM-B SHA: `13a40b83f774a52b719ad886a115d2dc6e523e08`
- Python 3.10.12
- `pepew-pay.service`: active
- ElectrumX tunnel: active
- H2 backup timer: active
- health: PASS
- watcher health: PASS
- new dedicated J1 Ed25519 key created
- private key mode `0600`; public key mode `0644`
- J1 public-key fingerprint: `SHA256:EA02WJKZNUTXv9AjKQWvblkeF9WcZMVYPaHN7O79u98`
- ElectrumX tunnel fingerprint: `SHA256:wI3/kdmzXly35io6Z0J+Lv7c6ho1J0kcpPPPtPZIpug`
- fingerprints differ: PASS
- verified VM-B public source IP: `192.9.179.139`
- no backup transfer performed
- no feature-gate or timer change

The public key itself is operational configuration and is not committed to GitHub.

### 12.1 Common preflight

On both VM-A and VM-B:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git fetch origin
git checkout main
git pull --ff-only origin main
git rev-parse HEAD
python3 --version
```

Expected GitHub main at the start of this acceptance must be the current
documented J1d-ready commit or a later green `main`. Do not deploy from a
locally modified checkout.

On VM-B confirm:

```bash
systemctl is-active pepew-pay.service
systemctl is-active pepew-electrumx-tunnel.service
systemctl is-active pepew-pay-backup.timer
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/status
ls -ld /var/lib/pepew-pay/backups
```

On VM-A confirm:

```bash
systemctl is-active pepew-light.service
ss -lnt | grep -E '127\.0\.0\.1:(50001|8000|8088)'
```

Do not change Payment API/watcher/webhook feature gates.

### 12.2 VM-A destination preparation

On VM-A:

```bash
sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/pepew-pay-offhost
sudo chown ubuntu:ubuntu /var/lib/pepew-pay-offhost
sudo chmod 0700 /var/lib/pepew-pay-offhost

test "$(stat -c '%U:%G %a' /var/lib/pepew-pay-offhost)" = "ubuntu:ubuntu 700"
```

Verify the receiver script compiles:

```bash
cd /home/ubuntu/pepepow-electrumx-service/backend
python3 -m py_compile scripts/payment_db_offhost_receiver.py
```

### 12.3 VM-B dedicated transfer key

On VM-B create a dedicated key **only if it does not already exist**:

```bash
install -d -m 0700 /home/ubuntu/.ssh

test ! -e /home/ubuntu/.ssh/pepew-pay-offhost-ed25519
ssh-keygen -t ed25519 \
  -f /home/ubuntu/.ssh/pepew-pay-offhost-ed25519 \
  -N '' \
  -C 'pepew-pay-offhost-vm-b-to-vm-a'

chmod 0600 /home/ubuntu/.ssh/pepew-pay-offhost-ed25519
chmod 0644 /home/ubuntu/.ssh/pepew-pay-offhost-ed25519.pub
```

Do not print or copy the private key. Only the `.pub` value may be transferred
to VM-A.

Do not reuse:

- the ElectrumX tunnel key;
- an administrator shell key;
- a GitHub deploy key.

### 12.4 VM-A restricted authorized key

Before installing the key, determine the current VM-B public source IP from a
trusted cloud/operator source. Do not substitute the VM-B private IP.

On VM-A, inspect the server capability first:

```bash
sshd -V 2>&1 || true
man sshd_config >/dev/null 2>&1 || true
```

Append exactly one restricted J1 key entry to
`/home/ubuntu/.ssh/authorized_keys`.

Preferred shape when OpenSSH supports `restrict`:

```text
from="<CURRENT_VM_B_PUBLIC_IP>",restrict,command="/usr/bin/python3 /home/ubuntu/pepepow-electrumx-service/backend/scripts/payment_db_offhost_receiver.py --destination-dir /var/lib/pepew-pay-offhost --keep 14" ssh-ed25519 <PUBLIC_KEY> pepew-pay-offhost-vm-b-to-vm-a
```

If `restrict` is unavailable, replace it with all of:

```text
no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding
```

Do not remove or alter existing SSH keys. Preserve:

```bash
chmod 0700 /home/ubuntu/.ssh
chmod 0600 /home/ubuntu/.ssh/authorized_keys
```

The J1 entry must provide no unrestricted shell and no forwarding.

### 12.5 VM-B known-host pinning

Use an operator-verified VM-A SSH host key/fingerprint. Do not accept a changed
host key blindly.

The sender intentionally uses:

```text
StrictHostKeyChecking=yes
BatchMode=yes
IdentitiesOnly=yes
PasswordAuthentication=no
KbdInteractiveAuthentication=no
```

Therefore the correct VM-A host key must already exist in the VM-B
`known_hosts` file.

### 12.6 Re-run H2 local backup acceptance

On VM-B, run the existing H2 oneshot before J1 transfer:

```bash
sudo systemctl start pepew-pay-backup.service
sudo systemctl status pepew-pay-backup.service --no-pager
journalctl -u pepew-pay-backup.service -n 80 --no-pager
```

Require:

```text
BACKUP MAINTENANCE: PASS
restore_drill=pass
```

If H2 does not pass, stop J1d. Do not transfer an older pair merely to make the
acceptance pass.

### 12.7 Manual one-shot off-host copy

On VM-B, use the exact destination identity chosen for VM-A:

```bash
cd /home/ubuntu/pepepow-electrumx-service/backend

python3 scripts/payment_db_offhost_sender.py \
  --backup-dir /var/lib/pepew-pay/backups \
  --host ubuntu@<VM_A_SSH_HOST_OR_IP> \
  --identity-file /home/ubuntu/.ssh/pepew-pay-offhost-ed25519
```

Require final output:

```text
source_restore_drill=pass
OFFHOST COPY: PASS
```

The receiver-side output relayed by the sender must also show:

```text
receiver_destination_restore_drill=pass
receiver_OFFHOST RECEIVE: PASS
```

Do not treat an SSH exit code alone as proof of backup validity.

### 12.8 VM-A destination verification

On VM-A:

```bash
find /var/lib/pepew-pay-offhost -maxdepth 1 -type f \
  -name 'payment-auto-*' -printf '%f %m %u:%g %s bytes\n' | sort

find /var/lib/pepew-pay-offhost -maxdepth 1 \
  -type d -name '.pepew-offhost-incoming-*' -print
```

Require:

- one newly copied complete database/manifest pair;
- database and manifest mode `0600`;
- owner/group `ubuntu:ubuntu`;
- no leftover `.pepew-offhost-incoming-*` directory.

Do not print the SQLite content.

### 12.9 Service health after transfer

VM-B:

```bash
systemctl is-active pepew-pay.service
systemctl is-active pepew-electrumx-tunnel.service
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/status
journalctl -u pepew-pay.service -n 80 --no-pager
```

VM-A:

```bash
systemctl is-active pepew-light.service
ss -lnt | grep -E '127\.0\.0\.1:(50001|8000|8088)'
journalctl -u pepew-light.service -n 80 --no-pager
```

Acceptance requires no new SQLite, watcher, webhook, ElectrumX, Light API, or
SSH-forwarding errors caused by J1.

### 12.10 Negative access check

From VM-B, the dedicated J1 key must **not** provide a shell:

```bash
ssh -T \
  -i /home/ubuntu/.ssh/pepew-pay-offhost-ed25519 \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  ubuntu@<VM_A_SSH_HOST_OR_IP> 'id'
```

Because VM-A forces the receiver, this must not execute `id`. A nonzero result
or receiver protocol failure is acceptable; shell output containing UID/GID is
a failure.

Do not test port forwarding against a production private endpoint. The
authorized-key restriction itself must be inspected and recorded instead.

### 12.11 J1d pass evidence

Record only non-secret evidence:

```text
VM-A SHA
VM-B SHA
Python versions
H2 oneshot PASS
source restore drill PASS
off-host copy PASS
destination restore drill PASS
source/destination filename
size_bytes
sha256
destination modes/owner
VM-B service/tunnel/health PASS
VM-A Light/private-listener health PASS
dedicated key shell check PASS
timer installed/enabled: NO
```

Do not record:

- private key material;
- merchant API/webhook secrets;
- wallet mnemonic/private key;
- payment row contents;
- addresses, payment IDs, txids, merchant references.

### 12.12 J1d rollback

If J1d fails:

1. keep the valid local H2 backup;
2. do not modify the authoritative VM-B SQLite database;
3. remove only a partial J1 destination staging directory if the receiver did
   not already clean it;
4. remove/disable only the dedicated J1 authorized-key entry if necessary;
5. preserve any already verified complete off-host pair as recovery evidence;
6. do not change Payment Platform feature gates;
7. do not install a timer.

## 13. J1d-B3 production acceptance closeout

J1d-B3 live one-shot production acceptance completed on 2026-10-02:

- VM-B SHA: `21364083214fc3e93c3fcc2b33e73b1f18dfa3e3`
- Python 3.10.12
- Payment service remained active before/after
- ElectrumX tunnel remained active before/after
- watcher remained connected and healthy before/after
- fresh H2 backup maintenance: PASS
- fresh H2 local restore drill: PASS
- off-host sender: PASS
- receiver: PASS
- copied recovery point: `payment-auto-20261002T145235Z.sqlite3`
- size: 794624 bytes
- SHA-256: `cdb1a386afecc14789efaa64cb2a5d6f36d2f5755ef2c28dbcfdb361e7cd0eab`
- dedicated J1 key shell-negative test: PASS
- Payment Platform post-transfer health: PASS
- no new SQLite/watcher/webhook/tunnel errors
- no J1 timer installed/enabled
- no Payment Platform feature-gate change
- no material discrepancies

J1d is therefore complete. J1 remains open only for J1e: an independent
non-destructive restore drill performed from the VM-A off-host copy.

## 13. Rollback

J1 adds no payment authority.

Rollback is therefore limited to disabling/removing the off-host transfer
oneshot/timer and its dedicated credential after preserving any valid copies.

Do not modify the authoritative VM-B database as part of J1 rollback.
