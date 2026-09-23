# Phase F Final Cutover Runbook

Status: **COMPLETE — production authority cutover and E2E verified 2026-09-24**

Use this runbook only after the Phase F preflight, VM-A snapshot rehearsal, and
VM-B transfer verification have all passed. GitHub `main` remains the source of
truth.

## Safety invariants

- There must be exactly one authoritative Payment Platform writer.
- Do not expose ElectrumX publicly.
- Do not copy secrets into chat, shell history, GitHub, or logs.
- Do not promote the rehearsal snapshot to production.
- Do not restart VM-A `pepew-light.service` with Payment Platform feature gates still enabled after the final snapshot. After VM-B authority is validated, disable the three Payment Platform gates on VM-A and restart the Light service so legacy Light API/wallet routes remain available without creating a second writer.
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

**Do not restart `pepew-light.service` yet.** Keep it stopped until VM-B authority is running and locally validated. Then disable VM-A Payment Platform gates before restarting the Light service.

Final VM-A freeze and authoritative snapshot completed successfully on 2026-09-23:

```text
feature_gates=api:true,watcher:true,webhook:true
integrity_check=ok
payments=3
payment_transactions=3
events=8
webhook_endpoints=1
webhook_deliveries=1
enabled_webhook_endpoints=0
webhook_delivery_statuses=delivered:1
PRECHECK: PASS
sha256=25a73150da504e9eba07f7fe71f28aeefb1bc581f50d159ea25370cff2326942
SNAPSHOT: PASS
final_snapshot=/home/ubuntu/phase-f-final-20260923T155346Z.sqlite3
pepew-light.service=inactive
```

VM-A must remain stopped until VM-B authority is running and validated locally.

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

At this stage VM-B is the only authoritative writer because VM-A remains stopped.

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

Only after `pay.pepepow.net` is publicly healthy should VM-A be returned to
Light-only mode.

## 8. Return VM-A to Light-only mode with Payment compatibility proxy

Keep VM-A's old SQLite and secrets intact for rollback, but disable all
authoritative Payment Platform feature gates before restarting the shared Light
service. Existing `light.pepepow.net/pay/?payment_id=...` links must continue
working, so the post-cutover Light Nginx configuration proxies only authoritative
Payment Platform v1/webhook routes to `pay.pepepow.net`; legacy Light APIs,
wallet routes, and `/api/payment/check` stay local.

On VM-A, while `pepew-light.service` is still stopped:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only
cd backend

python3 scripts/configure_light_post_cutover.py

sudo cp -a /etc/nginx/sites-available/pepew-light \
  /etc/nginx/sites-available/pepew-light.pre-phase-f

sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/nginx/pepew-light-post-cutover \
  /etc/nginx/sites-available/pepew-light

sudo nginx -t
sudo systemctl reload nginx

sudo systemctl start pepew-light.service
sudo systemctl --no-pager --full status pepew-light.service
```

Confirm only the Payment Platform gates are disabled:

```bash
grep -E '^(PAYMENT_API_ENABLED|PAYMENT_WATCHER_ENABLED|PAYMENT_WEBHOOK_ENABLED)=' .env
```

Expected:

```text
PAYMENT_API_ENABLED=false
PAYMENT_WATCHER_ENABLED=false
PAYMENT_WEBHOOK_ENABLED=false
```

Verify local Light service health and compatibility:

```bash
curl -fsS https://light.pepepow.net/api/health
curl -fsS https://light.pepepow.net/api/status
curl -I https://light.pepepow.net/pay/
```

A migrated persisted payment capability should return the same status through
both `light.pepepow.net/api/v1/payments/<id>` and
`pay.pepepow.net/api/v1/payments/<id>`. VM-A itself must not run the payment
watcher or webhook worker after this point.

Confirm ElectrumX remains local-only on VM-A and is not publicly reachable.

Post-cutover host/service validation completed on 2026-09-23:

```text
pay.pepepow.net /api/health: PASS
pay.pepepow.net /api/status: PASS
pay.pepepow.net /: HTTP 200 PepewPay static
VM-B ElectrumX tunnel/status: connected
VM-A PAYMENT_API_ENABLED=false
VM-A PAYMENT_WATCHER_ENABLED=false
VM-A PAYMENT_WEBHOOK_ENABLED=false
VM-A pepew-light.service=active
light.pepepow.net /api/health: PASS
light.pepepow.net /api/status: PASS
light.pepepow.net /pay/: HTTP 200
```

Before webhook/payment live E2E, run the public, secret-free API boundary helper:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only
cd backend

python3 scripts/phase_f_post_cutover_acceptance.py
```

This must finish with:

