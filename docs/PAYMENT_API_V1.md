# Payment API v1

Status: **production; Phase G merchant-integration hardening in progress**

This API is the persisted transaction-level payment path. It is separate from the legacy stateless `GET /api/payment/check` address monitor.

The API is feature-gated by:

```text
PAYMENT_API_ENABLED
```

and remains disabled by default until deployment configuration and end-to-end testing are completed.

## Create payment

Payment creation is a merchant server-to-server action and requires the configured Bearer key:

```http
POST /api/v1/payments
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
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
  "message": "Order 1234"
}
```

The server validates the PEPEW address and exact decimal amount, then uses one short-lived ElectrumX session to snapshot both the current chain height and the address's existing confirmed/mempool history before persisting the payment in SQLite.

The existing history txids are stored as the payment creation baseline. This prevents a transaction already present in mempool before payment creation from later becoming a false new payment when it confirms.

A payment is not created if the chain-tip/history snapshot cannot be established. This fail-closed behavior prevents an ambiguous creation boundary.

### Idempotent creation retries

Merchant integrations should send an `Idempotency-Key` on payment creation. The key is scoped to the current single-merchant API credential boundary and is persisted in SQLite.

Accepted keys are 1-128 characters using ASCII letters, digits, `.`, `_`, `:`, or `-`.

Behavior:

- first use creates the payment normally and durably binds the key to that payment plus a hash of the normalized request
- repeating the same key with the same request returns the original payment and does not create a second `payment.created` event
- a known idempotency mapping is checked before the ElectrumX creation snapshot, so ordinary HTTP retries do not add upstream work
- repeating the same key with different payment parameters returns HTTP `409` with `payment_idempotency_conflict`
- the mapping survives process restart because it is stored in the same SQLite database
- omitting `Idempotency-Key` preserves the original create-new-payment behavior

The request hash follows the merchant-supplied create parameters. Omitted defaulted fields remain distinguishable from explicitly supplied fields, so a retry stays stable even if server defaults are changed later.

## Merchant payment recovery

The authenticated merchant API can list persisted payments without turning public capability URLs into an anonymous enumeration endpoint:

```http
GET /api/v1/payments
Authorization: Bearer <PAYMENT_CREATE_API_KEY>
```

This route uses the same current single-merchant server-side Bearer boundary as payment creation. It is intended for merchant backend recovery and operations, not customer browsers.

Optional query parameters:

```text
status=waiting|partial|paid_unconfirmed|paid_confirmed|overpaid|expired
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
- the public capability endpoint `GET /api/v1/payments/{payment_id}` does not expose the idempotency key and remains unauthenticated
- this is still a single-merchant credential model; scoped multi-merchant ownership is intentionally deferred until real requirements justify it

Example response shape:

```json
{
  "ok": true,
  "payments": [
    {
      "payment_id": "pay_...",
      "status": "paid_confirmed",
      "amount": "1.25",
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

Schema domains created now:

```text
payments
payment_idempotency_keys
payment_transactions
events
chain_state
```

SQLite uses WAL mode, foreign keys, a bounded busy timeout, and short transactions.

## Security

- payment IDs are generated from cryptographically secure random bytes
- no mnemonic/private key/signing material is accepted
- creation is feature-gated, authenticated, rate-limited at Nginx, and supports durable idempotent retries
- request body size should remain small
- public errors do not expose database paths or SQLite exception details
- authoritative state remains transaction-output based, not current address balance

Merchant authentication is defined in [PAYMENT_API_AUTH.md](PAYMENT_API_AUTH.md): payment creation and merchant payment listing require the server-side Bearer API key, while status GET uses the high-entropy payment ID as a read-only capability. Authenticated merchant operations fail closed if the configured key is missing or shorter than 32 characters.

Durable state-change events are defined in [PAYMENT_EVENTS.md](PAYMENT_EVENTS.md) and are persisted atomically with payment version updates.

Browser clients should prefer the exact decimal-string amount fields rather than converting large JSON integer atom values through JavaScript `Number`.
