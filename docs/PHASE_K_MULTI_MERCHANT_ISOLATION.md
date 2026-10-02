# Phase K — Multi-merchant Credential Isolation

Status: **IN PROGRESS — K0-K4 complete; K5 repository tooling ready, production execution pending**

Last updated: 2026-10-02

## 1. Why Phase K

Phases 0/A-J established a production-validated transaction-level Payment
Platform, public SDK distribution, merchant examples/adapters, and operational
resilience.

The remaining structural limit is the current **single-merchant credential
boundary**:

- one `PAYMENT_CREATE_API_KEY` authorizes every authenticated merchant action;
- payment idempotency keys are globally scoped;
- `merchant_reference` is globally unique rather than merchant-scoped;
- webhook endpoints and delivery logs have no merchant owner;
- authenticated merchant listing is therefore one shared namespace.

That model was appropriate for the initial rollout, but it should not be
extended to multiple independent real merchants by sharing one production
Bearer key.

Phase K introduces explicit merchant ownership and scoped credentials while
keeping the existing SQLite-first, low-resource architecture.

## 2. Non-goals

Phase K does **not** add:

- a public merchant dashboard;
- user/password accounts or social login;
- Redis, PostgreSQL, Kafka, RabbitMQ, Celery, or another service;
- custody, wallet private keys, mnemonic handling, or server-side signing;
- a new public sandbox host;
- per-merchant blockchain watchers;
- a new payment state machine.

Public payment capability URLs and transaction-level payment authority remain
unchanged.

## 3. Target model

Conceptual additions:

```text
Merchant
- merchant_id
- display_name / operator label
- enabled
- created_at / updated_at

MerchantCredential
- credential_id / key prefix
- merchant_id
- credential_hash
- enabled
- created_at / disabled_at
```

Ownership is added to authoritative domains:

```text
payments                -> merchant_id
payment_idempotency_keys -> merchant_id
events                  -> merchant_id (internal ownership)
webhook_endpoints       -> merchant_id
webhook_deliveries      -> derived/verified same-merchant relationship
```

The public `payment_id` remains globally high entropy and globally unique.

## 4. Credential principles

Bearer authentication remains the HTTP contract so existing merchant SDK
transport does not need a new authentication protocol.

New credentials must:

- be high entropy;
- be shown only once when created;
- never be stored in plaintext in SQLite;
- support more than one active credential per merchant during rotation;
- support explicit disable/revoke;
- resolve to a `merchant_id` auth context;
- use constant-time secret verification;
- never appear in logs or API responses after creation.

The initial implementation should use a key identifier/prefix for bounded lookup
plus a cryptographic hash of the full high-entropy credential. A database leak
must not reveal usable Bearer secrets.

Public self-service credential creation is out of scope. Initial credential
lifecycle is operator tooling only.

## 5. Backward-compatible production migration

Existing VM-B production state belongs to the current legacy merchant.

Migration must create one reserved legacy merchant and assign all existing:

- payments;
- idempotency mappings;
- events;
- webhook endpoints;
- webhook deliveries through their existing endpoint/event relationships

to that merchant.

The current `PAYMENT_CREATE_API_KEY` remains accepted during a bounded
compatibility period and resolves to the reserved legacy merchant. Do not force
an immediate credential cutover during the schema migration.

After database-backed credentials are production-verified, the existing
merchant can receive a new scoped credential and the environment key can be
retired deliberately.

## 6. Merchant-scoped invariants

### Payment creation and recovery

For an authenticated merchant `M`:

- new payments are persisted with `merchant_id = M`;
- payment listing/recovery returns only `M` payments;
- exact `merchant_reference` lookup is restricted to `M`;
- idempotency replay/conflict lookup is restricted to `M`.

Required uniqueness becomes:

```text
(merchant_id, merchant_reference)
(merchant_id, idempotency_key)
```

The same reference or idempotency key may therefore exist independently for two
different merchants.

### Public capability status

```http
GET /api/v1/payments/{payment_id}
```

remains a read-only high-entropy capability and does not expose
`merchant_id`, credential IDs, idempotency keys, or private merchant metadata.

### Webhooks

Webhook endpoint create/list/disable and delivery-log reads are merchant scoped.

When an event is created for merchant `M`, delivery rows may be created only
for enabled endpoints owned by `M`.

