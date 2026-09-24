# Phase G Production Deployment and Acceptance

Status: **READY FOR VM-B DEPLOYMENT**

This runbook deploys the Phase G merchant-integration hardening already merged into
GitHub `main`:

- durable payment-create `Idempotency-Key`
- authenticated merchant payment recovery/listing
- unique optional `merchant_reference`
- additive merchant reference in new Payment Event Envelope v1 records

The deployment changes the authoritative SQLite schema by adding one nullable
`payments.merchant_reference` column and one partial unique index. Take a verified
snapshot before starting the new code.

## Scope

Run these steps on **VM-B only**.

VM-B remains the sole authoritative Payment Platform writer:

```text
pay.pepepow.net
Payment API
payment watcher
SQLite
webhook worker
PepewPay
```

Do **not** enable Payment API, watcher, or webhook writers on VM-A.

## 1. Pre-deployment snapshot

From the VM-B repository:

```bash
cd /home/ubuntu/pepepow-electrumx-service

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p /var/lib/pepew-pay/backups

sudo systemctl stop pepew-pay

python3 backend/scripts/phase_f_sqlite_snapshot.py \
  --service-name pepew-pay.service \
  --output "/var/lib/pepew-pay/backups/pre-phase-g-$STAMP.sqlite3"
```

The snapshot helper must report:

```text
integrity_check=ok
...
sha256=<value>
SNAPSHOT: PASS
```

If snapshot verification fails, do not continue.

The helper refuses to snapshot while `pepew-pay.service` is active, so the
SQLite copy cannot silently race an authoritative writer.

## 2. Pull and test GitHub main

Keep the service stopped while updating and testing:

```bash
git status --short
git pull --ff-only

source backend/.venv/bin/activate
pytest -q backend
python3 -m py_compile backend/scripts/phase_g_production_acceptance.py
```

Expected result: the full backend test suite passes.

Do not continue with a dirty/conflicted worktree or failed tests.

## 3. Start VM-B

```bash
sudo systemctl start pepew-pay
sudo systemctl --no-pager --full status pepew-pay
```

Then check recent service errors without printing the protected environment file:

```bash
sudo journalctl -u pepew-pay -n 100 --no-pager
```

The SQLite migration is intentionally additive:

```text
payments.merchant_reference TEXT NULL
unique index on non-null merchant_reference
```

Existing payment rows remain valid with `merchant_reference = NULL`.
Historical event bodies are not rewritten.

## 4. Run Phase G acceptance

The helper defaults to the public HTTPS endpoint, so it checks the VM-B FastAPI
service through production Nginx:

```bash
cd /home/ubuntu/pepepow-electrumx-service/backend
source .venv/bin/activate

python3 scripts/phase_g_production_acceptance.py
```

The helper reads these values from protected `backend/.env` without printing
them:

```text
PAYMENT_CREATE_API_KEY
PAYMENT_DB_PATH
PAYMENT_API_ENABLED
PAYMENT_WATCHER_ENABLED
```

It reuses an existing payment address internally but does not print the address.

### Webhook safety

The acceptance creates one one-atom test payment. A `payment.created` event is
therefore generated.

By default, the script **refuses to create the test payment when any webhook
endpoint is enabled**. It does not automatically disable merchant endpoints.

If enabled webhook endpoints are expected and their receiver may safely receive
one clearly labeled production-acceptance `payment.created` event, rerun only
after explicit review:

```bash
python3 scripts/phase_g_production_acceptance.py --allow-active-webhooks
```

Do not use this flag merely to bypass the guard.

## 5. Acceptance contract

A successful run prints ten PASS checks:

```text
1/10 VM-B production config + safe test preflight: PASS
2/10 pay health + ElectrumX status: PASS
3/10 anonymous merchant enumeration blocked: PASS
4/10 authenticated listing + SQLite merchant_reference migration: PASS
5/10 merchant_reference + Idempotency-Key create: PASS
6/10 same-key/same-request replay returns original payment: PASS
7/10 same-key/different-request conflict: PASS
8/10 duplicate merchant_reference conflict: PASS
9/10 authenticated recovery + public metadata privacy: PASS
10/10 exactly one payment.created event with merchant reference: PASS

PHASE G PRODUCTION ACCEPTANCE: PASS
```

The test validates these production properties:

- anonymous `GET /api/v1/payments` returns 401
- authenticated listing works through Nginx
- the live SQLite database has the new column and unique index
- first create returns one payment
- repeating the same `Idempotency-Key` and same request returns the same payment
- changing the request under the same idempotency key returns 409
- a new idempotency key cannot reuse the same `merchant_reference`
- exact authenticated reference recovery returns one payment
- the public capability status does not expose `merchant_reference` or
  `Idempotency-Key`
- SQLite contains exactly one `payment.created` event for the acceptance payment
- the persisted new event contains the merchant reference

The helper does not print:

- merchant API key
- payment capability ID
- payment capability URL
- payment address

The test payment is intentionally unfunded, requests one atom, expires after ten
minutes, and remains in SQLite as an auditable production acceptance record.

## 6. Post-acceptance checks

```bash
sudo systemctl is-active pepew-pay
sudo systemctl is-active pepew-electrumx-tunnel
sudo nginx -t
sudo journalctl -u pepew-pay -n 100 --no-pager
```

Expected:

```text
pepew-pay: active
pepew-electrumx-tunnel: active
nginx config: valid
no new traceback / migration / SQLite errors
```

No deployment step in this runbook requires changing VM-A.

## Failure / rollback boundary

If the new service fails **before it accepts any new authoritative writes**, the
pre-Phase-G snapshot is available under:

```text
/var/lib/pepew-pay/backups/pre-phase-g-<timestamp>.sqlite3
```

Do not blindly restore an old snapshot after VM-B has accepted newer payments,
events, webhook state, or deliveries. Once post-snapshot writes exist, rollback
requires reconciliation rather than file replacement.

If acceptance fails after the test payment was created, preserve the database
and inspect the specific failed invariant first. Do not move Payment Platform
authority back to VM-A as a routine rollback mechanism.
