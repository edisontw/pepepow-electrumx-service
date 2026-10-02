# PEPEW Payment Platform Roadmap

Last updated: 2026-10-02

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

Status: **COMPLETE — H1-H4 baseline delivered and verified 2026-09-25**

Repositories:

- `pepepow-electrumx-service` for H1/H2/H3 reliability/backend reference work
- `pepepow-devkit` for H4 merchant SDK helpers/examples

Off-host backup remains a follow-up reliability enhancement, not a blocker for
the completed Phase H baseline.

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

Status: **COMPLETE — local backup/restore automation production verified 2026-09-25**

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
- [x] Deploy the first H2 increment to VM-B and pass online backup + restore-drill production acceptance
- [x] Select a lightweight local policy: daily backup, 14 complete automatic pairs, 512 MiB pre-backup free-space guardrail, restore drill after every backup
- [x] Add bounded retention that deletes only complete H2 automatic backup/manifest pairs and leaves manual/Phase F/Phase G/orphan files untouched
- [x] Add hardened systemd oneshot/timer units with low process/I/O priority and no Payment Platform downtime
- [x] Add deterministic retention/low-disk/failure-boundary tests
- [x] Deploy the automation increment to VM-B, run the corrected oneshot manually, then enable/verify the timer
- [x] Diagnose the first VM-B automation acceptance failure: `ProtectSystem=strict` blocked SQLite WAL shared-memory coordination; timer remained disabled and production stayed healthy
- [x] Correct the backup unit so the private mount namespace permits `-shm` coordination while pinning the authoritative DB/WAL/journal files read-only
- [x] Add a regression test for the WAL-compatible systemd sandbox policy
- [x] Re-run VM-B manual oneshot acceptance with the corrected unit before enabling the timer
- [ ] Follow-up reliability item: evaluate a simple off-host second copy without introducing a continuously running backup service

H2 first-increment production closeout (2026-09-25):

- VM-B deployed `e5565f3` and passed 233 backend tests on Python 3.10.12
- online SQLite backup passed while `pepew-pay.service` and the ElectrumX tunnel remained active
- backup size was 446,464 bytes; integrity, SHA-256, mode `0600`, and privacy-safe manifest checks passed
- non-destructive restore drill passed without replacing the live authoritative database
- local/public health remained healthy and the payment watcher remained healthy, connected, non-degraded, and non-stale
- no recent SQLite/service errors were observed; production configuration was unchanged
- VM-A was not touched and no automatic timer/retention was installed during this acceptance

H2 automation acceptance attempt 1 (2026-09-25):

- VM-B was on `0130aab`, Python 3.10.12, with 237 backend tests passing
- `systemd-analyze verify` passed aside from unrelated host warnings
- backup filesystem was `0750 ubuntu:ubuntu` with 28 GiB free
- the manual backup oneshot failed safely with `unable to open database file`; no automatic backup was created
- root cause was the hardened unit blocking SQLite WAL `-shm` coordination under `ProtectSystem=strict`
- existing manual/Phase F/Phase G backups were preserved and no unrelated files were deleted
- `pepew-pay.service`, the ElectrumX tunnel, local/public health, and watcher all remained healthy
- timer stayed disabled; no production configuration changed and VM-A was not touched

H2 automation retry closeout (2026-09-25):

- VM-B deployed corrected main `e208c8e`
- corrected WAL-compatible sandbox loaded successfully
- manual backup oneshot completed successfully and created an automatic backup/manifest pair
- backup and manifest were both mode `0600`
- automatic restore drill and an independent second restore drill both passed
- `pepew-pay.service` and the localhost ElectrumX tunnel remained active
- local/public health remained healthy; watcher remained healthy, connected, running, non-degraded, and non-stale
- timer was enabled and active only after the corrected manual oneshot passed
- next scheduled activation observed at `2026-09-26 03:28:07 UTC`
- no Persistent catch-up run was observed during acceptance
- live database was not replaced, production configuration was unchanged, existing backups were preserved, and VM-A was not touched

H2 local backup/restore exit criteria are satisfied. Off-host second-copy resilience is tracked as a follow-up reliability item rather than a blocker for the completed local H2 scope.

#### H3 — Reference Merchant Integration Flow

Status: **COMPLETE — canonical merchant flow implemented and contract-tested 2026-09-25**

Runbook: [PHASE_H3_REFERENCE_MERCHANT_FLOW.md](PHASE_H3_REFERENCE_MERCHANT_FLOW.md)

Reference implementation:

```text
backend/examples/reference_merchant.py
```

