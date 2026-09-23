# Phase F Final Cutover Runbook

Status: **READY FOR FINAL CUTOVER — rehearsal transfer/verification passed**

Use this runbook only after the Phase F preflight, VM-A snapshot rehearsal, and
VM-B transfer verification have all passed. GitHub `main` remains the source of
truth.

## Safety invariants

- There must be exactly one authoritative Payment Platform writer.
- Do not expose ElectrumX publicly.
- Do not copy secrets into chat, shell history, GitHub, or logs.
- Do not promote the rehearsal snapshot to production.
- Do not restart VM-A `pepew-light.service` after the final snapshot unless
  performing rollback.
- Do not enable VM-B writers until the final snapshot has passed destination
  verification and is installed at the production DB path.
- Keep mnemonic/private-key/signing code client-side.

## 1. Stage VM-B before the maintenance window

VM-B may remain in bootstrap mode while these checks are performed.

Update source:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only
```

Confirm the tunnel and bootstrap backend remain healthy:

```bash
sudo systemctl is-active pepew-electrumx-tunnel.service
sudo systemctl is-active pepew-pay.service
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/status
```

Confirm the static PepewPay artifact is already staged:

```bash
test -f /var/www/pepewpay/index.html && echo "PepewPay static: READY" || echo "PepewPay static: MISSING"
```

If missing, deploy the current `pepewpay-dist` artifact before the final freeze.
Use the repository-scoped read-only deploy key or transfer a CI-built artifact
through the administrator's encrypted path. Do not install Node.js merely to
build on VM-B.

The artifact verified on 2026-09-23 reports:

```text
source_commit=3d38810fedce470be5ad3a412815b40c609eb258
built_by=GitHub Actions
```

At verification time, that source commit is identical to devkit `main` and
includes the same-origin `/api` status configuration.

The production Nginx configuration is already tracked at:

```text
deploy/nginx/pepew-pay
```

Do not switch from the bootstrap virtual host until the backend authority has
moved and local validation has passed.

PepewPay static staging on VM-B passed on 2026-09-23. The verified
GitHub Actions artifact for devkit source commit
`3d38810fedce470be5ad3a412815b40c609eb258` is present under
`/var/www/pepewpay`, and Nginx configuration validation passed. VM-A remained
authoritative during this staging step.

## 2. VM-A final preflight and freeze

On VM-A:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only
cd backend

python3 scripts/phase_f_cutover_preflight.py --require-no-enabled-webhooks
```

Require:

```text
integrity_check=ok
enabled_webhook_endpoints=0
PRECHECK: PASS
```

Create a final snapshot path, stop the authoritative service, and snapshot:

```bash
FINAL="/home/ubuntu/phase-f-final-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"

sudo systemctl stop pepew-light.service
sudo systemctl is-active pepew-light.service || true

python3 scripts/phase_f_sqlite_snapshot.py \
  --output "$FINAL" \
  --require-no-enabled-webhooks

echo "final_snapshot=$FINAL"
```

Require `SNAPSHOT: PASS`. Record the printed SHA-256.

**Do not restart `pepew-light.service` after this point unless rolling back.**

## 3. Transfer the final snapshot

Transfer the final snapshot through the administrator's authenticated encrypted
SSH/SCP path. The restricted ElectrumX tunnel key must not be repurposed for
file transfer.

Place the transferred file on VM-B under `/home/ubuntu/` first. Do not copy it
directly into the production DB path.

## 4. VM-B verify and install the final database

Stop the bootstrap backend before installing authority:

```bash
sudo systemctl stop pepew-pay.service
sudo systemctl is-active pepew-pay.service || true
```

Verify the transferred file:

```bash
cd /home/ubuntu/pepepow-electrumx-service/backend

python3 scripts/phase_f_verify_snapshot.py \
  /home/ubuntu/<FINAL_SNAPSHOT_FILENAME>.sqlite3 \
  --sha256 <SOURCE_SHA256> \
  --require-no-enabled-webhooks
```

Require `VERIFY: PASS` and the same row counts printed on VM-A.

Install it with restrictive ownership and permissions:

