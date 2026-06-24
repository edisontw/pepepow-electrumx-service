# PEPEW Light

Read-only PEPEPOW ElectrumX API Gateway and simple status/address/payment monitor website.

## Security boundary

PEPEW Light is a public API gateway for read-only chain data. It must not receive,
store, log, derive, or handle wallet secrets.

- Mnemonic, seed phrase, private keys, address derivation, and transaction signing stay client-side.
- Phase 0 and Phase 1 are read-only.
- Future broadcast support may only accept signed raw transactions.
- ElectrumX should remain private on localhost; public traffic should go through Nginx and FastAPI.
- Payment checking is address-level. Use a unique receiving address per payment request for accurate invoice detection.

## Local development

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8088 --reload
```

Python 3.11+ is preferred. Python 3.10 is acceptable for the initial MN3 deployment.

## Tests

```bash
cd backend
source .venv/bin/activate
python3 -m py_compile app/main.py
python3 -m pytest -q
curl http://127.0.0.1:8088/api/health
curl http://127.0.0.1:8088/api/status
curl http://127.0.0.1:8088/status
```

If the virtual environment is not ready yet, run tests with the system/user Python after installing requirements:

```bash
cd backend
python3 -m pytest -q
```

## MN3 deployment

Target host:

```bash
ssh -i ssh3.key ubuntu@158.178.140.199
```

On MN3:

```bash
cd /home/ubuntu
git clone https://github.com/edisontw/pepepow-electrumx-service.git
cd pepepow-electrumx-service/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
nano .env
```

If `python3 -m venv .venv` fails, install the venv package first:

```bash
sudo apt update
sudo apt install -y python3-venv
python3 -m venv .venv
```

Run manually:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8088
```

## systemd install

```bash
sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/systemd/pepew-light.service /etc/systemd/system/pepew-light.service
sudo systemctl daemon-reload
sudo systemctl enable pepew-light
sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager
journalctl -u pepew-light -n 100 --no-pager
```

## Nginx install

```bash
sudo cp /home/ubuntu/pepepow-electrumx-service/deploy/nginx/pepew-light.nginx.conf /etc/nginx/sites-available/pepew-light
sudo ln -sfn /etc/nginx/sites-available/pepew-light /etc/nginx/sites-enabled/pepew-light
sudo nginx -t
sudo systemctl reload nginx
```

## Phase 2 public deployment checklist

Confirmed public deployment state for `https://light.pepepow.net`:

- Nginx terminates HTTPS and reverse proxies to FastAPI.
- HTTP requests redirect to HTTPS.
- Public ingress is limited to ports `80/tcp` and `443/tcp`.
- Uvicorn listens on `127.0.0.1:8088` only.
- ElectrumX listens on `127.0.0.1:50001` only.
- ElectrumX RPC listens on `127.0.0.1:8000` only.
- The service remains read-only; it must not handle mnemonic, private key, seed phrase, signing, or wallet secret data.

Service restart:

```bash
cd ~/pepepow-electrumx-service/backend
source .venv/bin/activate
pytest
sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager
```

