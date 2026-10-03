# Phase K — Production Acceptance

Status: **FUNCTIONAL PRODUCTION PASS — multi-merchant smoke complete; final J1 recovery pending VM-A receiver update/verification**

Last updated: 2026-10-03

This runbook is the K5 production sequence for multi-merchant credential isolation.

VM-B remains the only authoritative Payment Platform writer. VM-A remains Light-only and is used only for the already accepted J1 off-host recovery copy.

## 1. Safety boundaries

- Do not paste PAYMENT_CREATE_API_KEY, scoped merchant credentials, webhook master key, webhook signing secrets, mnemonic, or private keys into chat.
- Keep PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false through migration and post-migration legacy acceptance.
- Stop pepew-pay.service before the explicit schema migration or auth-gate environment change.
- Do not replace the live database from a backup during acceptance.
- Do not retire PAYMENT_CREATE_API_KEY during the migration itself.
- If any migration/ownership/health check fails, keep scoped auth disabled and restore service using the unchanged legacy credential path.

## 2. Repository/runtime baseline

On VM-B:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull --ff-only
git status --short
python3 -m pytest -q backend/tests
```

Confirm the checked-out main SHA matches GitHub main and the working tree is clean.

## 3. Fresh pre-migration recovery point

Create a fresh H2 automatic backup while pepew-pay.service remains online:

```bash
sudo systemctl start pepew-pay-backup.service
sudo systemctl --no-pager --full status pepew-pay-backup.service
```

The oneshot must finish successfully with BACKUP MAINTENANCE: PASS / restore_drill=pass.

Then send the newest completed H2 pair to the already accepted VM-A J1 receiver using the existing dedicated receive-only J1 SSH identity and host target. Reuse the accepted J1 configuration; do not create or paste a new key in chat.

Required result:

```text
source_restore_drill=pass
OFFHOST COPY: PASS
```

Do not proceed until both the fresh local H2 pair and off-host J1 copy are verified.

## 4. Stop authority and migrate

```bash
sudo systemctl stop pepew-pay.service
sudo systemctl is-active pepew-pay.service
```

The second command must report inactive/failed rather than active.

Run:

```bash
python3 backend/scripts/phase_k_production_migration.py
```

Required result includes:

```text
schema_profile=phase_k_merchant_v1
sqlite_integrity=ok
foreign_key_check=ok
authoritative_row_counts=preserved
payment_business_data=preserved
historical_event_payloads=preserved
webhook_endpoint_records=preserved
merchant_ownership_checks=pass
scoped_merchant_auth=still_disabled
PHASE K MIGRATION: PASS
```

The helper refuses to run if the service is active, the legacy API key is missing, scoped auth is already enabled, or the latest H2 pair cannot pass the restore drill.

## 5. Restart with legacy auth only

```bash
sudo systemctl start pepew-pay.service
sudo systemctl --no-pager --full status pepew-pay.service
python3 backend/scripts/phase_k_post_migration_acceptance.py
```

Required final line:

```text
PHASE K POST-MIGRATION ACCEPTANCE: PASS
```

This acceptance is read-only. It verifies Phase K schema/ownership, all pre-existing rows owned by mrc_legacy_v1, foreign keys, pay health, ElectrumX connectivity, anonymous-list rejection, legacy Bearer compatibility, and public capability privacy while scoped auth remains disabled.

## 6. Create two temporary acceptance merchants + scoped credentials

Do this only after Step 5 passes.

Create two distinct temporary merchants:

```bash
python3 backend/scripts/merchant_credential_admin.py \
  create-merchant \
  --display-name "Phase K acceptance merchant A"

python3 backend/scripts/merchant_credential_admin.py \
  create-merchant \
  --display-name "Phase K acceptance merchant B"
```

Record both returned merchant IDs locally.

Create two protected secret files outside the repository:

```bash
umask 077

python3 backend/scripts/merchant_credential_admin.py \
  create-credential \
  --merchant-id <merchant-a-id> \
  --label phase-k-acceptance-a \
  --secret-file /home/ubuntu/.pepew-phase-k-acceptance-a.secret

python3 backend/scripts/merchant_credential_admin.py \
  create-credential \
  --merchant-id <merchant-b-id> \
  --label phase-k-acceptance-b \
  --secret-file /home/ubuntu/.pepew-phase-k-acceptance-b.secret
```

Do not print or paste either file's contents.

Using two temporary scoped merchants deliberately keeps the acceptance payment.created events out of the existing legacy merchant webhook namespace. Existing legacy enabled webhook endpoints therefore do not need to be disabled or modified.

## 7. Enable scoped DB auth

```bash
sudo systemctl stop pepew-pay.service
python3 backend/scripts/configure_phase_k_scoped_auth.py --enable
sudo systemctl start pepew-pay.service
sudo systemctl --no-pager --full status pepew-pay.service
```

The config helper requires Phase K schema, a still-configured legacy API key, at least one active DB-backed credential, and a stopped service. It changes only PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED and keeps backend/.env mode 0600.

## 8. Two-merchant production isolation smoke

Run:

```bash
python3 backend/scripts/phase_k_two_merchant_acceptance.py \
  --scoped-secret-file-a /home/ubuntu/.pepew-phase-k-acceptance-a.secret \
  --scoped-secret-file-b /home/ubuntu/.pepew-phase-k-acceptance-b.secret
