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
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8088 --reload
```

## Tests

```bash
cd backend
source .venv/bin/activate
pytest
curl http://127.0.0.1:8088/api/health
curl http://127.0.0.1:8088/
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
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
nano .env
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

## Phase 0 endpoints

- `GET /`
- `GET /api/health`

## Next phases

1. ElectrumX TCP JSON-RPC client and `/api/status`
2. PEPEW address validation, scriptPubKey, scripthash conversion, and address lookup
3. Payment monitor
4. Web wallet read-only integration
5. Signed raw transaction broadcast only after the read-only service is stable