No cross-merchant event delivery is acceptable.

Existing endpoint signing secrets should remain stable through the migration
when possible. Do not rotate the webhook master key or silently change an
existing endpoint secret merely to add merchant ownership.

## 7. SQLite migration constraints

Keep SQLite and one authoritative writer.

The migration must be restart-safe and covered from a real pre-K schema fixture.

Important schema work includes:

- add `merchants` and `merchant_credentials`;
- add/backfill `merchant_id` ownership columns;
- replace the global merchant-reference uniqueness rule with a composite
  merchant-scoped unique index;
- rebuild the idempotency table if required to replace its global primary key;
- add bounded indexes for merchant listing/recovery and endpoint management;
- keep foreign keys enabled;
- preserve every existing payment/event/delivery record.

Do not rewrite historical Payment Event Envelope v1 bodies solely to inject a
merchant identifier. Merchant ownership is an internal authorization boundary.

## 8. Development sequence

### K0 — Baseline and contract freeze

Status: **COMPLETE 2026-10-02**

- [x] confirm current API uses one global `PAYMENT_CREATE_API_KEY`
- [x] confirm no current `merchant_id` ownership exists
- [x] confirm idempotency and merchant-reference namespaces are global
- [x] confirm webhook endpoints are currently unowned
- [x] define backward-compatible legacy-merchant migration
- [x] keep Bearer transport and public capability status contract stable
- [x] defer dashboard/accounts/sandbox infrastructure

### K1 — Merchant identity + credential storage

Status: **COMPLETE — 268 backend tests PASS on Python 3.10**

- [x] add merchant and credential schema
- [x] add one-time credential generation/operator helper; generated secret is written only to a newly created mode-0600 file
- [x] store only credential hash + bounded identifier metadata
- [x] return merchant auth context rather than a boolean-only auth check
- [x] keep legacy environment-key compatibility mapped to the reserved merchant
- [x] add credential enable/disable/rotation tests
- [x] keep scoped database credential acceptance behind `PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED=false` until K2/K3 ownership isolation is complete

K1 implementation notes:

- reserved legacy merchant: `mrc_legacy_v1`
- new credentials use a bounded identifier plus high-entropy secret
- SQLite stores only SHA-256 of the complete high-entropy token, never the plaintext Bearer token
- multiple active credentials per merchant are supported for safe rotation
- disabled credentials no longer authenticate
- operator helper does not print newly generated credentials; it writes the one-time secret to an explicit non-existing mode-0600 file
- existing production Bearer key behavior remains compatible and resolves to the legacy merchant context
- production must keep scoped DB auth disabled until payment and webhook ownership scoping are complete

### K2 — Payment ownership isolation

Status: **COMPLETE — 270 backend tests PASS on Python 3.10**

- [x] add/backfill `payments.merchant_id`
- [x] scope idempotency mappings by merchant
- [x] scope `merchant_reference` uniqueness and recovery by merchant
- [x] scope authenticated payment listing by merchant
- [x] preserve public capability status response shape/privacy
- [x] test cross-merchant same-reference and same-idempotency-key independence
- [x] test cross-merchant listing/recovery denial

K2 implementation notes:

- existing pre-K payment rows are assigned to the reserved legacy merchant
- the idempotency table is rebuilt transactionally from the legacy global-key schema to a composite `(merchant_id, idempotency_key)` primary key
- merchant-reference uniqueness is now `(merchant_id, merchant_reference)`
- authenticated create/list routes propagate the resolved merchant auth context
- the same reference and idempotency key may coexist for two independent merchants
- public `GET /api/v1/payments/{payment_id}` remains a capability response and does not expose `merchant_id`
- scoped DB credential acceptance remains disabled by default until K3 webhook ownership isolation is complete

### K3 — Webhook ownership isolation

Status: **COMPLETE — 272 backend tests PASS on Python 3.10**

- [x] assign/backfill webhook endpoints to merchants
- [x] ensure event creation queues deliveries only to same-merchant endpoints
- [x] scope endpoint list/disable to authenticated merchant
- [x] scope delivery-log reads to authenticated merchant
- [x] preserve existing endpoint signing secrets through migration
- [x] add cross-merchant webhook isolation tests

K3 implementation notes:

