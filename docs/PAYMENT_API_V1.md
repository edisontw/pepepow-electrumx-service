# Payment API v1

Status: **Phase D foundation**

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

When `PAYMENT_WATCHER_ENABLED=true`, the persistent ElectrumX watcher subscribes to tracked scripthashes and chain headers, reconciles transaction outputs into SQLite, and advances confirmations without browser polling. Both Payment API and watcher remain disabled by default until production access policy and end-to-end validation are complete.

## SQLite

Initial storage uses Python's standard-library `sqlite3`; no external database service is required.

Production path:

```text
/var/lib/pepew-light/payments.sqlite3
```

The systemd unit uses `StateDirectory=pepew-light` so the database remains writable while `ProtectHome=read-only` stays enabled.

Schema domains created now:

```text
payments
payment_transactions
events
chain_state
```

SQLite uses WAL mode, foreign keys, a bounded busy timeout, and short transactions.

## Security

- payment IDs are generated from cryptographically secure random bytes
- no mnemonic/private key/signing material is accepted
- creation is feature-gated and rate-limited at Nginx when deployed
- request body size should remain small
- public errors do not expose database paths or SQLite exception details
- authoritative state remains transaction-output based, not current address balance

Merchant authentication is defined in [PAYMENT_API_AUTH.md](PAYMENT_API_AUTH.md): payment creation requires a server-side Bearer API key, while status GET uses the high-entropy payment ID as a read-only capability. The create endpoint fails closed if the key is missing or shorter than 32 characters.

Durable state-change events are defined in [PAYMENT_EVENTS.md](PAYMENT_EVENTS.md) and are persisted atomically with payment version updates.

Browser clients should prefer the exact decimal-string amount fields rather than converting large JSON integer atom values through JavaScript `Number`.