- [x] Define the merchant trust boundary: Bearer API key and webhook signing secret remain backend-only
- [x] Reserve merchant order identity plus a stable Idempotency-Key before remote payment creation
- [x] Demonstrate authenticated payment creation and durable `merchant_reference` / `payment_id` mapping
- [x] Build customer checkout URLs containing only the high-entropy `payment_id` capability
- [x] Demonstrate exact-reference merchant recovery after uncertain create outcomes
- [x] Verify webhook HMAC against exact raw body bytes with constant-time comparison
- [x] Enforce a bounded webhook timestamp replay window
- [x] Durably deduplicate webhook processing by stable `event_id`
- [x] Handle create/webhook races without consuming unknown-order events
- [x] Apply merchant state by increasing `payment_version`, not status ranking, so reorg rollback events remain correct
- [x] Keep the example standalone and Python 3.10 standard-library-only; add no production daemon/infrastructure
- [x] Add deterministic contract tests for create/recovery/checkout/signature/replay/dedup/reorg behavior

H3 is documentation/example work and does not alter production Payment Platform
runtime behavior.

#### H4 — Merchant SDK Helpers / Examples

Status: **COMPLETE — devkit implementation and CI verified 2026-09-25**

Repository: `edisontw/pepepow-devkit`

Implemented at devkit main `05ffb264f615b70fcd5697a9f7c9110fdd9c2aa4`:

```text
packages/pepewpay-merchant/
examples/merchant-node/
```

- [x] Keep `@pepepow/pepew-js` browser/protocol-focused; isolate merchant-secret handling in a separate server-side package
- [x] Add Node.js 20+ `@pepepow/pepewpay-merchant` package
- [x] Add authenticated payment-create helper with stable `Idempotency-Key`
- [x] Add exact `merchant_reference` recovery helper for uncertain create outcomes
- [x] Add PepewPay checkout URL builder that exposes only the high-entropy `payment_id`
- [x] Add exact-raw-body webhook HMAC-SHA256 verification with constant-time comparison
- [x] Add bounded default webhook replay-window verification
- [x] Validate Payment Event Envelope v1 identity/version before merchant application use
- [x] Add reorg-safe `payment_version` ordering helper rather than status ranking
- [x] Keep merchant durable order storage, event-ID deduplication, and fulfillment state application-owned
- [x] Add framework-neutral durable-store composition examples under `examples/merchant-node/`
- [x] Add CI build/tests and preserve the PepewPay browser bundle Node-shim guard
- [x] Verify devkit main CI: `pepew-js` 23/23, merchant SDK 10/10, PepewPay 11/11, static build/browser-bundle guard PASS, deployment-branch publish PASS

H4 adds no production runtime dependency and does not move mnemonic, private
keys, transaction signing, merchant API keys, or webhook signing secrets into
customer/browser code.

Phase H exit criteria are satisfied for the planned H1-H4 baseline.

### Phase I — Merchant Developer Onboarding / Distribution

Status: **CLOSED — all Phase I distribution, onboarding, testing, and platform-integration exit criteria satisfied 2026-10-02**

Primary repository: `edisontw/pepepow-devkit`

Supporting repository: `edisontw/pepepow-electrumx-service` for canonical API,
security, production-integration, and webhook contract documentation.

Goal: make the production-validated Payment Platform straightforward for an
ordinary merchant developer to install, understand, test, and integrate before
building platform-specific adapters.

Development sequence:

#### I1 — SDK Distribution Baseline

- [x] Keep browser/protocol utilities in `@pepepow/pepew-js` and merchant-secret handling in `@pepepow/pepewpay-merchant`
- [x] Keep package versions independent and SemVer-based; pre-1.0 breaking changes require a minor-version bump, while compatible fixes/additions use patch releases where practical
- [x] Select the public npm registry as the intended canonical package-distribution channel for reusable SDK packages
- [x] Keep `pepewpay-dist` as the static checkout artifact path; PepewPay itself is not a merchant runtime dependency
- [x] Add package metadata and clean-consumer tarball install/import tests for both reusable SDK packages
- [x] Adopt the MIT License for the DevKit and reusable SDK artifacts
- [x] Make `@pepepow/pepew-js` and `@pepepow/pepewpay-merchant` public-release-ready while keeping registry publication an explicit release action
- [x] Keep package tarballs allowlisted to built output plus package metadata/README/LICENSE; do not ship source/test trees by accident
- [x] Add a first-release runbook plus a tokenless GitHub Actions OIDC workflow for subsequent trusted-publishing releases (`pepepow-devkit` commit `87dd9199ebc79be156c27d0fab3df4967d762346`)
- [x] Confirm npm `@pepepow` organization/scope ownership and interactive first-publish access with account 2FA
- [x] Publish the first tagged public SDK releases and verify clean registry installation/import (`@pepepow/pepew-js@0.1.0` and `@pepepow/pepewpay-merchant@0.1.0`, release tags from DevKit commit `87dd9199ebc79be156c27d0fab3df4967d762346`; clean registry import smoke PASS 2026-10-02)

Release policy:

- GitHub `main` remains development source of truth.
- Registry releases must come from a tested, tagged commit rather than an arbitrary working tree.
- Merchant API keys, webhook secrets, mnemonic/private keys, and signing code must never be packaged into examples or published artifacts.
- The bootstrap `0.1.0` publish is intentionally interactive with npm account 2FA from an exact tested/tagged commit; do not introduce a long-lived CI write token solely for first publish. After each package exists, configure npm trusted publishing/OIDC against `.github/workflows/npm-release.yml` for subsequent releases.
- A registry release is not required on production VM-A or VM-B and must add no production Node.js runtime dependency.

