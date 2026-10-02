# Phase K — Merchant Credential Operations

Status: **K4 operator runbook complete; production scoped auth remains disabled until K5**

Last updated: 2026-10-03

This runbook covers operator-issued merchant identities and credentials for the Phase K multi-merchant model.

Merchant applications keep the existing HTTP contract:

```http
Authorization: Bearer <merchant credential>
```

## 1. Production gate

Repository default:

```text
PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false
```

Keep it false through K4. The existing PAYMENT_CREATE_API_KEY remains the compatibility credential and maps to reserved merchant mrc_legacy_v1.

K5 must first create a verified local + off-host recovery point, migrate production ownership, and complete two-merchant isolation acceptance before scoped DB credentials are enabled.

## 2. Operator helper

Tool: backend/scripts/merchant_credential_admin.py

The helper reads PAYMENT_DB_PATH from the protected backend environment file. It may print merchant IDs, credential IDs, state, and PASS/FAIL status. It never prints the generated Bearer secret.

### Show legacy merchant

```bash
python3 backend/scripts/merchant_credential_admin.py show-legacy-merchant
```

### Create merchant

```bash
python3 backend/scripts/merchant_credential_admin.py create-merchant --display-name "Example merchant"
```

Record the returned merchant_id. It is not a secret.

### Create credential

Choose a protected path that does not already exist:

```bash
umask 077
python3 backend/scripts/merchant_credential_admin.py create-credential \
  --merchant-id mrc_<id> \
  --label primary \
  --secret-file /secure/operator/path/merchant-api-key.txt
```

Properties:

- the secret file is created mode 0600;
- an existing file is never overwritten;
- SQLite stores only credential identifier, merchant owner, metadata, and SHA-256 of the complete high-entropy token;
- if one-time secret-file delivery fails, the new credential is disabled immediately;
- normal output contains no Bearer secret.

Transfer the secret only through the approved secret-management path. Do not paste it into chat, tickets, GitHub, shell history, QR data, browser JavaScript, or logs.

### List credential metadata

```bash
python3 backend/scripts/merchant_credential_admin.py list-credentials --merchant-id mrc_<id>
```

Only IDs and enabled/disabled state are listed.

### Disable / emergency revoke

```bash
python3 backend/scripts/merchant_credential_admin.py disable-credential --credential-id mck_<id>
```

Disabling is immediate for database-backed authentication checks. The secret value is not required for revocation.

## 3. Normal rotation

Use overlap rather than replace-in-place:

```text
create new credential
  -> install new secret on merchant backend
  -> verify requests with new credential
  -> disable old credential ID
  -> verify old credential is rejected
```

Multiple active credentials per merchant are intentionally supported during this bounded rotation window.

## 4. Emergency revoke

For a suspected leak: disable the affected credential ID, verify it is rejected, create a replacement only if service must resume, and keep incident notes limited to IDs/metadata rather than the secret value.

## 5. Backup and recovery boundary

Credential plaintext is never stored in SQLite and therefore never appears in H2/J1 backup manifests or transfer metadata. Phase K backup verification records only schema profile, bounded table counts, zero-valued ownership consistency checks, file size, and SHA-256.

Backup files remain sensitive recovery material and keep the same mode-0600 / non-public storage requirements.

## 6. K5 cutover order

```text
fresh H2 backup + off-host J1 copy
  -> migrate production SQLite
  -> verify legacy credential compatibility
  -> create second test merchant + scoped credential
  -> enable PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=true
  -> verify two-merchant payment/webhook isolation
  -> only later consider retiring PAYMENT_CREATE_API_KEY
```

If any migration, ownership, webhook, backup, or health check fails, keep scoped auth disabled and preserve the legacy credential path.
