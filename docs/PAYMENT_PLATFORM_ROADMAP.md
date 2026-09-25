# PEPEW Payment Platform Roadmap

Last updated: 2026-09-25

This document is the canonical cross-repository roadmap for the PEPEW Payment Platform. GitHub `main` remains the source of truth for implementation state. Update this file when architecture, phase status, API boundaries, deployment assumptions, or major decisions change.

## 1. Goal

Build a lightweight non-custodial PEPEW payment stack in this order:

```text
pepew-js
  -> PEPEW Payment URI
  -> PepewPay
  -> Payment/Event Gateway
  -> Webhook
```

The existing PEPEW Light API and web wallet remain supported. New payment work must not weaken the wallet's non-custodial boundary or expose ElectrumX publicly.

## 2. Repository boundaries

| Repository | Responsibility |
| --- | --- |
| `edisontw/electrumx-pepepow` | ElectrumX and PEPEPOW chain/indexing support |
| `edisontw/pepepow-electrumx-service` | FastAPI gateway, ElectrumX access, cache, payment backend, event/webhook infrastructure, deployment |
| `edisontw/pepepow-light-wallet` | Client-side non-custodial wallet, mnemonic/derivation, transaction construction/signing, wallet UI |
| `edisontw/pepepow-devkit` | `pepew-js`, Payment URI specification, PepewPay, examples and test vectors |

Mnemonic, private-key derivation, and signing logic must never move into the server repositories.

## 3. Current production baseline

Production endpoints:

```text
https://light.pepepow.net   # PEPEW Light + web wallet + compatibility checkout
https://pay.pepepow.net     # authoritative Payment Platform + PepewPay
```

Production is split across two Oracle Cloud ARM64 Ubuntu 22.04 hosts, each with
approximately 1 CPU core and 5.8 GiB RAM. Python 3.10 remains the production
runtime; Node.js is not required on either host for serving PepewPay.

Current runtime:

```text
Internet
  |
  +-- light.pepepow.net
  |     VM-A
  |     -> Nginx
  |     -> PEPEW Light FastAPI 127.0.0.1:8088
  |     -> ElectrumX 127.0.0.1:50001
  |     -> PEPEPOWd RPC 127.0.0.1:8834
  |     -> Payment API/watcher/webhook feature gates disabled
  |
  +-- pay.pepepow.net
        VM-B
        -> Nginx + PepewPay static
        -> Payment FastAPI 127.0.0.1:8088
        -> authoritative SQLite /var/lib/pepew-pay/payments.sqlite3
        -> payment watcher + webhook worker
        -> localhost SSH tunnel 127.0.0.1:50001
        -> VM-A ElectrumX 127.0.0.1:50001
```

ElectrumX and PEPEPOWd RPC remain non-public. VM-A compatibility-proxies only
the authoritative Payment Platform v1/webhook routes to VM-B so existing
`light.pepepow.net/pay/` capability links continue to work.

The production host is resource-constrained primarily by its single CPU core. Prefer low background CPU, bounded I/O, short caches, rate limits, minimal dependencies, and fault isolation.

Do not assume Node.js or a newer Python runtime exists on production. Frontend artifacts may be built in CI or on a development machine and deployed as static files.

## 4. Existing payment monitor: keep, but do not treat as merchant authority

The existing endpoint:

```text
GET /api/payment/check
```

is a stateless, address-level monitor. It is useful for simple checks and compatibility, but it is not an invoice database or authoritative merchant payment ledger.

Verified current behavior on `main`:

- a cache miss creates one ElectrumX client/session and closes it after the observation
- one cache miss still queries `server.version`, balance, history, and mempool
- repeated checks for the same address use a bounded short-lived in-process observation cache (default 5 seconds)
- concurrent checks for the same address share one in-flight ElectrumX observation
- `confirmations=N` on this legacy monitor remains address-level and is not the authoritative transaction-level confirmation engine
- status is based primarily on current address balance
- expiry is not a persisted payment state

This endpoint should remain compatible while correctness and load are improved.