#### I2 — Runnable Merchant Sample Application

Status: **COMPLETE — runnable Node/SQLite reference app and CI verified 2026-09-26**

Implementation: `pepepow-devkit/examples/merchant-app/`

- [x] Add one complete Node.js sample merchant application with a small durable SQLite order/event store
- [x] Demonstrate order creation, stable idempotency, checkout URL generation, uncertain-create recovery, raw-body webhook verification, event-ID deduplication, and `payment_version` ordering
- [x] Keep the sample intentionally small and framework-light: Node HTTP + SQLite + merchant SDK, with no Redis/PostgreSQL/queue
- [x] Add deterministic local tests and a one-command development start path after normal dependency setup
- [x] Keep the internal order-create route localhost/trusted-backend oriented rather than presenting it as an unauthenticated public checkout API
- [x] Keep fulfillment policy application-owned; the sample stores payment state but does not auto-ship goods

I2 verification:

- devkit commit `ccc162fa8361bb90f1902e25fca9889fb1292b76`
- dedicated Node 22 merchant-sample CI job passed SQLite install, local SDK build, store/service tests, and server syntax check
- existing Node 20 DevKit/SDK/PepewPay regression job remained green
- existing `pepewpay-dist` publish job remained green

#### I3 — Production Integration Guide

Status: **COMPLETE — production onboarding guide and framework patterns verified 2026-09-26**

Implementation in `pepepow-devkit`:

```text
docs/MERCHANT_QUICK_START.md
examples/merchant-app/scripts/register-webhook.mjs
examples/webhooks/express.mjs
examples/webhooks/fastify.mjs
```

- [x] Add a short merchant Quick Start from API key/webhook setup through the first checkout
- [x] Add production configuration, timeout/retry, secret-storage, webhook, reorg, fulfillment, logging/privacy, and recovery guidance
- [x] Add copyable Express/Fastify webhook examples that preserve exact raw-body verification
- [x] Document upgrade/version compatibility expectations between SDK releases and Payment API v1
- [x] Add a webhook registration helper that keeps the Bearer API key out of command-line arguments, does not print the one-time signing secret, and writes it to a restrictive local file for transfer into server-side secret storage
- [x] Ignore local `.env`, merchant SQLite state, and one-time webhook registration files in Git
- [x] Keep webhook testing compatible with production SSRF policy; do not weaken HTTPS/public-network target validation for local development

I3 verification:

- devkit commits `0ba372f4e4638350276498ca332aa7818146b676` and `b79f7cc5c7676f4d101f77832e2101bb40b9df68`
- GitHub Actions run `36211575239` completed successfully
- merchant-sample job passed registration-helper and Express/Fastify syntax checks plus the existing SQLite merchant tests
- Node 20 DevKit/SDK/PepewPay regression job remained green
- existing `pepewpay-dist` publish job remained green

#### I4 — Sandbox / Test Integration Strategy

Status: **COMPLETE — deterministic contract harness and bounded live smoke verified 2026-09-26**

Implementation in `pepepow-devkit`:

```text
docs/TESTING_AND_SANDBOX.md
examples/merchant-app/test-support/mock-payment-platform.mjs
examples/merchant-app/tests/contract-harness.test.mjs
examples/merchant-app/scripts/live-smoke.mjs
```

- [x] Define a developer test path before introducing a separate always-on sandbox service
- [x] Prefer deterministic local/mock contract tests plus an explicitly bounded live smoke path where appropriate
- [x] Document test merchant credentials/data isolation and how webhook retry/reorg cases are exercised
- [x] Keep CI credential-free and zero-network for merchant contract behavior
- [x] Keep live smoke explicit/operator-only, one short-lived payment intent per invocation, and never run it automatically in CI
- [x] Preserve production webhook HTTPS/SSRF protections instead of weakening them for localhost testing
- [x] Defer dedicated sandbox infrastructure until real integration demand justifies its operational cost

I4 verification:

- devkit commits `9b83334c61ed7e800ab94ecdb6911cd33c1fec34` and `591e153e4ad24af458bd02adb08873c5f061e407`
- GitHub Actions run `36219255658` completed successfully
- merchant-sample CI covered real `MerchantClient` behavior against deterministic mock API, lost-response recovery, idempotency conflict, signed webhook verification, duplicate `event_id`, and higher-version reorg rollback
- existing Node 20 DevKit/SDK/PepewPay regression and `pepewpay-dist` publish jobs remained green
- no production VM/runtime change was required

#### I5 — Platform Integrations

Status: **COMPLETE — WooCommerce, Telegram, and Discord production-shaped integrations accepted and cleaned up**

Planned order:

```text
WooCommerce
  -> Telegram merchant/payment adapter
  -> Discord
```

WooCommerce implementation:

```text
pepepow-devkit/integrations/woocommerce/pepew-payments/
```

