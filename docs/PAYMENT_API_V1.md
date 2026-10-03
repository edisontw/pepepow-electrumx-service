# Payment API v1

Status: **production contract; Phase K merchant-scoped implementation complete in main, production acceptance pending**

This API is the persisted transaction-level payment path. It is separate from the legacy stateless `GET /api/payment/check` address monitor.

The API is feature-gated by:

```text
PAYMENT_API_ENABLED
```

and remains disabled by default until deployment configuration and end-to-end testing are completed.

## Create payment

Payment creation is a merchant server-to-server action and requires a valid merchant Bearer credential:

```http
POST /api/v1/payments
Authorization: Bearer <merchant-bearer-credential>
Idempotency-Key: <merchant retry key>   # optional but recommended
Content-Type: application/json
```

Example request:

```json
{
  "address": "PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb",
  "amount": "12.34",
  "confirmations": 3,
  "expires_in": 900,
  "label": "Demo merchant",
  "message": "Order 1234",
  "merchant_reference": "ORDER-1234"
}
```

The server validates the PEPEW address and exact decimal amount, then uses one short-lived ElectrumX session to snapshot both the current chain height and the address's existing confirmed/mempool history before persisting the payment in SQLite.

The existing history txids are stored as the payment creation baseline. This prevents a transaction already present in mempool before payment creation from later becoming a false new payment when it confirms.

A payment is not created if the chain-tip/history snapshot cannot be established. This fail-closed behavior prevents an ambiguous creation boundary.

### Receiving-address payment windows

Payment creation also enforces non-overlapping time windows per receiving address across the entire Payment Platform, including different merchants. If an existing payment for the same address has `expires_at >= new.created_at`, the create request returns HTTP `409 payment_address_in_use`.

This rule is intentionally based on the persisted payment time window rather than current status. A payment that confirms early still reserves its address through its original `expires_at` boundary, because an output first observed exactly at that boundary may still belong to the earlier payment and later reorg/reconciliation must remain unambiguous. The address may be reused once a later create occurs after that boundary.

The final check and insert run inside the same SQLite `BEGIN IMMEDIATE` transaction, so concurrent creates for the same address cannot both succeed. Different receiving addresses may be created concurrently. This adds no external locking service or database dependency.

### Idempotent creation retries

Merchant integrations should send an `Idempotency-Key` on payment creation. The key is persisted in SQLite and scoped to the authenticated merchant namespace.

Accepted keys are 1-128 characters using ASCII letters, digits, `.`, `_`, `:`, or `-`.

Behavior:

- first use creates the payment normally and durably binds the key to that payment plus a hash of the normalized request
- repeating the same key with the same request returns the original payment and does not create a second `payment.created` event
- a known idempotency mapping is checked before the ElectrumX creation snapshot, so ordinary HTTP retries do not add upstream work
- repeating the same key with different payment parameters returns HTTP `409` with `payment_idempotency_conflict`
- the mapping survives process restart because it is stored in the same SQLite database
- omitting `Idempotency-Key` preserves the original create-new-payment behavior
- a valid same-key/same-request replay still returns the original payment even while that address window is reserved

The request hash follows the merchant-supplied create parameters. Omitted defaulted fields remain distinguishable from explicitly supplied fields, so a retry stays stable even if server defaults are changed later.

### Merchant reference

`merchant_reference` is an optional merchant-owned external order/payment identifier.

Current v1 semantics:

- it is unique within the authenticated merchant namespace; a different merchant may reuse the same reference independently
- accepted values are 1-128 characters using ASCII letters, digits, `.`, `_`, `:`, `/`, or `-`
- it is immutable after payment creation; v1 has no API that rewrites it
- duplicate reuse returns HTTP `409 payment_merchant_reference_conflict`, even if the new request otherwise matches the old payment
- it is **not** the HTTP retry idempotency mechanism; merchants should still send `Idempotency-Key` when retrying a create request
- when both are supplied, a valid same-key/same-request idempotent replay wins before merchant-reference conflict detection
- it is included in authenticated create/list responses and new Payment Event Envelope v1 payloads
- it is deliberately omitted from the public capability response `GET /api/v1/payments/{payment_id}`

This separation keeps two concepts distinct:

```text
Idempotency-Key      = retry identity for one create request
merchant_reference   = durable merchant business/order identity
payment_id           = PEPEW Payment Platform capability identity
```

Existing payments created before this field was introduced remain valid with `merchant_reference = null`.

## Merchant payment recovery

The authenticated merchant API can list persisted payments without turning public capability URLs into an anonymous enumeration endpoint:

```http
GET /api/v1/payments
Authorization: Bearer <merchant-bearer-credential>
```

This route uses the same merchant Bearer identity as payment creation. Results are restricted to the authenticated merchant and are intended for merchant backend recovery/operations, not customer browsers.

Optional query parameters:

```text
status=waiting|partial|paid_unconfirmed|paid_confirmed|overpaid|expired
merchant_reference=ORDER-1234
limit=1..100                  # default 50
before_created_at=<unix-seconds>
before_payment_id=<payment-id>
```

