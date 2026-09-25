# ElectrumX Payment Watcher

Status: **Phase D implementation + Phase H1 operational health**

This document records the PEPEPOW ElectrumX subscription behavior verified from
`edisontw/electrumx-pepepow/main` and the watcher design built against it.

## Verified fork behavior

The current fork requires:

```text
server.version
```

to be the first RPC on a new Electrum session.

### Header subscription

Request:

```text
blockchain.headers.subscribe
```

Initial result and later notifications use the cached header-subscription object:

```json
{
  "hex": "<raw header hex>",
  "height": 5012797
}
```

Notification form:

```json
{
  "method": "blockchain.headers.subscribe",
  "params": [
    {
      "hex": "...",
      "height": 5012798
    }
  ]
}
```

### Scripthash subscription

Request:

```text
blockchain.scripthash.subscribe <scripthash>
```

The initial result is the address-history status hash or `null`.

Notification form:

```json
{
  "method": "blockchain.scripthash.subscribe",
  "params": [
    "<scripthash>",
    "<new status hash or null>"
  ]
}
```

The status hash represents both confirmed history and mempool history.

### History semantics

In this fork:

```text
blockchain.scripthash.get_history
```

returns confirmed history **plus unconfirmed mempool history**.

Mempool heights follow Electrum semantics:

```text
0   = unconfirmed without unconfirmed parents
-1  = unconfirmed with unconfirmed parents
```

Therefore the payment watcher does not need a second
`blockchain.scripthash.get_mempool` call during reconciliation.

### Verbose transaction lookup

The fork supports:

```text
blockchain.transaction.get <txid> true
```

and passes `verbose=true` to the PEPEPOWd `getrawtransaction` RPC.

This is used to match exact transaction outputs to the payment address.

## Persistent client

Implementation:

```text
backend/app/electrumx/subscription_client.py
```

Properties:

- one persistent TCP connection
- `server.version` first
- background reader owns the socket read side
- request-ID correlation supports multiple in-flight RPCs
- notification dispatch is separate from responses
- bounded request timeouts
- connection loss fails pending requests
- reconnect is handled by the payment watcher

The existing short-lived `ElectrumXClient` remains unchanged for normal API
requests. Subscription logic is intentionally separate.

## Payment watcher

Implementation:

```text
backend/app/services/payment_watcher.py
```

Feature gate:

```text
PAYMENT_WATCHER_ENABLED=false
```

The watcher is disabled by default.

Connected flow:

```text
connect
  -> server.version
  -> headers.subscribe
  -> persist chain tip
  -> load watched scripthashes from SQLite
  -> scripthash.subscribe for each
  -> reconcile each subscription once
  -> wait for notifications
```

On a scripthash status change:

```text
get_history
  -> confirmed + mempool txids
  -> verbose transaction.get for relevant txids
  -> exact output matching
  -> upsert (payment_id, txid, vout)
  -> remove observations for txids no longer present
  -> recompute persisted payment state
```

On a new header:

```text
persist tip height
  -> recompute payments with transaction observations
  -> true N-confirmation state advances without browser polling
```

## Creation baseline

A payment creation snapshot uses one short-lived ElectrumX session:

```text
server.version
headers.subscribe
scripthash.get_history
```

The current history txids are persisted as the payment's baseline.

This is required for correctness. A mempool transaction that existed before the
invoice/payment record was created must not later be treated as a new payment
merely because it confirms after creation.

Confirmed pre-existing outputs are also protected by `created_height`, but the
baseline additionally handles pre-existing mempool transactions.

## Reconnect and reconciliation

The watcher retries failed connections using bounded exponential backoff.

After every reconnect it:

- resubscribes to the current watched scripthashes
- performs a full reconciliation
- reconstructs state from persisted observations + current ElectrumX history

No notification is assumed to have been delivered exactly once.

If the notification queue overflows, the watcher marks itself for a full
reconciliation rather than trusting the partial notification stream.

## Resource bounds

Initial defaults:

```text
PAYMENT_WATCHER_SUBSCRIPTION_REFRESH_SECONDS=5
PAYMENT_WATCHER_RECONNECT_MIN_SECONDS=1
PAYMENT_WATCHER_RECONNECT_MAX_SECONDS=30
PAYMENT_WATCHER_MAX_SUBSCRIPTIONS=2000
PAYMENT_WATCHER_STALE_SECONDS=60
```

The subscription set is capped. If the desired capped set changes in a way that
would leave stale subscriptions on a protocol 1.4 connection, the watcher
reconnects rather than allowing unbounded subscription growth.

The watcher fetches verbose transactions only after a subscribed scripthash
status changes or during reconnect reconciliation. It does not poll every
payment on a fixed short interval.


## Operational health (Phase H1)

The watcher now maintains a small process-local health snapshot. It is exposed
through the existing `GET /api/status` response under `payment_watcher` and
does not require a metrics daemon, database table, Prometheus, Redis, or an
additional polling loop.

The snapshot reports:

- feature-gate state plus `running` / `connected`
- derived state: `disabled`, `stopped`, `starting`, `healthy`,
  `recovering`, `disconnected`, or `stale`
- last successful connection, reconciliation, header, failure, disconnect, and
  general activity timestamps
- watcher-observed chain-tip height
- current subscription count
- connection attempts, reconnects, total failures, and consecutive failures
- a bounded safe error code

It does **not** expose watched scripthashes, addresses, payment IDs, transaction
IDs, SQLite paths, credentials, webhook secrets, or merchant metadata.

`PAYMENT_WATCHER_STALE_SECONDS` defaults to 60 seconds. Successful connection,
header processing, and reconciliation update the activity timestamp. Because the
existing subscription refresh cycle already performs reconciliation work, stale
detection adds no new background load.

`/api/status` appends this process-local snapshot after the normal ElectrumX
status cache is evaluated. Watcher disconnect/stale/recovery changes therefore
remain visible immediately even when the upstream status portion is a cache hit.

Reconnect logging is intentionally bounded: the first three consecutive failures
are warnings, then every tenth consecutive failure is a warning; intermediate
repeats are debug-level. After a connection has completed its initial header and
subscription reconciliation, recovery is logged once and the consecutive-failure
counter resets.

`GET /api/health` remains a shallow service-liveness endpoint and is not made
dependent on watcher or ElectrumX health.

## Deployment state

Both of these remain disabled by default:

```text
PAYMENT_API_ENABLED=false
PAYMENT_WATCHER_ENABLED=false
```

Do not enable the public payment creation path until merchant access/auth policy
and production end-to-end validation are complete.