##### I5.1 — WooCommerce classic gateway skeleton

- [x] Add PEPEW currency and 8-decimal pricing support
- [x] Add classic `WC_Payment_Gateway`
- [x] Persist deterministic `merchant_reference`, stable `Idempotency-Key`, and exact amount snapshot before remote create
- [x] Create Payment API v1 intents and recover uncertain creates by exact merchant reference
- [x] Persist payment capability/version/status and redirect to PepewPay
- [x] Fail closed if the Woo order amount changes after PEPEW create identity/payment binding
- [x] Use WooCommerce order CRUD only; CI rejects direct post/order-storage writes
- [x] Keep Checkout Blocks unsupported rather than overstating compatibility
- [x] Add PHP lint and deterministic order-identity tests
- [x] Declare HPOS compatibility only after a real WooCommerce runtime matrix passes

I5.1 verification:

- devkit commits `abb67d5c9e8cbab98196b9f204c2bbe56154dad7`, `7d7bdbeeacc3354c9ce2e14d943d3dbb70e91d60`, and `522c0fea18b85003ffe0f6e3cc5d707f9f3eea3f`
- GitHub Actions run `36223238329` completed successfully
- WooCommerce adapter job passed PHP lint, deterministic order identity tests, and no-direct-order-storage guard
- existing merchant-sample, DevKit/SDK/PepewPay, and `pepewpay-dist` jobs remained green
- no production VM/runtime change was required

##### I5.2 — WooCommerce webhook and order lifecycle

Status: **COMPLETE — implementation and CI verified 2026-09-26**

- [x] Verify Webhook v1 HMAC against exact raw request bytes in PHP
- [x] Persist webhook signing configuration server-side only
- [x] Resolve Woo order from deterministic merchant reference and validate stored payment identity/amount
- [x] Bind `payment_id` safely if webhook delivery wins the create-response race
- [x] Durably order updates by `event_id` and `payment_version`; same-event retries are idempotent and lower versions cannot overwrite newer state
- [x] Accept higher-version reorg rollback instead of ranking status strings monotonically
- [x] Use a short-lived per-event WordPress lock to suppress concurrent duplicate handling without external queue infrastructure
- [x] Map verified PEPEW state to Woo order lifecycle while keeping Payment Platform authoritative
- [x] Use WooCommerce `payment_complete()` for normal confirmed/overpaid transitions
- [x] Move processing orders to on-hold + review if a higher-version reorg reduces confirmation
- [x] Do not automatically resurrect cancelled/refunded orders; conflicting later payment state requires manual review
- [x] Preserve large JSON atom values through `JSON_BIGINT_AS_STRING`
- [x] Keep secrets and full capability URLs out of webhook error responses/order notes

I5.2 verification:

- devkit commits `a4f990540db814a1e632719155d5c3a8890ad61b`, `9bb982191cb727a955e6e50b675baaa29407f412`, `9ccf70928b44bdb3d3afdcadbe77efebca2a2fad`, `ef400850a443753a2a0bde1e010737b5bd31646b`, and `62f9a1f1f8d7a23c81f45fcdad1b25005522807c`
- GitHub Actions run `36225272365` completed successfully
- WooCommerce adapter job passed PHP lint, order-identity parsing, exact-body webhook/tamper/replay tests, large atom parsing, order-state/reorg policy tests, and no-direct-order-storage guard
- existing merchant-sample, DevKit/SDK/PepewPay, and `pepewpay-dist` jobs remained green
- real WordPress/WooCommerce + HPOS runtime acceptance is intentionally deferred to I5.3
- no production VM/runtime change was required

##### I5.3 — Checkout Blocks + HPOS acceptance

Status: **COMPLETE — Checkout Blocks + legacy/HPOS runtime matrix verified 2026-09-26**

- [x] Add WooCommerce Checkout Block payment-method integration using server-side `AbstractPaymentMethodType` and client-side `registerPaymentMethod()`
- [x] Keep payment execution on the existing `WC_Payment_Gateway` path rather than creating a second payment flow
- [x] Pin acceptance runtime to WordPress 7.1.2 / WooCommerce 11.1.2 / PHP 8.1
- [x] Test gateway registration, Blocks hook/script/settings, Woo order CRUD/meta reload, and `payment_complete()` with legacy order storage
- [x] Run the same runtime smoke with HPOS enabled
- [x] Declare `cart_checkout_blocks` compatibility only after runtime acceptance
- [x] Declare `custom_order_tables` compatibility only after runtime acceptance
- [x] Set `WC tested up to: 11.1.2`

I5.3 verification:

- devkit implementation/acceptance commits include `a0e8e8552cdb60344a5c4f5dd753d3ce3d106c83`, `19a4a9ddd9efea2299dd901a58656a6a34be230b`, `2fefa5ee40212b0cc8a52ddcd1f5114293ddf4fe`, and `02a30dd441335e3b50aba397f2443c20f9ffc4a8`
- GitHub Actions run `36231075337` completed successfully after compatibility declarations were enabled
- `WooCommerce runtime (legacy)` and `WooCommerce runtime (hpos)` both passed the pinned wp-env smoke matrix
- existing Woo unit/static checks, merchant-sample, DevKit/SDK/PepewPay, and `pepewpay-dist` jobs remained green
- no production Payment Platform or VM runtime change was required

