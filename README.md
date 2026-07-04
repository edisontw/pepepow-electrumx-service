# PEPEW Light

PEPEW Light is the public FastAPI gateway and lightweight website for PEPEPOW / PEPEW ElectrumX access.

PEPEW Light provides:
- ElectrumX-backed API gateway
- status dashboard
- address lookup
- transaction lookup
- simplified payment monitor
- read-only wallet API
- static entrypoint for PEPEW Light Wallet under `/wallet/`

Production:
- Gateway: [light.pepepow.net](https://light.pepepow.net)
- Wallet: [light.pepepow.net/wallet/](https://light.pepepow.net/wallet/)

---

## 1. Project Structure & Architecture

The deployment architecture is structured as follows:

```text
User → HTTPS (Port 443) → Nginx (Reverse Proxy & Static Assets)
                             |
                             |-- /wallet/ (Static HTML/JS SPA)
                             |-- /static/ (FastAPI Gateway Static Assets)
                             |-- /        (Dashboard, checking pages)
                             v
                        FastAPI Gateway (Port 8088)
                             |
                             | (Localhost RPC Connection)
                             v
                        ElectrumX Node (Port 50001)
                             |
                             v
                         PEPEPOWd Daemon
```

### Repository Boundaries

| Repository | Responsibility |
| --- | --- |
| [pepepow-electrumx-service](https://github.com/edisontw/pepepow-electrumx-service) | FastAPI gateway, cache, dashboard pages, wallet API, deployment scripts and configurations |
| [pepepow-light-wallet](https://github.com/edisontw/pepepow-light-wallet) | Vite/React client-side non-custodial web wallet and wallet core packages |
| `electrumx-pepepow` | Upstream chain indexing support for PEPEPOW network |

### File Structure Map

```text
pepepow-electrumx-service
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routers (address, wallet, tx, payment, health)
│   │   ├── electrumx/    # ElectrumX JSON-RPC client
│   │   ├── services/     # Caching and upstream query handlers
│   │   ├── static/       # Dashboard styles & brand assets (brand/logo.png)
│   │   └── templates/    # HTML jinja2 templates (index, status, address, tx, pay)
│   └── tests/            # Pytest suite
├── deploy/
│   ├── nginx/            # Hardened Nginx site configurations
│   ├── systemd/          # Hardened systemd service configurations
│   └── deploy_wallet.sh  # Wallet build and deployment sync script
├── docs/                 # WALLET_API and SECURITY guidelines
└── README.md             # Project documentation

pepepow-light-wallet
└── apps/
    └── web/              # Vite / React wallet client codebase
```

---

## 2. Deployment Instructions

### 2.1 Backend Deployment

To deploy or update the FastAPI gateway:

```bash
cd /home/ubuntu/pepepow-electrumx-service
git pull
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
pytest backend/

# Copy configurations
sudo cp deploy/systemd/pepew-light.service /etc/systemd/system/pepew-light.service
sudo systemctl daemon-reload
sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager
```

### 2.2 Wallet & Static Asset Deployment

To build the client-side wallet SPA and sync brand files to Nginx:

```bash
# Executing build and copy steps automatically via deployment script
bash deploy/deploy_wallet.sh
```

*(Manual steps included in the script)*:
```bash
cd /home/ubuntu/pepepow-light-wallet/apps/web
npm install
npm run build

# Deploy built package to Nginx root path
sudo mkdir -p /var/www/pepew-light/wallet
sudo rm -rf /var/www/pepew-light/wallet/*
sudo cp -a apps/web/dist/. /var/www/pepew-light/wallet/

# Sync brand assets and favicons
sudo mkdir -p /var/www/pepew-light/static/brand
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/brand/. /var/www/pepew-light/static/brand/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/favicon.ico /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/favicon.svg /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/apple-touch-icon.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/icon-192.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/icon-512.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/site.webmanifest /var/www/pepew-light/

sudo chown -R www-data:www-data /var/www/pepew-light
```

### 2.3 Nginx Deployment

```bash
sudo cp deploy/nginx/pepew-light /etc/nginx/sites-available/pepew-light
sudo ln -sfn /etc/nginx/sites-available/pepew-light /etc/nginx/sites-enabled/pepew-light
sudo nginx -t
sudo systemctl reload nginx
```

---

## 3. Security Boundary & Hardening Policies

PEPEW Light strictly separates gateway duties from private cryptography.

1. **Non-Custodial Architecture**: Mnemonics, seed phrases, and private keys stay strictly in the user's browser. The server never receives, stores, derives, or signs using wallet secrets.
2. **Read-Only API Boundaries**: Route endpoints only process public addresses, transaction IDs, query configurations, or signed raw transactions.
3. **CORS Policy**: Configured `CORSMiddleware` in FastAPI allows cross-origin reading for developers (`allow_origins=["*"]`) but strictly disables credentials support (`allow_credentials=False`) to block cross-site scripting/credential leak attempts.
4. **Nginx Rate Limits**:
   - General API endpoints (e.g. `/api/status`): Limited to `5r/s` with a burst buffer of 20 requests per IP.
   - Heavy query endpoints (e.g. `/api/address`, `/api/tx`, `/api/wallet`): Limited to `3r/s` with a burst buffer of 15 requests per IP (nodelay).
   - Health checks (`/api/health`): Bypassed from rate limit constraints.
5. **FastAPI Input Validation**:
   - Address strings undergo rigorous checksum and format prefix verification (P2PKH decoding).
   - Transaction IDs (txids) are normalized and checked to verify they represent exactly 64-character hexadecimal values.
   - Amounts and confirmations are validated for positive boundaries.
   - Input exceptions return structured `400 Bad Request` payloads without exposing server stack traces or internal implementation paths.
6. **Security Headers**: Nginx enforces defensive HTTP response headers for both static assets and API routes:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: SAMEORIGIN`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
7. **Timeout Limits**: Upstream ElectrumX JSON-RPC calls are limited to a strict `5.0s` connection/request timeout to prevent gateway thread exhaustion.
8. **Systemd Isolation**:
   - Managed with `NoNewPrivileges=true` and `PrivateTmp=true`.
   - Hardened with system directory protection (`ProtectSystem=full`) and home directory write restriction (`ProtectHome=read-only`).
9. **Log Privacy**: Access logs strip request bodies (to guarantee no raw transaction data or signatures are recorded) and avoid long-term IP/address correlations.

---

## 4. Diagnostics & Troubleshooting Commands

Here are common commands to check, verify, and debug active services:

```bash
# Check FastAPI API Gateway status
sudo systemctl status pepew-light --no-pager
sudo journalctl -u pepew-light -n 100 --no-pager

# Check Nginx syntax and status
sudo nginx -t
sudo systemctl status nginx --no-pager
sudo tail -n 100 /var/log/nginx/pepew-light.error.log

# Fetch local endpoints and verify security headers
curl -k -I -H "Host: light.pepepow.net" https://127.0.0.1/
curl -k -I -H "Host: light.pepepow.net" https://127.0.0.1/wallet/
curl -k -I -H "Host: light.pepepow.net" https://127.0.0.1/static/brand/logo.png
curl -s -H "Host: light.pepepow.net" https://127.0.0.1/api/health
curl -s -H "Host: light.pepepow.net" https://127.0.0.1/api/status
```

---

## 5. Minimum Verification Checklist

Verify the following items before marking a release as ready:

- [ ] `/api/health` returns `{"ok":true}` and loads with CORS active
- [ ] `/api/status` returns the sync state of the private ElectrumX node
- [ ] `/static/brand/logo.png` renders without broken images on all public dashboard pages
- [ ] `/wallet/` SPA interface loads correctly
- [ ] Address page can look up balance, tx logs, and render QR codes
- [ ] Invalid addresses (e.g. invalid checksum or short input) return a clean `400 Bad Request` message
- [ ] API rate limit restricts excessive burst requests without blocking legitimate refreshes
- [ ] Non-custodial warning notice displays correctly on the home page and wallet dashboard
- [ ] Diagnostic test suite runs with 100% test passes (`pytest`)

---

## 6. Payment Monitor Details

Payment Monitor is not an invoice database.
- It does not create, reserve, store, or expire invoices server-side.
- Each payment should use a unique receiving address.
- Reusing addresses can cause ambiguous payment detection.

The payment monitor is address-level and read-only. Amounts are address-level totals. Mempool values are unconfirmed. This is not a merchant invoice ledger.

Important response fields:
- `requested_amount`
- `requested_sats`
- `confirmed_received_sats`
- `mempool_received_sats`
- `total_received_sats`
- `status_explanation`
- `explorer_address_url`

Configuration features like `PEPEW_MIN_CONFIRMATIONS` define confirmation logic.