- `events.merchant_id` is internal ownership metadata; historical/public event payload JSON is not rewritten
- existing event ownership is derived from its payment owner during migration
- existing webhook endpoints are assigned to the reserved legacy merchant
- new delivery rows are created only when the endpoint owner matches the payment/event merchant
- worker processing remains one bounded global queue; it does not need one watcher or worker per merchant
- endpoint list/disable and delivery-log APIs are scoped by resolved merchant auth context
- cross-merchant endpoint IDs do not reveal or disable another merchant's endpoint
- webhook signing-secret derivation remains `master_key + endpoint_id`; migration does not change endpoint IDs or existing signing secrets
- scoped DB credential acceptance remains disabled by default until K4/K5 production migration acceptance

### K4 — Migration / recovery / operational hardening

Status: **COMPLETE — 279 backend tests PASS on Python 3.10**

- [x] add full pre-K SQLite migration fixture with row-count, relationship, index, historical-event-byte, and restart-safety assertions
- [x] update H2/J1 verification with automatic legacy_payment_v1 / phase_k_merchant_v1 schema profiles and merchant ownership consistency checks
- [x] keep historical pre-K manifests/restores compatible when no Phase K markers exist
- [x] add safe operator credential create/list-metadata/disable commands
- [x] fail closed by disabling a newly inserted credential if one-time secret-file delivery fails
- [x] ensure secrets never enter logs, manifests, backup metadata, J1 transfer metadata, or normal command output
- [x] document overlap rotation and emergency revoke ([PHASE_K_CREDENTIAL_OPERATIONS.md](PHASE_K_CREDENTIAL_OPERATIONS.md))
- [x] keep low CPU/background activity and no new daemon

K4 closure: full pre-K migration, H2/J1 Phase K schema-profile verification, fail-closed credential delivery, and credential operations all pass in CI (279 tests). K4 deliberately does not enable PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED in production. That gate remains part of K5 acceptance after a fresh local + off-host recovery point and production ownership migration.

### K5 — Production acceptance

K5 repository tooling: **READY — 289 backend tests PASS on Python 3.10**

Prepared tooling/runbook:

- `backend/scripts/phase_k_production_migration.py`
- `backend/scripts/phase_k_post_migration_acceptance.py`
- `backend/scripts/configure_phase_k_scoped_auth.py`
- `backend/scripts/phase_k_two_merchant_acceptance.py`
- [PHASE_K_PRODUCTION_ACCEPTANCE.md](PHASE_K_PRODUCTION_ACCEPTANCE.md)

These helpers do not deploy themselves. VM-B production execution remains pending.

Latest K5 tooling CI: **289 passed, 1 warning**; production helper compilation PASS.

- [ ] create pre-migration verified H2 + off-host recovery point
- [ ] migrate VM-B while preserving single-writer authority
- [ ] verify legacy merchant compatibility first
- [ ] create a second test merchant with a distinct scoped credential
- [ ] verify same merchant reference/idempotency key can coexist across merchants
- [ ] verify each merchant cannot list/recover/manage the other's private data
- [ ] verify webhook events cannot cross merchant boundaries
- [ ] verify public capability status remains compatible
- [ ] verify watcher/webhook/backup health unchanged
- [ ] retire the legacy environment credential only after scoped credential acceptance

### K6 — DevKit / onboarding alignment

- [ ] document operator-issued scoped merchant credentials
- [ ] update Quick Start and testing strategy for multi-merchant production
- [ ] keep `@pepepow/pepewpay-merchant` Authorization transport compatible
- [ ] add integration contract fixtures for two merchant namespaces
- [ ] explicitly keep merchant dashboard/self-service provisioning deferred

## 9. Exit criteria

Phase K is complete when:

- two independent merchants can use the same Payment Platform without sharing a
  credential or private namespace;
- payment recovery, idempotency, merchant references, webhook endpoints, and
  delivery logs are merchant isolated;
- no cross-merchant webhook delivery can be created;
- current production data is migrated losslessly to a legacy merchant;
- existing public payment capability URLs remain compatible;
- credential rotation/revocation works without storing plaintext credentials;
- H2/J1 backup and restore verification covers the new authoritative schema;
- no new continuously running infrastructure is required.

## 10. Deferred work

A merchant dashboard, self-service signup, billing, quotas, analytics, and a
dedicated public sandbox should be considered only after the scoped credential
and ownership model is production-validated.