##### I5.4 — distributable WooCommerce plugin

Status: **COMPLETE — packaged plugin and externally reachable paid E2E verified 2026-09-30**

- [x] Produce an allowlist-based installable plugin ZIP
- [x] Verify package integrity and exclude test/dev/environment/repository files
- [x] Clean-install the exact CI-built ZIP in a fresh WordPress/WooCommerce runtime
- [x] Verify activation with WooCommerce present and rejection when the required dependency is inactive/absent
- [x] Verify overwrite upgrade preserves merchant settings
- [x] Verify deactivation preserves settings and explicit uninstall removes merchant API/webhook secrets
- [x] Preserve historical Woo order payment metadata on uninstall for reconciliation/audit continuity
- [x] Add setup/upgrade/uninstall, webhook secret rotation, rollback, and production acceptance guidance
- [x] Exercise repeated checkout/back-button behavior without a second create
- [x] Exercise transport-loss create recovery by exact merchant reference
- [x] Exercise duplicate/stale webhook handling and higher-version reorg rollback in both legacy and HPOS runtime modes
- [x] Publish CI artifact `pepew-payments-woocommerce` containing the ZIP and SHA-256 checksum
- [x] Complete one externally reachable staging/live Woo -> PepewPay -> wallet -> Payment Platform -> webhook -> Woo order paid E2E before production-ready status

I5.4 automated verification:

- devkit implementation commit `58c6e300d392e3a0a9ae581bbdebd9869b3d90e3`
- CI/package fixes include `e67768b074f6aadd446c125fe35e07b7f15ae835`, `fa7614d26f38d62a368fb5a6e2c6cf3c3443fff6`, `e8fa4ccd879a90d93e63bebf4f2d0a662627055b`, and `51f8601b591f5e252172ae8078e96cd0a5dda041`
- GitHub Actions run `36244246216` completed successfully
- `woocommerce-package`, `WooCommerce runtime (legacy)`, and `WooCommerce runtime (hpos)` all passed
- deployment/acceptance runbook: `pepepow-devkit/docs/WOOCOMMERCE_DEPLOYMENT.md`
- no production VM-A/VM-B runtime change was required

I5.4 manual staging progress (2026-09-27):

- externally reachable Windows/Local WordPress staging verified through temporary Cloudflare HTTPS
- exact CI-built plugin ZIP install/overwrite-upgrade succeeded
- Payment API Bearer access from WordPress returned HTTP 200
- public Woo webhook receiver was registered and moved from expected unconfigured HTTP 503 to expected signature-validation HTTP 400 for an unsigned probe
- real payment creation reached the authoritative Payment Platform and exposed a decimal-format comparison bug (`0.10000000` vs `0.1`); the plugin now compares exact atoms and CI regression coverage was added
- a second edge case was found while evaluating self-payment: `overpaid` may still be below the configured confirmation policy; Woo now keeps such orders on hold until `policy_confirmed_sats` covers the requested amount
- after upgrading the plugin, the checkout successfully redirected through PepewPay to the PEPEW Light web wallet
- final externally reachable paid E2E passed on 2026-09-30 using a payer address different from the merchant receiving address: Woo checkout -> PepewPay -> integrated Wallet -> authoritative Payment Platform -> signed webhook -> Woo order paid

##### I5.5 — Telegram merchant/payment adapter

Status: **COMPLETE — production Telegram payment/webhook E2E verified 2026-09-30**

Implementation:

```text
pepepow-devkit/integrations/telegram/
```

Architecture boundary:

- this is not a second PEPEW wallet bot and does not replace the Telegram
  Bot/Mini App in `edisontw/pepepow-wallet-suite`
- `pepepow-wallet-suite` remains payer-side wallet UX and client-side signing
- I5.5 is merchant-side payment acceptance: Payment API create/recovery,
  PepewPay checkout link delivery, authoritative webhook state, and Telegram
  message/status updates
- no mnemonic/private-key/signing code belongs in the merchant adapter
- a merchant may embed the adapter into its own Telegram bot; only transport
  testing requires a temporary/dedicated test bot if convenient

Wallet handoff alignment (2026-09-29):

