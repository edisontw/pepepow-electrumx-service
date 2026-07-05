# Wallet API

The wallet API is the compatibility layer used by `pepepow-light-wallet`.

Base URL in production:

```text
https://light.pepepow.net
```

When the wallet is served from `/wallet/`, frontend calls should normally use same-origin `/api/wallet/*` paths.

## Security model

- Read endpoints are public blockchain data lookups.
- The API never handles wallet recovery material.
- Transaction signing belongs to the client wallet.
- Broadcast accepts only an already signed raw transaction.
- Broadcast accepts only the `raw_tx` field and rejects any signing material.
- ElectrumX remains private behind FastAPI.

## Endpoints

### Address summary

```http
GET /api/wallet/address/{address}
GET /api/wallet/address/{address}?verbose_history=1&detail_limit=10
```

Returns balance and a compact recent history list. `verbose_history=1` enriches recent history rows with wallet-oriented amount and direction fields. Keep `detail_limit` small for UI previews.

Example:

```bash
curl -s https://light.pepepow.net/api/wallet/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
```

Typical response shape:

```json
{
  "address": "P...",
  "balance": {
    "confirmed": 0,
    "unconfirmed": 0,
    "confirmed_pepew": "0",
    "unconfirmed_pepew": "0"
  },
  "history": [],
  "source": "electrumx",
  "read_only": true,
  "cache": {}
}
```

### Address history

```http
GET /api/wallet/history/{address}?limit=50&offset=0
GET /api/wallet/history/{address}?limit=50&offset=0&verbose=false
GET /api/wallet/history/{address}?limit=50&offset=0&verbose=1&detail_limit=10
```

Returns confirmed history and mempool entries when available.

Current wallet API behavior:

- `verbose` defaults to `true` for wallet clients.
- Use `verbose=false` only for a compact txid/height listing.
- Keep `detail_limit` small because verbose rows may resolve transaction details and previous outputs.

Limits:

- `limit`: 1-500
- `offset`: 0 or greater
- `detail_limit`: 0-25 when `verbose=1`

Compact rows with `verbose=false`:

```json
{
  "txid": "...",
  "height": 4691991,
  "is_mempool": false
}
```

Default verbose rows add fields calculated for the queried wallet address:

```json
{
  "txid": "...",
  "height": 4691991,
  "is_mempool": false,
  "direction": "received",
  "amount_atoms": 1000000000,
  "amount_pepew": "10",
  "address_delta_atoms": 1000000000,
  "address_delta_pepew": "10",
  "received_atoms": 1000000000,
  "spent_atoms": 0,
  "timestamp": 1780000000,
  "confirmations": 3
}
```

Direction values:

- `received`: net positive amount for the queried address.
- `sent`: net negative amount for the queried address.
- `self`: both input and output touch the address but net delta is zero.
- `unknown`: the transaction could not be fully resolved.

Verbose history may fetch transaction details and previous outputs to compute deltas. Use it for wallet UI, but avoid high `detail_limit` values.

### UTXO lookup

```http
GET /api/wallet/utxo/{address}
GET /api/wallet/utxo/{address}?fresh=1
```

Returns spendable outputs for client-side UTXO selection.

Typical response shape:

```json
{
  "address": "P...",
  "utxos": [],
  "utxo_count": 0,
  "total": 0,
  "source": "electrumx",
  "read_only": true,
  "cache": {}
}
```

### Transaction lookup

```http
GET /api/wallet/tx/{txid}
GET /api/wallet/tx/{txid}?raw=1
```

Returns transaction data from ElectrumX. Wallet list screens should prefer `/api/wallet/history/{address}` and should not bulk-call this endpoint unless verbose history is unavailable.

### Broadcast

```http
POST /api/wallet/broadcast
Content-Type: application/json

{
  "raw_tx": "<SIGNED_RAW_TX_HEX>"
}
```

Strict contract:

- request body must be a JSON object
- request body must contain only `raw_tx`
- `raw_tx` must be signed raw transaction hex
- `raw_tx` must have even hex length
- `raw_tx` must be between 20 and 200,000 hex characters
- `0x` prefix is normalized away before broadcast
- backend submits the signed raw transaction to ElectrumX / node
- backend returns txid or a stable public error code
- backend does not derive, build, or sign user transactions

Rejected payload examples:

```json
{
  "raw_tx": "01000000...",
  "mnemonic": "do-not-send"
}
```

```json
{
  "raw_tx": "01000000...",
  "note": "extra fields are not accepted"
}
```

These return:

```json
{
  "ok": false,
  "error": {
    "code": "invalid_broadcast_payload",
    "message": "Broadcast accepts only signed raw_tx hex and rejects signing material."
  }
}
```

## Error behavior

Expected public error codes:

| Status | Example code | Meaning |
| --- | --- | --- |
| 400 | `invalid_address` | Address validation failed |
| 400 | `invalid_txid` | Txid validation failed |
| 400 | `invalid_raw_tx` | Broadcast payload is not valid raw transaction hex |
| 400 | `invalid_broadcast_payload` | Broadcast payload contains unsupported fields |
| 400 | `raw_tx_too_short` | Broadcast raw tx is too short |
| 400 | `raw_tx_too_large` | Broadcast raw tx is too large |
| 404 | `tx_not_found` | Transaction not found |
| 429 | `rate_limited` | Nginx or app rate limit |
| 503 | `electrumx_error` | Upstream unavailable or rejected request |
| 503 | `broadcast_rejected` | Network rejected signed transaction |
| 500 | `internal_error` | Generic safe fallback |

Do not return internal exception details in public responses.

## Frontend integration notes

The wallet client should:

- default to same-origin `/api/wallet/*`
- use `VITE_PEPEW_LIGHT_API_BASE_URL` only for development/staging
- use request timeouts
- show friendly messages for invalid address, timeout, rate limit, and API unavailable states
- append `fresh=1` only when immediate refresh is needed
- use `/api/wallet/history/{address}?limit=50&offset=0&detail_limit=10` for wallet history pages
- avoid bulk `/api/wallet/tx/{txid}` calls when history rows already contain `address_delta_pepew`
- send only `{ "raw_tx": "<signed raw tx hex>" }` to broadcast

## Smoke tests

```bash
curl -s https://light.pepepow.net/api/health
curl -s https://light.pepepow.net/api/status
curl -s https://light.pepepow.net/api/wallet/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
curl -s "https://light.pepepow.net/api/wallet/history/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb?limit=5&offset=0"
curl -s "https://light.pepepow.net/api/wallet/history/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb?limit=5&offset=0&verbose=false"
curl -s https://light.pepepow.net/api/wallet/utxo/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
```

Broadcast safety check:

```bash
curl -s -X POST https://light.pepepow.net/api/wallet/broadcast \
  -H 'Content-Type: application/json' \
  -d '{"raw_tx":"0100000001abcdef0123","mnemonic":"do-not-send"}'
```

Expected result: `invalid_broadcast_payload`.
