# PEPEW Light

PEPEW Light is the public FastAPI gateway and lightweight website for PEPEPOW / PEPEW ElectrumX access.

PEPEW Light provides:
- ElectrumX-backed API Gateway
- status dashboard
- address / tx lookup
- payment monitor
- read-only wallet API
- static deployment entrypoint for PEPEW Light Wallet under /wallet/

Production:
https://light.pepepow.net
Wallet:
https://light.pepepow.net/wallet/

## Current project phase

Phase 4 read-only wallet API is complete. Current focus is Phase 4.5 / Phase 5:

- polish and publicly deploy the non-custodial web wallet
- improve `/wallet/` loading speed and static asset paths
- improve safety warnings, balance display, history display, QR display, API status, and error messages
- keep broadcast limited to signed raw transactions only
- keep backend and wallet responsibilities clearly separated

## Security boundary

PEPEW Light is a public API gateway. It must not become a custodial wallet.

Rules:

1. Mnemonics, seed phrases, private keys, derivation, and signing stay client-side.
2. The server never receives, stores, logs, derives, or signs with wallet secrets.
3. Public API routes accept only addresses, txids, query parameters, payment-check parameters, and future signed raw transactions.
4. ElectrumX is private and reachable only from localhost/internal network.
5. Public traffic goes through HTTPS, Nginx, rate limiting, validation, and FastAPI.
6. ElectrumX calls must use timeouts.
7. Error responses must not expose internal paths, credentials, or upstream exception details.
8. Avoid long-term storage of IP/address associations.
9. Broadcast may only accept signed raw tx payloads.

## Architecture

```text
User
  |
  | HTTPS
  v
Nginx
  |-- /wallet/ static PEPEW Light Wallet
  |-- /api/*   reverse proxy to FastAPI
  |-- /        status/address/payment pages
  v
FastAPI PEPEW Light Gateway
  |
  | Electrum protocol, timeout, cache
  v
ElectrumX on localhost
  |
  v
PEPEPOWd
```

Repository boundaries:

| Repository | Responsibility |
| --- | --- |
| `pepepow-electrumx-service` | FastAPI gateway, cache, status pages, wallet API, deployment docs |
| `pepepow-light-wallet` | Static Vite/React non-custodial web wallet and client-side wallet logic |
| `electrumx-pepepow` | ElectrumX chain support only |

## PEPEW Light Wallet Integration

The PEPEW Light Wallet is maintained as a separate client-side repository:

- Wallet repo: https://github.com/edisontw/pepepow-light-wallet
- Production wallet: https://light.pepepow.net/wallet/

The wallet is built as a static Vite/React app and deployed under `/wallet/`.
It communicates with the PEPEW Light API through same-origin `/api/wallet/*` endpoints.

## Security Boundary

The backend never receives mnemonic phrases or private keys.
All wallet derivation and signing logic belongs to the client-side wallet.
The API only handles addresses, txids, query parameters, and future signed raw transactions.

## Public pages

```text
GET /
GET /address
GET /status
GET /pay
GET /tx
GET /wallet/     static wallet app, served by Nginx
```

## API endpoints

### Core API

```text
GET /api/health
GET /api/status
GET /api/address/{address}
GET /api/address/{address}/history
GET /api/tx/{txid}
GET /api/payment/check
```

### Wallet API

```text
GET  /api/wallet/address/{address}
GET  /api/wallet/history/{address}
GET  /api/wallet/utxo/{address}
GET  /api/wallet/tx/{txid}
POST /api/wallet/broadcast
```

`POST /api/wallet/broadcast` is only for signed raw transactions. Do not add mnemonic import, private-key upload, or server-side signing routes.

## Payment monitor

`GET /api/payment/check?address=Pxxx&amount=1` checks current ElectrumX address balance, history, and mempool for a read-only payment status.

Payment Monitor is not an invoice database.

- It does not create, reserve, store, or expire invoices server-side.
- It only checks whether a given address has received at least the requested amount.
- Each payment should use a unique receiving address.
- Reusing addresses can cause ambiguous payment detection.
- For production merchant use, a real invoice/payment database should be added later.

The payment monitor is address-level and read-only. Use a unique receiving address per payment request.

Amounts are address-level totals. Mempool values are unconfirmed. This endpoint is not a merchant invoice ledger.

Possible states:

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

Important response fields:

```text
requested_amount
requested_sats
confirmed_received_sats
mempool_received_sats
total_received_sats
status_explanation
explorer_address_url
```

