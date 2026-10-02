# Phase K — Production Acceptance

Status: **READY FOR VM-B EXECUTION — repository tooling complete; production not yet changed**

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

## 6. Create second acceptance merchant + scoped credential

Do this only after Step 5 passes.

```bash
python3 backend/scripts/merchant_credential_admin.py \
  create-merchant \
  --display-name "Phase K acceptance merchant"
```

Record the returned merchant_id locally.

Choose a temporary protected secret file outside the repository:

```bash
umask 077
python3 backend/scripts/merchant_credential_admin.py \
  create-credential \
  --merchant-id <returned-merchant-id> \
  --label phase-k-acceptance \
  --secret-file /home/ubuntu/.pepew-phase-k-acceptance.secret
```

Do not print or paste the file contents.

## 7. Enable scoped DB auth

```bash
sudo systemctl stop pepew-pay.service
python3 backend/scripts/configure_phase_k_scoped_auth.py --enable
sudo systemctl start pepew-pay.service
sudo systemctl --no-pager --full status pepew-pay.service
```

The config helper requires Phase K schema, a still-configured legacy API key, at least one active DB-backed credential, and a stopped service. It changes only PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED and keeps backend/.env mode 0600.

## 8. Two-merchant production isolation smoke

First confirm there are no intentionally enabled merchant webhooks. The smoke refuses by default if any enabled endpoint exists, because its payment.created events must not be sent to a real merchant receiver.

Run:

```bash
python3 backend/scripts/phase_k_two_merchant_acceptance.py \
  --scoped-secret-file /home/ubuntu/.pepew-phase-k-acceptance.secret
```

The script creates two unfunded one-atom payments with the same merchant_reference and the same Idempotency-Key, one through the legacy credential and one through the scoped credential.

Required checks:

```text
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

After recording the credential_id from local operator metadata, disable the temporary acceptance credential:

```bash
python3 backend/scripts/merchant_credential_admin.py \
  list-credentials \
  --merchant-id <acceptance-merchant-id>

python3 backend/scripts/merchant_credential_admin.py \
  disable-credential \
  --credential-id <acceptance-credential-id>

rm -f /home/ubuntu/.pepew-phase-k-acceptance.secret
```

The test merchant row and two unfunded test payment records may remain as bounded acceptance evidence. The payments expire normally.

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