## 5. Authoritative payment model

The new Payment Platform must use transaction-level state, not current address balance.

Conceptual records:

```text
Payment
- payment_id
- address / scripthash
- requested amount
- required confirmations
- created_at / created_height
- expires_at
- status
- version

PaymentTransaction
- txid
- vout
- value
- block height
- confirmations
- first_seen_at
```

The state machine must support at least:

```text
waiting
seen_in_mempool
partial
paid_unconfirmed
paid_confirmed
overpaid
expired
error
```

A payment remains paid after the merchant later spends or consolidates the received funds. Existing balance before payment creation must not count as a new payment.

Confirmation count for confirmed transactions is derived from chain height:

```text
current_tip_height - tx_height + 1
```

## 6. Target logical architecture

```text
Wallet / PepewPay / Merchant
          |
          v
     Payment API
          |
          v
       SQLite
   payment + event state
      /         \
     v           v
Payment watcher  Webhook worker
     |
     v
private ElectrumX
```

The payment watcher should consolidate blockchain observation instead of turning every browser refresh into an ElectrumX query.

A future persistent ElectrumX subscription client should handle:

- long-lived TCP connection
- request/response correlation
- notifications
- reconnect/backoff
- resubscription
- reconciliation after disconnect

Browser polling of persisted payment state is acceptable for v1. SSE is optional and is not required for the first release.

## 7. Storage and infrastructure

Initial payment/event persistence should use SQLite, preferably WAL mode where appropriate.

Do not add Redis, PostgreSQL, RabbitMQ, Kafka, Celery, or similar infrastructure until real load or operational requirements justify them.

Expected persistent domains:

```text
payments
payment_transactions
events
webhook_endpoints
webhook_deliveries
```

## 8. Webhook principles

Webhook delivery is a later phase after transaction-level payment state is correct.

Required properties:

- HMAC signing
- stable event ID
- idempotent merchant handling
- at-least-once delivery semantics
- bounded timeout
- retry with backoff
- delivery history
- deduplication
- secret redaction
- URL validation and SSRF protection

Webhook configuration or secrets must not be embedded in the Payment URI.

## 9. Payment URI principles

The PEPEW Payment URI is a wallet-facing protocol, not a backend configuration transport.

Initial shape:

```text
pepew:<address>?amount=<amount>
```

Optional human-facing fields such as `label` and `message` may be specified by the v1 document.

Do not place private callback URLs, webhook secrets, API tokens, private ElectrumX endpoints, or signing material in the URI.

The canonical specification and test vectors belong in `pepepow-devkit`.

## 10. Development sequence

### Phase 0 — Planning and baseline

Status: **COMPLETE**

- [x] Production read-only environment audit
- [x] Confirm current repo boundaries
- [x] Create `edisontw/pepepow-devkit`
- [x] Establish Payment Platform roadmap
- [x] Separate legacy address-level monitor from future authoritative payment state

### Phase A — Protocol foundation

Status: **COMPLETE**

Repository: `pepepow-devkit`

- [x] Define PEPEW Payment URI v1
- [x] Add URI parser/serializer test vectors
- [x] Bootstrap `packages/pepew-js`
- [x] Implement exact amount/address validation shared by URI tooling
- [x] Add package tests and CI configuration
- [x] Document compatibility and versioning rules
- [x] Verify GitHub Actions on Node 20 (CI run 35626376990: success)

Exit criteria:

- deterministic parse/serialize behavior
- invalid input tests
- published v1 spec in repo
- no wallet-secret handling

### Phase B — Payment correctness and Light optimization

Status: **COMPLETE**

Repository: `pepepow-electrumx-service`