```

The script authenticates both temporary scoped credentials, requires that they belong to two different non-legacy merchants, and creates two unfunded one-atom payments with the same merchant_reference and the same Idempotency-Key.

Before creating either payment, it checks only the two acceptance merchants for enabled webhook endpoints that would actually receive payment.created. Existing enabled endpoints owned by the legacy merchant or another merchant do not block the smoke because merchant ownership prevents those deliveries.

If either acceptance merchant has a matching enabled webhook endpoint, the smoke fails before creating payments.

Required checks:

```text
acceptance_payment_created_webhook_recipients=0
same_reference_across_merchants=pass
same_idempotency_key_across_merchants=pass
merchant_payment_recovery_isolation=pass
public_capability_privacy=pass
webhook_endpoint_listing_isolation=pass
webhook_delivery_listing_isolation=pass
payment_event_ownership=pass
global_delivery_owner_mismatch=0
PHASE K TWO-MERCHANT ACCEPTANCE: PASS
```

No merchant credential or payment capability ID is printed.

## 9. Acceptance credential cleanup

List and disable both temporary credentials:

```bash
python3 backend/scripts/merchant_credential_admin.py \
  list-credentials \
  --merchant-id <merchant-a-id>

python3 backend/scripts/merchant_credential_admin.py \
  list-credentials \
  --merchant-id <merchant-b-id>

python3 backend/scripts/merchant_credential_admin.py \
  disable-credential \
  --credential-id <credential-a-id>

python3 backend/scripts/merchant_credential_admin.py \
  disable-credential \
  --credential-id <credential-b-id>

rm -f /home/ubuntu/.pepew-phase-k-acceptance-a.secret
rm -f /home/ubuntu/.pepew-phase-k-acceptance-b.secret
```

The two temporary merchant rows and two unfunded test payment records may remain as bounded acceptance evidence. The payments expire normally.

## 10. Establish a post-migration recovery point

After the service is healthy and K5 smoke passes, create another H2 automatic backup and J1 off-host copy.

The new manifest must report:

```text
schema_profile=phase_k_merchant_v1
```

and all merchant ownership counters must be zero.

## 11. Final health checks

Verify:

- pepew-pay.service active;
- localhost ElectrumX tunnel active;
- pay.pepepow.net /api/health OK;
- pay.pepepow.net /api/status reports ElectrumX connected;
- watcher healthy/not stale;
- webhook worker healthy;
- H2 timer unchanged;
- VM-A remains Light-only;
- no new public ElectrumX/RPC listener;
- PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=true only after acceptance;
- temporary acceptance credential disabled and secret file removed.

## 12. Legacy environment credential retirement

Do not remove PAYMENT_CREATE_API_KEY merely because the two-merchant smoke passes.

Retire it only after the current real merchant integrations have received and verified a DB-backed credential for mrc_legacy_v1. That is a separate final K5 cutover action because removing the environment key before merchant consumers are rotated would break existing integrations.

Until that rotation is completed, the legacy key and scoped credentials may coexist. Independent merchants must never share the legacy key.


## Production partial acceptance record — 2026-10-03

First VM-B K5 execution reached the safety stop exactly as intended.

Verified:

- GitHub main `f757838f2fb7bfa66bf8c39d7035bd2dc8c91302`
- 289 backend tests PASS
- Payment Platform active, ElectrumX tunnel active, H2 timer active
- fresh pre-migration H2 + J1 recovery PASS
- recovery point `payment-auto-20261002T172505Z.sqlite3`
- recovery SHA-256 `4c4d4831cf9fc870210dd033607aef184c62c87bf2eb87c21edb7e197fdf0eb4`
- Phase K production migration PASS
- post-migration legacy compatibility acceptance PASS
- Payment Platform/watcher/public health remained healthy
- legacy environment credential preserved

The original two-merchant smoke stopped before creating its acceptance payments because an enabled webhook endpoint existed. The operator did not use the override. Scoped auth was returned to false, the temporary credential was disabled, and its secret file was removed. No post-migration H2/J1 recovery point was created because K5 had not completed.

The follow-up smoke now uses two temporary scoped merchants instead of the legacy merchant. This preserves existing legacy webhook endpoints unchanged while still exercising two independent scoped credential namespaces.


## Resume after a partial scoped smoke

If a scoped smoke already persisted valid non-legacy payment/event rows before a
later acceptance check failed, do **not** rerun the production migration and do
not expect the strict legacy-only post-migration verifier to pass.

With scoped auth still disabled, run:

```bash
python3 backend/scripts/phase_k_post_migration_acceptance.py \
  --allow-existing-scoped-data
