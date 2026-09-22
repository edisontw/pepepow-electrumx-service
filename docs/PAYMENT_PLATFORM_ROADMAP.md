# PEPEW Payment Platform Roadmap

Last updated: 2026-09-22

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

Production endpoint:

```text
https://light.pepepow.net
```

Current host baseline from the 2026-09-21 read-only audit:

```text
Oracle Cloud Ubuntu 22.04
ARM64 / aarch64
1 CPU core
5.8 GiB RAM
Python 3.10.12
Node.js not installed
```

Current runtime:

```text
Internet
  -> Nginx :80/:443
  -> PEPEW Light FastAPI 127.0.0.1:8088
  -> ElectrumX 127.0.0.1:50001
  -> PEPEPOWd RPC 127.0.0.1:8834
```

PEPEPOWd, ElectrumX, PEPEW Light, and Nginx are already running. ElectrumX and PEPEPOWd RPC remain non-public.

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

Status: **COMPLETE — production rollout remains gated**

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

Exit criteria:

- safe retry after receiver failure
- stable event IDs
- no secret leakage in logs
- blocked unsafe destinations

### Phase F — Production rollout / deployment split

Status: **IN PROGRESS — backend payment and PepewPay production E2E passed on the current host; webhook production E2E remains**

Preferred direction when the event/webhook workload becomes active:

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
- PepewPay static hosting if desired
```

The reason to split is primarily CPU/failure/security isolation, not current RAM pressure.

Current production rollout status (2026-09-22):

- PEPEW Light baseline deploy passed 184 backend tests on production Python 3.10
- Payment API authorization boundary verified (unauthenticated POST -> 401)
- persisted payment creation verified (authenticated POST -> 201)
- public read-only payment status verified through Nginx
- existing client-side wallet broadcast verified
- persistent watcher advanced a real 1 PEPEW payment to `paid_confirmed`
- Payment API and watcher are now enabled on the current host for rollout validation
- webhook worker remains disabled pending its own production E2E
- reproducible production webhook E2E helpers are prepared to verify SSRF rejection, HMAC, real HTTP 503 retry -> 204 delivery, stable IDs/body, delivery log, and cleanup without printing secrets
- PepewPay static UI is deployed and verified at `https://light.pepepow.net/pay/`
- Live PepewPay production E2E is verified with a new 0.1 PEPEW payment through web-wallet handoff, broadcast, `paid_unconfirmed`, and `paid_confirmed`
- Production retrieval of the private `pepepow-devkit` static artifact uses a dedicated repository-scoped read-only SSH deploy key; do not store a long-lived GitHub PAT in shell history or the repository

ElectrumX must remain private. A second VM should connect only through an approved private OCI network path or a controlled tunnel; do not expose port 50001 to the Internet.

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