- [x] Define transaction-level payment state machine ([PAYMENT_STATE.md](PAYMENT_STATE.md))
- [x] Add transaction output matching
- [x] Implement true N-confirmation logic in the authoritative payment-domain evaluator
- [x] Define creation-boundary, expiry, duplicate-output, and reorg behavior
- [x] Add bounded short-TTL and in-flight deduplicated reads for legacy `/api/payment/check`
- [x] Reduce repeated ElectrumX work with a 5-second observation cache while retaining the existing per-miss methods until protocol-equivalence shortcuts are verified
- [x] Preserve existing public API compatibility (new state primitives are additive; legacy route unchanged)
- [x] Add authoritative unit tests for payment-domain primitives
- [x] Verify Python 3.10 backend CI on GitHub Actions (latest Phase B run 35631522097: 115 passed)

Exit criteria:

- balance-before-invoice does not create a false payment
- later spending does not revert a paid invoice
- partial/multiple payments work
- N confirmations are tested from transaction heights

### Phase C — PepewPay

Status: **COMPLETE**

Repository: `pepepow-devkit`

- [x] Build PepewPay static PWA shell
- [x] Render canonical PEPEW Payment URI QR
- [x] Add native `pepew:` wallet handoff
- [x] Add existing PEPEW Light web-wallet fallback using public `to` / `amount` parameters only
- [x] Display authoritative persisted payment state from the Payment/Event Gateway using read-only `?payment_id=...` capability links
- [x] Keep frontend deployable as static output
- [x] Avoid requiring Node.js on the production chain host
- [x] Poll SQLite-backed status every 4 seconds while visible; hidden tabs skip refreshes
- [x] Use exact decimal-string status amounts in browser calculations; do not rely on large JSON integer atoms through JavaScript Number
- [x] Keep merchant create API key entirely server-side; PepewPay performs status GET only
- [x] Keep Payment API requests out of the PWA app-shell fallback cache
- [x] Add PepewPay status/unit tests and static production build to devkit CI (run 35726364293: success; 10 app tests passed)
- [x] Production backend E2E on 2026-09-22: unauthenticated create rejected -> authenticated create -> wallet broadcast -> watcher persisted state -> `paid_confirmed` (1 PEPEW, 1 confirmation)
- [x] Production static deployment verified at `https://light.pepepow.net/pay/`
- [x] Existing confirmed payment capability link verified in production with read-only persisted details, QR, and wallet handoff
- [x] Final live PepewPay E2E on 2026-09-22: new 0.1 PEPEW payment -> waiting -> PEPEW Light web-wallet handoff/broadcast -> paid_unconfirmed -> paid_confirmed (1 confirmation)

Exit criteria:

- end-to-end demo from payment creation to wallet handoff/status display
- no private key or mnemonic leaves the client wallet

### Phase D — Payment/Event Gateway

Status: **COMPLETE**

Repository: `pepepow-electrumx-service`

- [x] Add SQLite payment/transaction/event schema foundation with WAL and persisted chain state
- [x] Add feature-gated `POST /api/v1/payments` and persisted `GET /api/v1/payments/{payment_id}`
- [x] Snapshot creation height from ElectrumX status and fail closed when the chain tip is unavailable
- [x] Keep browser payment-status reads SQLite-only (no ElectrumX polling per refresh)
- [x] Implement feature-gated persistent ElectrumX subscription client and payment watcher
- [x] Verify PEPEPOW fork subscription semantics: server.version first; header notifications; scripthash status notifications; get_history includes mempool ([ELECTRUMX_PAYMENT_WATCHER.md](ELECTRUMX_PAYMENT_WATCHER.md))
- [x] Persist chain-tip state and apply true confirmation math to stored transaction observations
- [x] Add reconnect/backoff, resubscribe, initial/full reconciliation, queue-overflow recovery, and bounded subscription set
- [x] Snapshot creation-time history txids so pre-existing mempool transactions cannot become false new payments
- [x] Reconcile dropped mempool transactions and confirmed-to-unconfirmed reorg state
- [x] Create durable deterministic payment state-change events atomically with payment version updates ([PAYMENT_EVENTS.md](PAYMENT_EVENTS.md))
- [x] Add persistence/restart/reorg/watcher/API/event-dedup failure-boundary tests (Backend CI run 35714325135: 148 passed)
- [x] Add deployment-safe SQLite state directory and Nginx request/rate limits
- [x] Select fail-closed merchant create policy: server-to-server Bearer API key; read-only status uses high-entropy payment ID capability ([PAYMENT_API_AUTH.md](PAYMENT_API_AUTH.md))

