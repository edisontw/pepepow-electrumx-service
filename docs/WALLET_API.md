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
- ElectrumX remains private behind FastAPI.

## Endpoints

### Address summary

```http
GET /api/wallet/address/{address}
```

Returns balance and a compact recent history list.

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
```

Returns confirmed history and mempool entries when available.

Limits:

- `limit`: 1-500
- `offset`: 0 or greater

### UTXO lookup

```http
GET /api/wallet/utxo/{address}
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

Returns transaction data from ElectrumX.

### Broadcast

```http
POST /api/wallet/broadcast
Content-Type: application/json

{
  "raw_tx": "<SIGNED_RAW_TX>"
}
```

Rules:

- request body contains only signed raw transaction hex
- backend validates shape and size
- backend submits to ElectrumX / node
- backend returns txid or a stable public error code
- backend does not derive, build, or sign user transactions

## Error behavior

Expected public error codes:

| Status | Example code | Meaning |
| --- | --- | --- |
| 400 | `invalid_address` | Address validation failed |
| 400 | `invalid_txid` | Txid validation failed |
| 400 | `invalid_raw_tx` | Broadcast payload is not valid raw transaction hex |
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

## Smoke tests

```bash
curl -s https://light.pepepow.net/api/health
curl -s https://light.pepepow.net/api/status
curl -s https://light.pepepow.net/api/wallet/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
curl -s "https://light.pepepow.net/api/wallet/history/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb?limit=5&offset=0"
curl -s https://light.pepepow.net/api/wallet/utxo/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
```
