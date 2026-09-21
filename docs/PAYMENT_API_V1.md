# Payment API v1

Status: **Phase D foundation**

This API is the persisted transaction-level payment path. It is separate from the legacy stateless `GET /api/payment/check` address monitor.

The API is feature-gated by:

```text
PAYMENT_API_ENABLED
```

and remains disabled by default until deployment configuration and end-to-end testing are completed.

## Create payment

```http
POST /api/v1/payments
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

The server validates the PEPEW address and exact decimal amount, snapshots the current chain height, and persists the payment in SQLite.

A payment is not created if the current ElectrumX chain tip cannot be established. This fail-closed behavior prevents an ambiguous creation-height boundary.

## Read payment status

```http
GET /api/v1/payments/{payment_id}
```

This endpoint reads persisted SQLite state. It does **not** perform an ElectrumX request per browser refresh.

Current response fields include:

- `payment_id`
- `address`
- `amount` / `amount_sats`
- `confirmations_required`
- `created_at` / `created_height`
- `expires_at`
- `status`
- `version`
- `received_sats`
- `confirmed_sats`
- `policy_confirmed_sats`
- `overpaid_by_sats`
- optional `label` / `message`

Until the ElectrumX watcher is connected, newly created payments remain `waiting` (or become `expired`) because no transaction observations are being ingested.

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

Merchant authentication is not defined in this first Phase D slice. Keep the feature disabled on public production until the intended access policy is selected.