Exit criteria:

- browser status reads do not cause equivalent ElectrumX polling
- restart/reconnect preserves correct payment state
- duplicate notifications do not create duplicate logical events

### Phase E — Webhook

Status: **COMPLETE — production E2E verified**

Repository: `pepepow-electrumx-service`

- [x] Define stable Payment Event Envelope v1 and deterministic event IDs in Phase D
- [x] Implement per-endpoint HMAC-SHA256 signatures derived from a server-side master key ([WEBHOOKS.md](WEBHOOKS.md))
- [x] Add durable SQLite endpoint/delivery queue created atomically with new events
- [x] Add bounded DNS/connect/read timeouts and exponential retry/backoff
- [x] Add at-least-once/idempotency rules with stable event and delivery IDs
- [x] Add authenticated endpoint-management and delivery-log APIs
- [x] Add HTTPS-only SSRF/private/link-local/metadata/DNS-rebinding protections with direct validated-IP connections
- [x] Add webhook signing, queue, retry, API, direct-IP sender, URL-hardening, and SSRF tests
- [x] Keep webhook worker feature-gated off by default until production E2E
- [x] Production webhook E2E on 2026-09-22: SSRF loopback rejection -> endpoint create/list secret boundary -> `payment.created` -> intentional HTTP 503 -> persisted retry -> exact-body HMAC verification -> HTTP 204 retry delivery -> stable event/delivery IDs and body -> authenticated delivery log

Exit criteria:

- safe retry after receiver failure
- stable event IDs
- no secret leakage in logs
- blocked unsafe destinations

### Phase F — Production rollout / deployment split

Detailed migration plan: [PHASE_F_DEPLOYMENT_SPLIT.md](PHASE_F_DEPLOYMENT_SPLIT.md)

Status: **COMPLETE — dedicated Payment Platform host cutover and production E2E verified 2026-09-24**

Final production architecture:

```text
VM-A
- PEPEPOWd
- ElectrumX
- PEPEW Light
- Nginx / existing wallet static files

VM-B
- Payment API
- payment watcher
- SQLite
- webhook worker
- PepewPay static hosting
```

The reason to split is primarily CPU/failure/security isolation, not current RAM pressure.

Phase F rollout record (2026-09-22 through 2026-09-24):