Nginx and certificate checks:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl list-timers | grep certbot || true
sudo certbot renew --dry-run
```

Public verification:

```bash
curl -I http://light.pepepow.net/
curl -I https://light.pepepow.net/
curl -s https://light.pepepow.net/api/health
curl -s https://light.pepepow.net/api/status
curl -I https://light.pepepow.net/address
curl -s "https://light.pepepow.net/api/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb"
curl -s "https://light.pepepow.net/api/address/PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb/history?limit=5&offset=0"
curl -s https://light.pepepow.net/api/address/invalid
curl -s "https://light.pepepow.net/api/payment/check?address=PRfbEeHAKKbz6Voz85WJudrJwTA3ZbHunb&amount=1"
curl -s "https://light.pepepow.net/api/payment/check?address=invalid&amount=1"
curl -s https://light.pepepow.net/pay
ss -lntp | grep -E ':80|:443|:8088|:50001'
```

Expected port shape:

```text
0.0.0.0:80       nginx
0.0.0.0:443      nginx
127.0.0.1:8088   uvicorn
127.0.0.1:50001  electrumx_server
```

## Public endpoints

- `GET /`
- `GET /status`
- `GET /api/health`
- `GET /api/status`
- `GET /api/payment/check?address={address}&amount={amount}`

`/api/status` returns HTTP 200 even when ElectrumX is unavailable. Check `ok`, `electrumx.connected`, and `error` in the JSON body.

## Phase 3 payment monitor

`GET /api/payment/check?address=Pxxx&amount=1` checks the current ElectrumX address balance, history, and mempool for a read-only payment status.

Optional query parameters:

- `confirmations`: defaults to `PEPEW_MIN_CONFIRMATIONS`.
- `expires_at`: ISO8601 timestamp.
- `expires_in`: expiry seconds from the current request time.

Example response:

```json
{
  "ok": true,
  "address": "Pxxx",
  "requested_amount": "1000",
  "requested_sats": 100000000000,
  "amount_pepew": "1000",
  "pepew_decimals": 8,
  "confirmed_received": "0",
  "confirmed_received_sats": 0,
  "mempool_received": "0",
  "mempool_received_sats": 0,
  "total_received": "0",
  "total_received_sats": 0,
  "status": "waiting",
  "confirmations_required": 3,
  "explorer_address_url": "https://explorer.pepepow.net/address/Pxxx",
  "expired": false,
  "status_explanation": "No matching payment has been seen yet."
}
```

Response fields:

- `address`: validated PEPEW address being checked.
- `requested_amount` and `amount_pepew`: normalized requested PEPEW amount.
- `requested_sats`: requested amount in sats / smallest units.
- `pepew_decimals`: decimal precision used for PEPEW amount conversion.
- `confirmed_received` and `confirmed_received_sats`: address-level confirmed total.
- `mempool_received` and `mempool_received_sats`: address-level unconfirmed mempool total.
- `total_received` and `total_received_sats`: confirmed plus mempool/unconfirmed total seen for the address.
- `status`: one of `waiting`, `seen_in_mempool`, `partial`, `paid_unconfirmed`, `paid_confirmed`, or `overpaid`.
- `confirmations_required`: threshold from `PEPEW_MIN_CONFIRMATIONS` unless overridden by query parameter.
- `explorer_address_url`: public explorer URL for the checked address.
- `expires_in` and `expired`: display monitor expiry metadata when an expiry is supplied.
- `status_explanation` and `message`: short user-facing status text.

Amounts are address-level totals. Mempool values are unconfirmed. Confirmed payment status uses the configured `PEPEW_MIN_CONFIRMATIONS` threshold for deciding whether the monitor should report `paid_confirmed`.

This endpoint is not a merchant invoice ledger.

Payment checking is address-level. Use a unique receiving address per payment request for accurate invoice detection.

The payment monitor remains read-only. It does not add mnemonic, private key, signing, server-side wallet, custody, or broadcast logic.

### Not an invoice database

Payment Monitor is not an invoice database.

- It does not create, reserve, store, or expire invoices server-side.
- It only checks whether a given address has received at least the requested amount.
- Each payment should use a unique receiving address.
- Reusing addresses can cause ambiguous payment detection.
- For production merchant use, a real invoice/payment database should be added later.

## Phase 1 verification on MN3

```bash
cd ~/pepepow-electrumx-service

git pull
cd backend
source .venv/bin/activate
python3 -m py_compile app/main.py
python3 -m pytest -q

sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager -l

curl -s http://127.0.0.1:8088/api/health | jq
curl -s http://127.0.0.1:8088/api/status | jq
curl -I http://127.0.0.1:8088/status
sudo journalctl -u pepew-light -n 80 --no-pager
```

Expected successful `/api/status` shape:

```json
{
  "ok": true,
  "app": "pepew-light",
  "electrumx": {
    "connected": true,
    "host": "127.0.0.1",
    "port": 50001,
    "server_version": "...",
    "protocol": "...",
    "height": 4620000,
    "tip_hash": null,
    "response_time_ms": 23.0
  },
  "cache": {
    "enabled": true,
    "ttl_seconds": 10,
    "hit": false
  }
}
```

Expected safe unavailable shape:

```json
{
  "ok": false,
  "app": "pepew-light",
  "electrumx": {
    "connected": false,
    "host": "127.0.0.1",
    "port": 50001
  },
  "error": "electrumx_unavailable"
}
```

## Next phases

1. PEPEW address validation, scriptPubKey, scripthash conversion, and address lookup
2. Payment monitor
3. Web wallet read-only integration
4. Signed raw transaction broadcast only after the read-only service is stable