```

This resume mode still requires:

- `phase_k_merchant_v1`;
- zero merchant ownership orphan/mismatch checks;
- foreign-key integrity;
- Payment Platform health;
- ElectrumX connectivity;
- anonymous merchant listing rejection;
- legacy Bearer compatibility;
- public capability privacy.

It only relaxes the one-time pre-smoke assertion that every production row must
still belong to `mrc_legacy_v1`.

Previous disabled acceptance credentials may be mapped back to their non-secret
merchant owner metadata without exposing token hashes or secrets:

```bash
python3 backend/scripts/merchant_credential_admin.py \
  show-credential \
  --credential-id <disabled-credential-id>
```

Create fresh one-time credentials for those same two merchant IDs rather than
creating additional acceptance merchant rows.


After enabling scoped auth, resume the already persisted pair instead of
creating more acceptance payments:

```bash
python3 backend/scripts/phase_k_two_merchant_acceptance.py \
  --scoped-secret-file-a /home/ubuntu/.pepew-phase-k-acceptance-a.secret \
  --scoped-secret-file-b /home/ubuntu/.pepew-phase-k-acceptance-b.secret \
  --resume-existing
```

Resume mode discovers the newest common `phase-k-acceptance/` merchant
reference + idempotency key pair owned by those two merchants, verifies that
the two payment IDs are distinct, and then runs the remaining authenticated
recovery/public-capability/webhook-listing/ownership checks. It does not create
another acceptance payment.

### Production heavy-route pacing

The two-merchant acceptance intentionally continues through the public
`pay.pepepow.net` Nginx boundary instead of bypassing rate limits through
localhost.

Production heavy routes are limited by Nginx to `3r/s` with bounded bursts.
The helper now:

- spaces heavy API requests by at least 0.55 seconds (below 2 requests/second);
- treats a non-JSON Nginx HTTP 429 as a retryable rate-limit response;
- honors a numeric `Retry-After` header when present;
- otherwise uses bounded exponential backoff;
- performs at most three rate-limit retries;
- fails normally if 429 persists.

Do not increase Nginx rate/burst limits merely to make the acceptance pass.


## Production partial acceptance record — rate-limit stop

A follow-up VM-B K5 attempt reached the scoped two-merchant smoke and then
stopped safely on a public Nginx HTTP 429.

Verified before/after the stop:

- repository SHA: `932fff6d8712b13c3fb133dfb7879174c8c9babe`
- backend suite: 290 passed
- read-only post-migration acceptance: PASS before scoped smoke
- Phase K schema remained active
- scoped auth was enabled only for the smoke, then returned to `false`
- two temporary acceptance payments, their idempotency rows, and
  `payment.created` events had already been persisted before the later 429
- those records were left intact rather than manually deleted
- existing legacy webhook endpoints were unchanged
- both temporary credentials were disabled
- both temporary secret files were removed
- legacy environment credential remained configured
- Payment Platform, ElectrumX tunnel, watcher, public health/status, and H2 timer
  remained healthy
- final post-migration H2/J1 recovery point was **not** created because K5 had
  not completed

The strict legacy-only verifier now correctly rejects the already persisted
non-legacy acceptance rows. Resume with
`--allow-existing-scoped-data`; this is expected state, not a reason to rerun
or roll back the successful Phase K schema migration.


## Production resumed acceptance record — functional PASS, J1 pending

The next K5 resume run passed the multi-merchant acceptance while reusing the
already-persisted acceptance pair:

- current `main`: `4e00b8ea1aa3ce29d93ed47bf6d6ee58de7d1efa`;
- 298 backend tests passed;
- resume post-migration acceptance passed;
- original acceptance credentials remained disabled and belonged to two distinct
  non-legacy merchants;
- scoped auth was enabled successfully and remains enabled;
- legacy environment credential remains configured;
- `--resume-existing` passed without creating new payments;
- acceptance webhook recipient count was zero;
- no HTTP 429 retry was observed during the resume window;
- newly issued temporary credentials were disabled and secret files removed;
- Payment Platform, ElectrumX tunnel, watcher, webhook worker, public health and
  H2 timer remained healthy.

The final H2 backup passed:

```text
payment-auto-20261003T032555Z.sqlite3
SHA-256 525a7f408c504884d99d0e9680d2cbf39a6723b55a43b7c9037ccacfe5a11570
schema_profile=phase_k_merchant_v1
merchant ownership counters=all zero
```

The subsequent J1 destination verification failed on VM-A with:

```text
backup table counts do not match manifest
```

No retry, receiver modification, SSH trust-boundary change, or destructive
recovery was attempted. Verify/update VM-A's local receiver and restore-helper
checkout before another copy.