- `https://wallet.pepepow.net` is the primary integrated payer Wallet for Telegram, PepewPay, merchant-payment handoff, and future platform integrations
- `https://light.pepepow.net/wallet/` remains a supported standalone/backup PEPEW Light Wallet and is not being retired or redirected
- both wallets remain non-custodial and use PEPEW Light API for chain access
- `pepepow-wallet-suite` commit `87cfd2969742023e755c7ac82d507dfe9a63be12` removes the legacy 1 PEPEW send floor, aligns the integrated Wallet with the standalone Light Wallet's dust-based send policy, and adds payment-handoff regression coverage
- integrated Wallet commit `87cfd2969742023e755c7ac82d507dfe9a63be12` passed production acceptance on 2026-09-29, including the representative sub-1-PEPEW handoff/send path
- PepewPay source commit `64903ab98fa8d7ab8a042747437ef8c384311648` changes the preferred web-wallet handoff to `wallet.pepepow.net/send`; the final CI artifact from source commit `e59f194683b1150be5af50e242fd62cd665e521a` was deployed on VM-B on 2026-09-29 and production health/status/static checks plus browser handoff to `wallet.pepepow.net/send?to=...&amount=0.1` all passed

- [x] keep `TELEGRAM_BOT_TOKEN`, merchant API key, and Payment Platform webhook signing secret server-side only
- [x] derive stable Telegram payment identity from bot/chat/message identity while hashing raw Telegram identifiers before `merchant_reference`
- [x] derive a stable Idempotency-Key for duplicate/retried Telegram updates
- [x] create Payment API intents through the existing server-side merchant SDK rather than duplicating payment authority
- [x] recover uncertain create outcomes by exact merchant reference
- [x] build a Telegram `sendMessage` payload with an HTTPS PepewPay inline URL button
- [x] apply updates by increasing `payment_version`, not status ranking
- [x] keep unconfirmed `overpaid` state pending until `policy_confirmed_sats` covers the requested amount
- [x] add deterministic no-network/no-secret contract tests
- [x] add a bounded operator-only Telegram Test Bot API transport harness limited to `getMe`, `getUpdates`, and `sendMessage`; no Payment API create/webhook/transaction
- [x] deploy/verify integrated Wallet payment compatibility from `pepepow-wallet-suite` commit `87cfd2969742023e755c7ac82d507dfe9a63be12`, including one representative sub-1-PEPEW send (production acceptance PASS 2026-09-29)
- [x] update PepewPay's preferred web-wallet handoff from the standalone Light Wallet to `wallet.pepepow.net` while keeping the standalone wallet independently usable (devkit commit `64903ab98fa8d7ab8a042747437ef8c384311648`; final static artifact source `e59f194683b1150be5af50e242fd62cd665e521a`; VM-B deploy + production browser handoff acceptance PASS 2026-09-29)
- [x] run Telegram transport smoke acceptance: dedicated Test Environment PASS 2026-09-29 and normal production Bot API PASS 2026-09-30 using the dedicated merchant/payment bot; tokens remained local and were not stored in GitHub
- [x] run one end-to-end Telegram payment/webhook message update using a payer address different from the merchant receiving address (production acceptance PASS 2026-09-30: real 0.1 PEPEW invoice -> paid_unconfirmed -> paid_confirmed -> terminal paid; exact-body signed webhook accepted; Telegram message updated; temporary webhook endpoint disabled during cleanup)

The normal Telegram production Bot API transport smoke passed on 2026-09-30 with the dedicated merchant/payment bot. The Telegram transport layer now also supports a separate normal Telegram production Bot API environment through `TELEGRAM_API_ENV=production`; this keeps the existing Wallet Bot webhook/control-plane untouched while allowing the dedicated merchant/payment bot to be exercised directly.

The payment/webhook E2E operator harness creates one bounded real payment, registers a temporary filtered webhook endpoint, verifies exact-body webhook HMAC, applies only increasing payment versions, updates the Telegram message, and disables the temporary endpoint during normal cleanup. Production acceptance passed on 2026-09-30 using the dedicated normal Telegram merchant/payment bot, a payer address different from the merchant receiving address, and a temporary public HTTPS Apache reverse-proxy route to the localhost receiver. The invoice progressed through `paid_unconfirmed` to `paid_confirmed`, the terminal state was `paid`, and the temporary Payment Platform webhook endpoint was disabled successfully.

The first Telegram transport increment intentionally created no Payment Platform invoice. The operator-only smoke harness was added in `pepepow-devkit` commit `4e91a179d4cfabe041255fa6b19fab55fd38b3fb`; the dedicated Telegram Test Environment transport run passed on 2026-09-29, and the dedicated normal production merchant/payment bot transport plus real payment/webhook E2E passed on 2026-09-30. The existing Wallet Bot webhook/control-plane remained untouched and no bot token or merchant secret was stored in GitHub/chat.


##### I5.6 — Discord merchant/payment adapter

Status: **COMPLETE — live Discord payment/webhook E2E and temporary-infrastructure cleanup verified 2026-10-01**

Implementation:

```text
pepepow-devkit/integrations/discord/
```

Architecture boundary:

- Discord is a merchant/payment adapter, not a wallet; mnemonic, private keys, UTXO selection, and transaction signing remain client-side only
- Payment Platform remains authoritative for payment state
- v1 request identity is derived from `application_id + channel_id + interaction_id`, hashed before entering `merchant_reference` or `Idempotency-Key`
- no Discord user ID is required for payment authority
- the intended transport is a slash-command interaction for request initiation, followed by an ordinary bot channel message for the durable PepewPay/status surface
- using a normal bot channel message avoids depending on a short-lived interaction token for later confirmation updates
- Discord-visible payment buttons expose only the public PepewPay capability URL
- `DISCORD_BOT_TOKEN`, merchant API key, and Payment Platform webhook signing secret remain server-side only