Additional common response fields:

```text
address
amount_pepew
pepew_decimals
confirmed_balance
confirmed_balance_sats
mempool_balance
mempool_balance_sats
total_visible_balance
total_visible_balance_sats
confirmed_received
mempool_received
total_received
confirmations_required
expired
message
```

## ElectrumX methods used

Current / expected methods:

```text
server.version
features
headers.subscribe
scripthash.get_balance
scripthash.get_history
scripthash.get_mempool
transaction.get
transaction.broadcast
```

Future candidates:

```text
block.header
estimatefee
```

## Scripthash rule

Address lookup must follow the ElectrumX scripthash format:

```text
address decode -> scriptPubKey -> sha256 -> reverse bytes -> hex
```

This flow requires unit tests.

## Cache policy

Recommended TTLs for the single-core Oracle Cloud host:

| Data | TTL |
| --- | --- |
| status | 5-10 seconds |
| balance | 10-20 seconds |
| history | 20-60 seconds |
| transaction | 5-10 minutes |

Use cache and rate limiting to protect PEPEPOWd and ElectrumX.

## Local development

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8088 --reload
```

Python 3.11+ is preferred.

## Backend configuration

Main `.env` variables:

```text
APP_NAME=pepew-light
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8088
APP_PUBLIC_BASE_URL=https://light.pepepow.net
ELECTRUMX_HOST=127.0.0.1
ELECTRUMX_PORT=50001
ELECTRUMX_USE_SSL=false
ELECTRUMX_TIMEOUT=5.0
PEPEW_DECIMALS=8
PEPEW_ADDRESS_PREFIX=P
PEPEW_MIN_CONFIRMATIONS=3
PEPEW_EXPLORER_BASE_URL=https://explorer.pepepow.net
CACHE_STATUS_SECONDS=10
CACHE_BALANCE_SECONDS=15
CACHE_HISTORY_SECONDS=30
CACHE_TX_SECONDS=300
LOG_LEVEL=INFO
```

## Tests

```bash
cd backend
source .venv/bin/activate
python3 -m py_compile app/main.py
python3 -m pytest -q
curl -s http://127.0.0.1:8088/api/health
curl -s http://127.0.0.1:8088/api/status
```

Coverage priorities:

- address validation
- scripthash conversion
- ElectrumX timeout/error handling
- API endpoint response shape
- wallet API read-only behavior
- signed-raw-tx-only broadcast validation
- safe error responses

## Production deployment

### Backend

```bash
cd /home/ubuntu/pepepow-electrumx-service
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/systemd/pepew-light.service /etc/systemd/system/pepew-light.service
sudo systemctl daemon-reload
sudo systemctl enable pepew-light
sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager
```

### Nginx

```bash
sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/nginx/pepew-light.nginx.conf /etc/nginx/sites-available/pepew-light
sudo ln -sfn /etc/nginx/sites-available/pepew-light /etc/nginx/sites-enabled/pepew-light
sudo nginx -t
sudo systemctl reload nginx
```

Nginx should also serve the built wallet static files under `/wallet/`.

### Public verification

```bash
curl -I https://light.pepepow.net/
curl -I https://light.pepepow.net/wallet/
curl -s https://light.pepepow.net/api/health
curl -s https://light.pepepow.net/api/status
curl -s https://light.pepepow.net/api/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
curl -s "https://light.pepepow.net/api/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb/history?limit=5&offset=0"
curl -s https://light.pepepow.net/api/wallet/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
curl -s https://light.pepepow.net/api/wallet/utxo/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb
curl -s "https://light.pepepow.net/api/payment/check?address=PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb&amount=1"
```

Expected port shape:

```text
0.0.0.0:80       nginx
0.0.0.0:443      nginx
127.0.0.1:8088   uvicorn
127.0.0.1:50001  ElectrumX
```

## Minimum public-ready checklist

1. `/api/health` returns ok.
2. `/api/status` shows ElectrumX connection state.
3. Address API works.
4. Wallet API works.
5. Site can query an address.
6. Wallet shows balance and QR.
7. Status page works.
8. `/wallet/` opens.
9. Static assets load correctly.
10. Mnemonic import works client-side.
11. Wallet shows balance/history.
12. Non-custodial warning is visible.
13. README and docs reflect current architecture.
14. Tests cover key address/scripthash/API flows.

## Documentation

- [Security](docs/SECURITY.md)
- [Wallet API](docs/WALLET_API.md)