```bash
sudo install -d -o ubuntu -g ubuntu -m 0750 /var/lib/pepew-pay

sudo install -o ubuntu -g ubuntu -m 0600 \
  /home/ubuntu/<FINAL_SNAPSHOT_FILENAME>.sqlite3 \
  /var/lib/pepew-pay/payments.sqlite3
```

Verify the installed copy again:

```bash
python3 scripts/phase_f_verify_snapshot.py \
  /var/lib/pepew-pay/payments.sqlite3 \
  --sha256 <SOURCE_SHA256> \
  --require-no-enabled-webhooks
```

## 5. Prepare VM-B production environment

While `pepew-pay.service` is still stopped:

```bash
python3 scripts/configure_payment_production.py
python3 scripts/configure_webhook_production.py
```

The first helper:

- verifies the production SQLite database read-only
- changes `PAYMENT_DB_PATH` to `/var/lib/pepew-pay/payments.sqlite3`
- enables Payment API and watcher
- generates a strong merchant API key only if absent
- refuses to run while `pepew-pay.service` is active
- writes `backend/.env` mode `0600`
- never prints the API key

The webhook helper generates/preserves the webhook master key, enables the
worker, writes the same protected environment file, and never prints the master
key.

## 6. Start VM-B authority and validate locally

Start VM-B:

```bash
sudo systemctl start pepew-pay.service
sudo systemctl --no-pager --full status pepew-pay.service
```

Require:

```bash
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/status
```

Confirm feature gates without printing secret values:

```bash
grep -E '^(PAYMENT_API_ENABLED|PAYMENT_WATCHER_ENABLED|PAYMENT_WEBHOOK_ENABLED|PAYMENT_DB_PATH)=' .env
```

Expected:

```text
PAYMENT_API_ENABLED=true
PAYMENT_WATCHER_ENABLED=true
PAYMENT_WEBHOOK_ENABLED=true
PAYMENT_DB_PATH=/var/lib/pepew-pay/payments.sqlite3
```

At this stage VM-B is the only authoritative writer because VM-A remains
stopped.

## 7. Switch VM-B Nginx from bootstrap to production

Only after local backend validation and static artifact readiness:

```bash
sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/nginx/pepew-pay \
  /etc/nginx/sites-available/pepew-pay

sudo ln -sfn /etc/nginx/sites-available/pepew-pay \
  /etc/nginx/sites-enabled/pepew-pay

sudo nginx -t
sudo systemctl reload nginx
```

Then verify public boundaries:

```bash
curl -fsS https://pay.pepepow.net/api/health
curl -fsS https://pay.pepepow.net/api/status
curl -I https://pay.pepepow.net/
```

Confirm ElectrumX remains local-only on VM-A and is not publicly reachable.

## 8. Production E2E

Use an existing migrated payment ID as the reference for webhook E2E so no
address has to be copied into chat:

```bash
cd /home/ubuntu/pepepow-electrumx-service/backend

python3 scripts/webhook_production_e2e.py \
  --reference-payment-id pay_<existing-migrated-payment-id>
```

The helper reads the merchant/webhook secrets from the protected environment
file and does not print them.

Also verify:

- unauthenticated Payment API create -> HTTP 401
- authenticated create -> HTTP 201 using the local secret without echoing it
- read-only status GET works
- watcher remains connected through the localhost SSH tunnel
- a new small real payment reaches `paid_unconfirmed` then `paid_confirmed`
- PepewPay at `https://pay.pepepow.net/` shows the same transition
- VM-A `pepew-light.service` remains stopped during acceptance

## 9. Rollback

If VM-B validation fails before accepting real new merchant writes:

```bash
# VM-B
sudo systemctl stop pepew-pay.service
```

Then on VM-A:

```bash
sudo systemctl start pepew-light.service
sudo systemctl is-active pepew-light.service
```

Restore public routing to VM-A as needed.

If VM-B has already accepted new payments/events after activation, do not simply
restart the old VM-A database. Reconcile the new VM-B rows first so the
single-writer ledger does not lose accepted state.

## 10. Completion

Phase F is complete only after:

- VM-B is authoritative for Payment API/watcher/SQLite/webhook
- `pay.pepepow.net` serves PepewPay and production Payment API routes
- payment and webhook E2E pass after migration
- VM-A no longer runs competing Payment Platform writers
- ElectrumX remains private
- rollback path has been validated