Current baseline:

- [x] add isolated `integrations/discord` package with Node 20+ contract tests
- [x] derive stable hashed Discord merchant reference and idempotency key
- [x] create Payment API intents through `@pepepow/pepewpay-merchant`
- [x] recover uncertain create outcomes by exact merchant reference
- [x] build Discord message payload with an HTTPS PepewPay link button and mentions suppressed
- [x] apply authoritative updates only by increasing `payment_version`
- [x] keep unconfirmed `overpaid` pending until `policy_confirmed_sats` covers the requested amount
- [x] keep baseline tests credential-free and network-free
- [x] wire Discord adapter contract tests into DevKit CI
- [x] verify Discord interaction request signatures against exact raw request bytes
- [x] implement a bounded PING + slash-command transport smoke harness that creates no real PEPEW payment
- [x] implement normal bot channel message create/edit helpers through Discord REST
- [x] run the Discord transport smoke against a dedicated Discord application/test server: signed PING -> PONG, `/pepew-pay amount:0.1`, immediate test-only acknowledgement, ordinary bot channel message, HTTPS PepewPay test button; no Payment Platform invoice was created
- [x] add a bounded real-payment E2E harness with temporary filtered webhook registration, exact-body HMAC verification, increasing `payment_version` application, and same-message Discord REST edits
- [x] run the authoritative signed Payment Platform webhook -> Discord same-message update E2E
- [x] complete one real 0.1 PEPEW payment with payer address different from merchant receiving address; observed `paid_unconfirmed` -> `paid_confirmed` and same-message Discord update on 2026-10-01

Planned transport sequence:

```text
Discord slash command
  -> exact-body Discord signature verification
  -> immediate defer/ack
  -> Payment Platform create/recovery
  -> normal bot channel message + PepewPay link button
  -> integrated Wallet
  -> signed Payment Platform webhook
  -> Discord message update by payment_version
```

The credential-free Discord transport increment uses no production Payment API call and no new VM daemon. Live transport acceptance passed on 2026-09-30 with the dedicated Discord application: Discord's signed PING was acknowledged, `/pepew-pay amount:0.1` was accepted, and an ordinary bot channel message with the HTTPS PepewPay test button was delivered without creating a Payment Platform invoice. The first live smoke exposed a harness-only HTTP keep-alive shutdown issue after delivery; DevKit commit `4b508ee4e0b3dfe4af2e530db406e2e17d495c92` hardened bounded listener shutdown without changing the transport contract.

DevKit commit `80d9acafd24a786cf55d5240f8756a2f4825f417` added the bounded operator harness for the real acceptance run. It keeps `127.0.0.1:8789` localhost-only, uses separate exact paths for Discord interactions and Payment Platform webhooks, registers a temporary filtered webhook endpoint, creates one configured small invoice only after a valid slash command, verifies webhook HMAC over the exact raw body, applies only increasing payment versions, edits the same ordinary Discord message, and disables the temporary webhook endpoint during normal cleanup. No mnemonic/private key/signing code is added to the server.

Final I5.6 acceptance passed on 2026-10-01 with a real 0.1 PEPEW payment from a payer address different from the merchant receiving address. The Payment Platform advanced through `paid_unconfirmed` and `paid_confirmed`, and the same ordinary Discord bot message was updated to confirmed. The first live real-payment attempt exposed a redundant operator-supplied Discord application-ID mismatch after the request had already passed Ed25519 verification; DevKit commit `dfe6ca346ca48d6b9c724c40d15cddfdaeeb0941` removed that extra operator check and uses the authenticated `application_id` from the verified interaction instead.

Discord closure evidence boundary (audit 2026-10-01): the retained payment evidence proves the authoritative `paid_unconfirmed` -> `paid_confirmed` progression and same-message Discord update. The earlier chat did not preserve the harness's final terminal/cleanup lines, so the temporary infrastructure was audited separately instead of inferred from the Discord UI.

I5.6 cleanup closure checklist — operator-confirmed **PASS**:

- [x] real 0.1 PEPEW invoice reached `paid_unconfirmed` then `paid_confirmed`
- [x] same ordinary Discord bot message updated to confirmed
- [x] nothing remains listening on `127.0.0.1:8789` on the temporary test host
- [x] temporary Payment Platform webhook endpoint is disabled
- [x] temporary Apache Discord E2E `ProxyPass` / `ProxyPassReverse` routes are absent
- [x] `apache2ctl configtest` passed, Apache was reloaded, and normal `pepepow.net` service remained healthy
- [x] Discord Developer Portal Interactions Endpoint no longer points at the deleted temporary route
- [x] temporary Discord/Payment E2E shell environment variables and secrets were cleared

