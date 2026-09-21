# Transaction-Level Payment State

This document defines the Phase B payment-domain invariants used before SQLite/event persistence is introduced.

The legacy `GET /api/payment/check` endpoint remains an address-level, stateless monitor. The rules below are for the authoritative Payment Platform path.

## Core identity

A received payment unit is a transaction output identified by:

```text
(txid, vout)
```

The same output may be observed repeatedly through mempool notifications, history reconciliation, reconnects, or later block confirmation. It is counted once.

## Payment observation

An observation records:

```text
txid
vout
value_sats
height
first_seen_at
```

`height <= 0` is unconfirmed. A confirmed output has:

```text
confirmations = tip_height - height + 1
```

when `tip_height >= height`.

## Creation boundary

A payment records its creation height/time.

An already-confirmed output with:

```text
height <= payment.created_height
```

is pre-existing chain state and must not satisfy the payment.

For unconfirmed observations, `first_seen_at` is required once a payment creation timestamp is used. This deliberately fails closed rather than accepting an old mempool transaction as a new payment.

## Expiry

Expiry is based on when a matching output was first observed, not on the current address balance.

- matching outputs first seen after `expires_at` do not satisfy the payment
- an incomplete payment becomes `expired` after the deadline
- a payment completed before the deadline does not revert to unpaid merely because the clock later passes the deadline
- persistence must retain `first_seen_at` so expiry can be reconstructed safely after restart

## Confirmation policy

`confirmations_required=N` is evaluated per matched output using transaction height and current tip height.

For multiple partial transactions, only value from outputs meeting the configured confirmation threshold contributes to `policy_confirmed_sats`.

A payment can therefore have:

```text
received_sats >= requested_sats
policy_confirmed_sats < requested_sats
```

and remain `paid_unconfirmed` until enough chain confirmations accumulate.

When the configured policy is `0`, observed mempool value is accepted by policy.

## Balance independence

Current address balance is not part of authoritative payment evaluation.

After a merchant receives a payment, later spending or consolidation cannot make that historical payment disappear.

This is the primary semantic difference from the legacy stateless address-level monitor.

## State derivation

The Phase B pure state evaluator derives:

```text
waiting
partial
paid_unconfirmed
paid_confirmed
overpaid
expired
```

from matched transaction outputs and the confirmation/expiry policy.

`seen_in_mempool` remains part of the broader platform status vocabulary, but a positive output to the payment address is already a `partial` payment. Later event infrastructure may use `seen_in_mempool` for an observed relevant transaction before a positive matching output is accepted.

## Implementation

Pure payment-domain helpers live in:

```text
backend/app/services/payment_state.py
```

They intentionally do not open ElectrumX connections and do not read current address balances. This keeps authoritative payment logic unit-testable without production services.

SQLite persistence, subscription/reconciliation, and webhook/event delivery are later phases and must build on these invariants rather than reimplementing them.
