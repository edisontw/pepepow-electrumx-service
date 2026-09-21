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

- each payment check creates an ElectrumX client/session and closes it after the request
- it queries `server.version`, balance, history, and mempool
- it does not currently use a dedicated payment cache
- `confirmations=N` is not yet a true transaction-height-based N-confirmation check
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

Status: **IN PROGRESS**

Repository: `pepepow-devkit`

- [x] Define PEPEW Payment URI v1
- [x] Add URI parser/serializer test vectors
- [x] Bootstrap `packages/pepew-js`
- [x] Implement exact amount/address validation shared by URI tooling
- [x] Add package tests and CI configuration
- [x] Document compatibility and versioning rules
- [ ] Verify the first GitHub Actions test run before closing Phase A

Exit criteria:

- deterministic parse/serialize behavior
- invalid input tests
- published v1 spec in repo
- no wallet-secret handling

### Phase B — Payment correctness and Light optimization

Status: **PLANNED**

Repository: `pepepow-electrumx-service`

- [ ] Define transaction-level payment state machine
- [ ] Add transaction output matching
- [ ] Implement true N-confirmation logic
- [ ] Define expiry and terminal-state behavior
- [ ] Add short-TTL/deduplicated reads for legacy `/api/payment/check`
- [ ] Reduce unnecessary ElectrumX work where verified safe
- [ ] Preserve existing public API compatibility
- [ ] Add authoritative unit tests

Exit criteria:

- balance-before-invoice does not create a false payment
- later spending does not revert a paid invoice
- partial/multiple payments work
- N confirmations are tested from transaction heights

### Phase C — PepewPay

Status: **PLANNED**

Repository: `pepepow-devkit`

- [ ] Build PepewPay PWA
- [ ] Render Payment URI QR
- [ ] Add wallet handoff
- [ ] Display persisted payment state
- [ ] Keep frontend deployable as static output
- [ ] Avoid requiring Node.js on the production chain host

Exit criteria:

- end-to-end demo from payment creation to wallet handoff/status display
- no private key or mnemonic leaves the client wallet

### Phase D — Payment/Event Gateway

Status: **PLANNED**

Repository: `pepepow-electrumx-service`

- [ ] Add SQLite payment/event persistence
- [ ] Add payment creation/status API
- [ ] Implement persistent ElectrumX event watcher
- [ ] Verify required ElectrumX subscription behavior against the PEPEPOW fork
- [ ] Track chain-tip confirmations
- [ ] Add reconnect/resubscribe/reconciliation
- [ ] Deduplicate event creation
- [ ] Add resource and failure-boundary tests

Exit criteria:

- browser status reads do not cause equivalent ElectrumX polling
- restart/reconnect preserves correct payment state
- duplicate notifications do not create duplicate logical events

### Phase E — Webhook

Status: **PLANNED**

Repository: `pepepow-electrumx-service`

- [ ] Define event envelope/versioning
- [ ] Implement HMAC signatures
- [ ] Add delivery queue in SQLite
- [ ] Add timeout/retry/backoff
- [ ] Add idempotency/dedup rules
- [ ] Add delivery log
- [ ] Add SSRF/private-network protections
- [ ] Add webhook security tests

Exit criteria:

- safe retry after receiver failure
- stable event IDs
- no secret leakage in logs
- blocked unsafe destinations

### Phase F — Production deployment split

Status: **PLANNED**

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
