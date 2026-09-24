# Payment Event Envelope v1

Status: **Phase D complete**

Payment state changes are persisted in SQLite in the same transaction that advances the payment record.

## Event identity

Event IDs are deterministic:

```text
evt_<sha256("pepew-event-v1:<payment_id>:<payment_version>:<event_type>")>
```

Logical deduplication is anchored by:

```text
(payment_id, event_type, payment_version)
```

The database also enforces this as a unique constraint.

Repeated ElectrumX notifications, reconnect reconciliation, or repeated browser reads therefore do not create duplicate logical events if payment state did not change.

## Event types

Initial creation:

```text
payment.created
```

State-derived events:

```text
payment.waiting
payment.partial
payment.paid_unconfirmed
payment.paid_confirmed
payment.overpaid
payment.expired
```

A reorg or dropped mempool transaction may move a payment backward and therefore produce a later-version event such as:

```text
payment.paid_confirmed
  -> payment.paid_unconfirmed
```

or:

```text
payment.paid_unconfirmed
  -> payment.waiting
```

Old events are never rewritten or deleted merely because the chain state changes.

## Envelope

Stored payload shape:

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "event_type": "payment.paid_confirmed",
  "payment_id": "pay_...",
  "payment_version": 3,
  "created_at": 1760000000,
  "data": {
    "status": "paid_confirmed",
    "amount_sats": 100000000,
    "received_sats": 100000000,
    "confirmed_sats": 100000000,
    "policy_confirmed_sats": 100000000,
    "confirmations_required": 3,
    "expires_at": 1760000900,
    "merchant_reference": "ORDER-1234"
  }
}
```

The serialized JSON is canonicalized with sorted keys and compact separators before storage.

For payments created with the optional merchant-owned `merchant_reference`, new events include that value in `data.merchant_reference`. Payments without one carry `null`.

This is an additive v1 field. Existing persisted event bodies are immutable and are not rewritten during schema migration, so historical events created before merchant references existed may not contain the field at all. Webhook consumers should therefore treat it as optional when decoding Payment Event Envelope v1.

## Atomicity

When authoritative payment state changes, one SQLite transaction:

1. evaluates the persisted transaction observations
2. updates payment state and increments `payment.version`
3. inserts the matching event for that new version

If the payment state does not change, the version does not increment and no event is inserted.

## Incremental consumers

`PaymentStore.list_events()` exposes SQLite row order as an internal monotonic `sequence` value.

A webhook worker can later consume:

```text
events WHERE sequence > last_seen_sequence
```

without requiring Redis, Kafka, RabbitMQ, or another queue.

The stable `event_id` remains the merchant-facing idempotency key. The internal `sequence` is only a local SQLite consumption cursor.

## Delivery semantics

Phase E now consumes these persisted events through the durable webhook queue.

For each event, currently enabled matching webhook endpoints receive at most one logical delivery row identified by `(event_id, endpoint_id)`. Delivery is at least once: HTTP retries reuse the original event ID, delivery ID, and exact persisted payload rather than generating a new payment event.

See [WEBHOOKS.md](WEBHOOKS.md) for endpoint management, HMAC signing, retry policy, delivery logs, and SSRF protections.