- PEPEW Light baseline deploy passed 184 backend tests on production Python 3.10
- Payment API authorization boundary verified (unauthenticated POST -> 401)
- persisted payment creation verified (authenticated POST -> 201)
- public read-only payment status verified through Nginx
- existing client-side wallet broadcast verified
- persistent watcher advanced a real 1 PEPEW payment to `paid_confirmed`
- Initial single-host validation enabled Payment API and watcher on VM-A; the completed split disables those authoritative gates on VM-A and enables them on VM-B
- Initial single-host validation enabled the webhook worker on VM-A; the completed split moves webhook authority to VM-B
- production webhook E2E verified SSRF rejection, exact-body HMAC, real HTTP 503 retry -> 204 delivery, stable event/delivery IDs and body, authenticated delivery log, cleanup, and no printed secrets
- PepewPay static UI is deployed and verified at `https://light.pepepow.net/pay/`
- Live PepewPay production E2E is verified with a new 0.1 PEPEW payment through web-wallet handoff, broadcast, `paid_unconfirmed`, and `paid_confirmed`
- Production retrieval of the private `pepepow-devkit` static artifact uses a dedicated repository-scoped read-only SSH deploy key; do not store a long-lived GitHub PAT in shell history or the repository
- Pre-cutover VM-B bootstrap validation passed with Payment API/watcher/webhook disabled; health/status worked through Nginx and the SSH ElectrumX tunnel before authority was enabled
- `pay.pepepow.net` DNS and Let's Encrypt HTTPS were verified during bootstrap, then switched to the production Payment API/PepewPay virtual host after authority migration
- Phase F cutover preflight tooling is available to verify SQLite integrity/counts and ensure no enabled webhook endpoint would be silently invalidated by key rotation
- VM-A Phase F cutover preflight passed on 2026-09-23: SQLite integrity ok; 3 payments, 3 payment transactions, 8 events, 1 disabled webhook endpoint, 1 delivered webhook delivery, and zero enabled webhook endpoints
- Phase F snapshot/verification helpers are now available; the snapshot helper refuses to run while the authoritative VM-A `pepew-light.service` remains active and emits a SHA-256 for destination verification
- VM-A snapshot rehearsal passed on 2026-09-23: consistent row counts, SHA-256 generated, and `pepew-light.service` restored active immediately afterward; VM-A remains the sole authoritative writer
- Rehearsal snapshot transfer to VM-B passed SHA-256, SQLite integrity, row-count, and zero-enabled-webhook verification; final authority cutover runbook is now tracked in `docs/PHASE_F_CUTOVER_RUNBOOK.md`
- VM-B PepewPay static staging passed on 2026-09-23 using the verified GitHub Actions artifact for devkit source commit `3d38810fedce470be5ad3a412815b40c609eb258` before the final authority cutover
- Final VM-A freeze/snapshot passed on 2026-09-23; authoritative snapshot `phase-f-final-20260923T155346Z.sqlite3` has SHA-256 `25a73150da504e9eba07f7fe71f28aeefb1bc581f50d159ea25370cff2326942`
- VM-B public cutover passed on 2026-09-23: `pay.pepepow.net` health/status and PepewPay static root are live, with ElectrumX connected through the localhost SSH tunnel
- VM-A has returned to Light-only mode with Payment API/watcher/webhook gates disabled; Light health/status and `/pay/` remain live
- Post-cutover API-boundary acceptance tooling is available at `backend/scripts/phase_f_post_cutover_acceptance.py`; the first run exposed an Nginx nested-route precedence issue that was fixed and regression-tested
- VM-B production webhook E2E passed on 2026-09-24: SSRF rejection, endpoint secret boundary, `payment.created`, persisted HTTP 503 retry, exact-body HMAC verification, retry to HTTP 204 with stable event/delivery IDs and body, authenticated delivery log, cleanup, and no printed secrets
- Corrected 8/8 public post-cutover acceptance passed on 2026-09-24, covering pay health/ElectrumX/static UI, authoritative persisted-payment routing, Light compatibility proxy, unauthenticated create rejection, legacy Light payment/check preservation, and exclusion of legacy Light APIs from the pay domain
- Final real-payment acceptance passed on 2026-09-24: a new 0.01 PEPEW invoice created on VM-B progressed through the watcher to `paid_confirmed` with exact requested/received/confirmed/policy-confirmed amount equality and one required confirmation
- Phase F is complete: VM-B is the sole authoritative Payment Platform writer; VM-A remains Light-only; ElectrumX stays private behind the controlled localhost SSH tunnel
- Backup/snapshot/transfer recovery mechanics were rehearsed and verified. A destructive post-write rollback was intentionally not exercised; if VM-B has accepted newer rows, rollback requires reconciliation before VM-A can become authoritative again

ElectrumX must remain private. A second VM should connect only through an approved private OCI network path or a controlled tunnel; do not expose port 50001 to the Internet.

### Phase G — Merchant integration hardening

Status: **COMPLETE — VM-B production acceptance verified 2026-09-24**

Repository: `pepepow-electrumx-service`

Priority after the completed production cutover is merchant-facing API correctness and recoverability, not additional migration work.

