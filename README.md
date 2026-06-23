# PEPEW Light

Read-only PEPEPOW ElectrumX API Gateway and simple status/address lookup website.

## Security boundary

PEPEW Light is a public API gateway for read-only chain data. It must not receive,
store, log, derive, or handle wallet secrets.

- Mnemonic, seed phrase, private keys, address derivation, and transaction signing stay client-side.
- Phase 0 and Phase 1 are read-only.
- Future broadcast support may only accept signed raw transactions.
- ElectrumX should remain private on localhost; public traffic should go through Nginx and FastAPI.

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

## Phase 1 endpoints

- `GET /`
- `GET /status`
- `GET /api/health`
- `GET /api/status`

`/api/status` returns HTTP 200 even when ElectrumX is unavailable. Check `ok`, `electrumx.connected`, and `error` in the JSON body.

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