I5.6 is therefore closed. These cleanup actions did not change the accepted adapter contract, Payment Platform authority, or client-side signing boundary.

- [x] Start WooCommerce first because it exercises a complete cart/order/payment/webhook lifecycle
- [x] Reuse the generic Payment API contracts; do not fork payment authority into the plugin
- [x] Use the completed WooCommerce lifecycle as the adapter pattern, then complete Telegram and proceed to Discord without changing authoritative payment semantics

Phase I exit criteria:

- [x] a new merchant developer can reach a working integration from current docs and released packages without reading backend source — first public SDK packages released and clean registry install/import verified 2026-10-02
- [x] the reference sample survives restart/retry/webhook-duplicate/reorg-state scenarios correctly
- [x] production integration guidance keeps all merchant secrets server-side and exposes only intended payment capabilities to browsers
- [x] at least the first real platform adapter can be built on the generic integration contract without changing authoritative payment semantics

Phase I closure audit (2026-10-02):

- I5.4 WooCommerce: functional/paid E2E complete; DevKit stale acceptance docs corrected
- I5.5 Telegram: complete, including terminal `paid` and temporary webhook cleanup evidence
- I5.6 Discord: complete through `paid_confirmed` + same-message update, with separate temporary runtime/infrastructure cleanup audit PASS
- I1 SDK distribution: npm `@pepepow` scope ownership confirmed; `@pepepow/pepew-js@0.1.0` and `@pepepow/pepewpay-merchant@0.1.0` published from exact Git tags; clean public-registry install/import smoke PASS
- npm Trusted Publisher configuration for both packages completed 2026-10-02 against DevKit workflow `npm-release.yml`; future releases use GitHub Actions OIDC without a long-lived npm write token, with first OIDC publish verification deferred to the next legitimate version release
- current post-audit `main` CI is green for both Phase I closure commits
- security architecture remains non-custodial and transaction-level: wallet signing stays client-side, Payment Platform remains authoritative, and ElectrumX remains private
- **Phase I is CLOSED. All Phase I exit criteria are satisfied. Future npm releases should use the prepared trusted-publishing/OIDC workflow after npm Trusted Publisher configuration; that hardening is not a Phase I blocker.**


### Phase J — Operational Resilience

Status: **IN PROGRESS — J1a-J1c complete; J1d production acceptance ready**

Primary repository: `edisontw/pepepow-electrumx-service`

Goal: remove the remaining single-host backup failure domain around the
authoritative VM-B SQLite state without adding an always-on backup service,
new database, queue, or high-background-CPU infrastructure.

This phase deliberately starts with the one explicit follow-up item left from
H2. It does not add new merchant/payment authority or change wallet signing
boundaries.

#### J1 — Off-host second copy

Initial design constraints:

- [x] copy tooling accepts only a completed H2 backup + sidecar manifest and reruns the local restore drill immediately before transfer; it never copies the live SQLite database directly
- [ ] keep the transfer passive and bounded (for example a scheduled one-shot transfer); do not introduce a continuously running backup daemon
- [x] choose the first off-host destination deliberately: VM-A is the initial second-copy target, using a dedicated receive-only SSH credential and directory; do not reuse the ElectrumX tunnel key and do not use edison2 as production backup storage ([PHASE_J1_OFFHOST_BACKUP.md](PHASE_J1_OFFHOST_BACKUP.md))
- [ ] preserve restrictive permissions and avoid exposing payment data, merchant metadata, filesystem paths, or credentials in logs
- [x] receiver verifies destination SHA-256/size/manifest plus SQLite integrity/schema/counts and a non-destructive restore drill before promoting the second copy
- [ ] make transfer failure non-authoritative: it must not stop the Payment Platform or invalidate a successful local H2 backup
- [x] apply bounded retention on the off-host destination without deleting unrelated/manual/orphan artifacts; receiver defaults to 14 complete exact-name J1 pairs
- [ ] add a non-destructive restore drill from the off-host copy before marking J1 complete
- [ ] keep CPU/I/O/network use low enough for the existing single-core production hosts
- [x] add deterministic tests for transfer selection, checksum mismatch, partial-copy cleanup, retention boundaries, receiver process failure, timeout, and source-backup preservation
- [ ] deploy only after the destination and credential boundary are reviewed; J1d runbook is ready, and no VM-A/VM-B runtime authority change is required

Preferred implementation order:

```text
J1a  destination/trust-boundary decision + runbook (COMPLETE 2026-10-02)
  -> J1b one-shot copy + verification tooling (IMPLEMENTED; CI required)
  -> J1c retention + failure-isolation tests
  -> J1d production acceptance
  -> J1e off-host restore drill
```

Exit criteria:

- at least one verified recent authoritative backup exists outside VM-B;
- a failed off-host transfer cannot affect payment creation, watcher/webhook
  operation, or the successful local H2 backup;
- checksum/manifest verification detects corruption or partial transfer;
- an off-host copy can be restored non-destructively and pass SQLite integrity
  and required-schema checks;
- no new continuously running infrastructure is introduced.


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