- [x] Add durable `Idempotency-Key` handling to `POST /api/v1/payments`
- [x] Persist idempotency key -> normalized request hash -> payment mapping in SQLite
- [x] Return the original payment for same-key/same-request retries without creating a second `payment.created` event
- [x] Reject same-key/different-request reuse with HTTP 409
- [x] Avoid a second ElectrumX creation snapshot for an already-known idempotent retry
- [x] Add restart/storage/service/API regression tests for idempotency
- [x] Add authenticated merchant-side payment recovery/listing without weakening public capability-link privacy
- [x] Add bounded status filtering and stable SQLite pagination for merchant payment recovery without ElectrumX work
- [x] Include merchant-supplied idempotency keys in authenticated recovery results while keeping public capability status unchanged
- [x] Define and implement optional `merchant_reference` as a unique merchant-owned business/order identifier
- [x] Keep `merchant_reference` distinct from `Idempotency-Key`: duplicate references conflict while same-key/same-request retries replay the original payment
- [x] Add exact authenticated merchant-reference recovery/filtering without exposing the reference in public capability status
- [x] Add `merchant_reference` to new Payment Event Envelope v1 bodies as additive optional merchant metadata without rewriting historical events
- [x] Add SQLite schema migration, uniqueness, retry-ordering, API, recovery, event, and privacy regression tests
- [x] Add a production-safe VM-B deployment/acceptance helper and runbook with pre-migration snapshot and webhook-delivery guard ([PHASE_G_PRODUCTION_ACCEPTANCE.md](PHASE_G_PRODUCTION_ACCEPTANCE.md))
- [x] Deploy current main to VM-B and pass Phase G create/retry/recovery/reference production acceptance

Production closeout record (2026-09-24):

- VM-B pulled GitHub main at `0e9a04c` and passed the full production Python 3.10 backend suite: 219 tests
- pre-migration authoritative SQLite snapshot passed `PRAGMA integrity_check` and SHA-256 verification before restarting the Payment Platform
- the pre-Phase-G database correctly did not yet contain `payment_idempotency_keys`; snapshot tooling was subsequently made schema-compatible across this migration boundary
- `pepew-pay.service` restarted cleanly and the localhost ElectrumX SSH tunnel remained active
- Phase G production acceptance passed 10/10 checks through `pay.pepepow.net`
- verified authenticated merchant listing, live `merchant_reference` migration/index, create idempotency replay, idempotency conflict, duplicate-reference conflict, exact merchant-reference recovery, public metadata privacy, and exactly one persisted `payment.created` event
- the acceptance test used one unfunded one-atom payment with normal expiry and did not print the merchant API key, payment capability ID/URL, or address
- VM-A remained Light-only; no authoritative writer was re-enabled there

Exit criteria:

- retrying payment creation after client/proxy timeout cannot create duplicate invoices or duplicate `payment.created` events
- merchant operational recovery does not require exposing or enumerating public payment capability IDs
- VM-A remains Light-only and no new authoritative writer is introduced
- changes remain SQLite-based and bounded for the current single-core production footprint


### Phase H — Production Reliability & Merchant Readiness

Status: **IN PROGRESS**

Repository: `pepepow-electrumx-service` for H1/H2/H3 backend work, with
`pepepow-devkit` planned for H4 merchant helpers/examples.

#### H1 — Payment Watcher Operational Health

Status: **COMPLETE — production deployed and verified 2026-09-25**

- [x] Keep watcher metrics in memory; add no database table or external metrics infrastructure
- [x] Expose watcher `enabled`, `running`, `connected`, derived health state, and stale/degraded flags through `GET /api/status`
- [x] Expose last successful connection, reconciliation, header, failure/disconnect, and activity timestamps
- [x] Expose watcher-observed chain-tip height plus bounded subscription count
- [x] Expose connection attempts, reconnect count, total failures, and consecutive failures
- [x] Add configurable `PAYMENT_WATCHER_STALE_SECONDS` with a lightweight 60-second default
- [x] Keep watcher health dynamic even when the existing ElectrumX status payload is served from cache
- [x] Keep the health surface privacy-safe: no addresses, scripthashes, payment IDs, txids, credentials, paths, or secrets
- [x] Bound repetitive reconnect warnings while retaining first failures, periodic summaries, and an explicit recovery log
- [x] Add deterministic tests for healthy, disconnected, stale, reconnect, and recovery states
- [x] Preserve Payment API, webhook, Light API, SQLite payment authority, and transaction/replay semantics
- [x] Keep VM-A writer gates unchanged; H1 is observability only and does not enable Payment Platform writers