```text
PHASE F POST-CUTOVER ACCEPTANCE: PASS
```

It does not create a payment and does not require or print merchant/webhook
secrets. It verifies the VM-B authoritative Payment API, the VM-A compatibility
proxy, unauthenticated create rejection, preservation of the legacy Light
`/api/payment/check` route, and exclusion of legacy Light APIs from the pay
domain.

### Post-cutover Nginx route-precedence correction

The first public acceptance run on 2026-09-23 passed health/status/static checks
but failed on the nested persisted-payment status route because
`pay.pepepow.net/api/v1/payments/<id>` returned Nginx's HTML 404 instead of the
FastAPI JSON `payment_not_found` response.

Root cause: the production virtual host used regex locations for nested Payment
API routes while a later `location ^~ /api/` legacy-API catch-all suppressed
regex evaluation.

The production configuration was corrected by making the nested authoritative
routes explicit more-specific `^~` prefixes:

```text
location ^~ /api/v1/payments/
location ^~ /api/v1/webhook-endpoints/
```

The generic `location ^~ /api/` 404 boundary remains in place so legacy Light
APIs are still not exposed on `pay.pepepow.net`. A regression test now guards
this route precedence.

## 9. Production E2E

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
- VM-A `pepew-light.service` is running in Light-only mode with Payment API/watcher/webhook gates disabled

Production webhook E2E on VM-B completed successfully on 2026-09-24:

```text
1/8 production webhook configuration: PASS
2/8 SSRF loopback rejection: PASS
3/8 endpoint create/list secret boundary: PASS
4/8 payment.created event trigger: PASS
5/8 persisted 503 -> retry transition: PASS
6/8 first real receiver HMAC verification: PASS
7/8 retry -> 204 delivered; stable IDs/body + HMAC: PASS
8/8 authenticated delivery log: PASS
WEBHOOK PRODUCTION E2E: PASS
```

The test completed without printing the merchant API key, webhook master key, or
derived endpoint signing secret. The temporary webhook endpoint/receiver cleanup
path ran as part of the helper.

Corrected public post-cutover acceptance completed successfully on 2026-09-24:

```text
1/8 pay health: PASS
2/8 pay ElectrumX status: PASS
3/8 PepewPay static root: PASS
4/8 pay authoritative payment API: PASS
5/8 light -> pay payment compatibility proxy: PASS
6/8 unauthenticated create boundary: PASS
7/8 Light legacy payment/check remains local: PASS
8/8 pay domain excludes legacy Light API: PASS
PHASE F POST-CUTOVER ACCEPTANCE: PASS
```

Final real-payment watcher acceptance completed successfully on 2026-09-24:

```text
amount=0.01 PEPEW
confirmations_required=1
status=paid_confirmed
received=0.01
confirmed=0.01
policy_confirmed=0.01
overpaid_by=0
```

The test used a newly created VM-B authoritative invoice and confirmed that the
post-cutover watcher persisted the real payment through the unconfirmed/confirmed
lifecycle to `paid_confirmed`. Capability IDs and wallet addresses are
intentionally not recorded in this runbook.

With the webhook production E2E and corrected 8/8 public acceptance also passed,
the Phase F authority migration is complete.

## 10. Rollback

If VM-B validation fails before accepting real new merchant writes:

```bash
# VM-B
sudo systemctl stop pepew-pay.service
```

Then on VM-A, restore the previous Payment Platform feature-gate settings from
the protected pre-cutover environment/configuration before starting the service.
Do not simply start VM-A while its gates remain disabled if rollback is intended.

After restoring the previous gates:

```bash
sudo systemctl start pepew-light.service
sudo systemctl is-active pepew-light.service
```

Restore public routing to VM-A as needed.

If VM-B has already accepted new payments/events after activation, do not simply
restart the old VM-A database. Reconcile the new VM-B rows first so the
single-writer ledger does not lose accepted state.

## 11. Completion

Phase F completed on 2026-09-24 with:

- VM-B authoritative for Payment API/watcher/SQLite/webhook
- `pay.pepepow.net` serving PepewPay and production Payment API routes
- production payment and webhook E2E passed after migration
- corrected 8/8 public post-cutover acceptance passed
- VM-A restored in Light-only mode with no competing Payment Platform writers
- existing `light.pepepow.net/pay/` capability links preserved through the compatibility proxy
- ElectrumX remaining private behind the controlled localhost SSH tunnel
- snapshot/transfer/recovery mechanics rehearsed and verified

A destructive rollback after VM-B has accepted newer production rows was
intentionally not exercised. In that situation, reconcile those rows before
restoring VM-A authority; never run independent writers concurrently.