The two `before_*` fields form one stable descending cursor and must be supplied together. Results are ordered by `created_at DESC, payment_id DESC`. The response includes `has_more` plus the next cursor values when another page exists.

Properties:

- listing is Bearer-authenticated; anonymous `GET /api/v1/payments` returns 401
- listing is SQLite-only and does not perform ElectrumX requests or refresh every row
- no expensive total-count query is required
- results are bounded to at most 100 records per request
- each merchant result includes its stored `idempotency_key` when one was supplied at creation, which helps recover a timed-out create request
- exact `merchant_reference` filtering uses the merchant-scoped unique index and returns zero or one matching payment for the authenticated merchant
- the public capability endpoint `GET /api/v1/payments/{payment_id}` does not expose the idempotency key and remains unauthenticated
- merchant ownership is explicit in SQLite; production database-backed scoped credentials remain gated by `PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED` until Phase K5 acceptance

Example response shape:

```json
{
  "ok": true,
  "payments": [
    {
      "payment_id": "pay_...",
      "status": "paid_confirmed",
      "amount": "1.25",
      "merchant_reference": "ORDER-1234",
      "idempotency_key": "order-123-attempt-1"
    }
  ],
  "has_more": true,
  "next_before_created_at": 1790240000,
  "next_before_payment_id": "pay_..."
}
```

## Read payment status

```http
GET /api/v1/payments/{payment_id}
```

This endpoint reads persisted SQLite state. It does **not** perform an ElectrumX request per browser refresh.

The status GET is a read-only capability URL and does not require the merchant Bearer key. The high-entropy `payment_id` may be shared with PepewPay/customer browsers. Do not put secrets or sensitive records in payment `label` or `message`.

Current response fields include:

- `payment_id`
- `address`
- `amount` / `amount_sats`
- `confirmations_required`
- `created_at` / `created_height`
- `expires_at`
- `status`
- `version`
- exact decimal strings: `received`, `confirmed`, `policy_confirmed`, `overpaid_by`
- compatibility integer fields: `received_sats`, `confirmed_sats`, `policy_confirmed_sats`, `overpaid_by_sats`
- optional `label` / `message`

When `PAYMENT_WATCHER_ENABLED=true`, the persistent ElectrumX watcher subscribes to tracked scripthashes and chain headers, reconciles transaction outputs into SQLite, and advances confirmations without browser polling. Repository defaults remain disabled; production VM-B enables the authoritative Payment API and watcher explicitly, while VM-A keeps them disabled.

## SQLite

Initial storage uses Python's standard-library `sqlite3`; no external database service is required.

Authoritative production path on VM-B:

```text
/var/lib/pepew-pay/payments.sqlite3
```

VM-B is the sole Payment Platform writer after the completed Phase F cutover. VM-A keeps Payment API/watcher/webhook feature gates disabled.

Schema domains include:

```text
merchants
merchant_credentials
payments
payment_idempotency_keys
payment_transactions
events
webhook_endpoints
webhook_deliveries
chain_state
```

SQLite uses WAL mode, foreign keys, a bounded busy timeout, and short transactions.

## Security

- payment IDs are generated from cryptographically secure random bytes
- no mnemonic/private key/signing material is accepted
- creation is feature-gated, authenticated, rate-limited at Nginx, supports merchant-scoped durable idempotent retries, enforces merchant-scoped unique references when supplied, and prevents overlapping payment windows for one receiving address across merchants
- request body size should remain small
- public errors do not expose database paths or SQLite exception details
- authoritative state remains transaction-output based, not current address balance

Merchant authentication is defined in [PAYMENT_API_AUTH.md](PAYMENT_API_AUTH.md): authenticated routes resolve a Bearer credential to a merchant context; the legacy environment key maps to `mrc_legacy_v1`, while database-backed scoped credentials remain behind the Phase K feature gate until production acceptance. Status GET continues to use the high-entropy payment ID as a read-only capability.

Durable state-change events are defined in [PAYMENT_EVENTS.md](PAYMENT_EVENTS.md) and are persisted atomically with payment version updates.

Browser clients should prefer the exact decimal-string amount fields rather than converting large JSON integer atom values through JavaScript `Number`.

## Phase K merchant scoping

Repository main now scopes payment creation, Idempotency-Key replay/conflict checks, merchant_reference uniqueness/recovery, and authenticated payment listing by merchant_id.

The same Idempotency-Key and merchant_reference may therefore coexist for two independent merchants. Public GET /api/v1/payments/{payment_id} remains unchanged and does not expose merchant_id, credential_id, merchant_reference, or idempotency metadata.

Production rollout is tracked in [PHASE_K_PRODUCTION_ACCEPTANCE.md](PHASE_K_PRODUCTION_ACCEPTANCE.md). Until K5 passes, PAYMENT_SCOPED_MERCHANT_AUTH_ENABLED remains false and the existing PAYMENT_CREATE_API_KEY continues to resolve to the reserved legacy merchant.