H1 production closeout (2026-09-25):

- VM-B deployed GitHub main at `906ed65` and passed 228 backend tests on production Python 3.10
- `pepew-electrumx-tunnel.service` and `pepew-pay.service` remained active
- local and public health endpoints returned HTTP 200
- watcher reported `enabled=true`, `running=true`, `connected=true`, `state=healthy`, `degraded=false`, and `stale=false`
- watcher stale threshold was 60 seconds, chain tip was 5,027,400, one subscription was active, and the acceptance sample had one-second activity age
- connection attempts were 1 with zero reconnects, failures, consecutive failures, or active error
- watcher health contained no prohibited addresses, scripthashes, payment IDs, txids, merchant metadata, credentials, secrets, or internal paths
- VM-A deployed the same commit and passed 228 backend tests on Python 3.10.12
- VM-A ElectrumX remained connected while Payment API, watcher, and webhook writer gates all remained disabled
- VM-A reported watcher state `disabled`; no authoritative writer was re-enabled

#### H2 — SQLite Backup / Restore Operational Hardening

Status: **IN PROGRESS — first tooling increment implemented; production acceptance pending**

Runbook: [PHASE_H2_SQLITE_BACKUP_RESTORE.md](PHASE_H2_SQLITE_BACKUP_RESTORE.md)

- [x] Reuse SQLite only; add no backup daemon, database, queue, or external infrastructure
- [x] Add an online VM-B backup helper using SQLite's backup API so normal backups do not require stopping the Payment Platform
- [x] Verify backup integrity, required schema, bounded table counts, SHA-256, and restrictive file permissions
- [x] Add a privacy-safe sidecar manifest containing checksum/size/count metadata only
- [x] Refuse accidental overwrite and clean partial temporary files on failure
- [x] Add a non-destructive restore drill that restores into a temporary SQLite database and re-verifies it
- [x] Keep the live `PAYMENT_DB_PATH` outside restore-drill scope
- [x] Document the rollback boundary: an older snapshot must not silently discard newer authoritative writes
- [x] Add deterministic tests for WAL/online backup, pre-Phase-G schema compatibility, overwrite refusal, tamper detection, and manifest mismatch
- [ ] Deploy this H2 increment to VM-B and pass online backup + restore-drill production acceptance
- [ ] Decide backup frequency, retention, disk-space guardrails, and whether a second-host/object-storage copy is required before enabling automation

Planned later H increments:

- H3 — reference merchant integration flow
- H4 — merchant examples / SDK helpers in `pepepow-devkit`

## 11. Testing policy

Authoritative payment logic must be unit-testable without requiring the production ElectrumX instance.

Minimum coverage should include:

- Payment URI parsing/serialization
- amount precision
- address validation
- transaction output matching
- partial payments
- multiple transactions per payment
- overpayment
- mempool to confirmed transition
- N confirmations
- expiry
- restart/reconciliation behavior
- duplicate notification/event handling
- webhook signature and retry behavior
- SSRF protections
- legacy API compatibility

## 12. Development and deployment workflow

Normal flow:

```text
GitHub main
  -> implement code/tests/docs
  -> user pulls on production
  -> run tests
  -> deploy
  -> verify runtime
```

ChatGPT may modify GitHub directly.

The host Codex Agent is primarily for production inspection, runtime diagnosis, systemd/Nginx changes, deployment, and host-specific verification.

Avoid simultaneous source-code edits by multiple agents outside GitHub.

## 13. Progress update rule

When a phase or material decision changes:

1. update implementation and tests
2. update relevant README/spec/security/architecture docs
3. update the phase status/checklist in this file
4. record deployment-impacting changes before production rollout

Do not mark a phase complete because code exists; its exit criteria must also be satisfied.
