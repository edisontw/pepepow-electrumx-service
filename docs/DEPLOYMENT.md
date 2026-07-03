# Deployment Guide

This document describes how to build, deploy, and verify the backend API gateway and the frontend wallet.

---

## 1. Backend Startup

The FastAPI backend is executed via Uvicorn. The production environment configuration variables are loaded from `/home/ubuntu/pepepow-electrumx-service/backend/.env`.

---

## 2. Systemd Service

The FastAPI backend runs as a background service managed by systemd:

- **Service Name**: `pepew-light.service`
- **Service Configuration File**: `/etc/systemd/system/pepew-light.service` (templated from `deploy/systemd/pepew-light.service`)

### Operations Commands:

```bash
# Check status of the backend service
sudo systemctl status pepew-light --no-pager

# Restart the backend service
sudo systemctl restart pepew-light

# Inspect backend logs
journalctl -u pepew-light -n 100 --no-pager
```

---

## 3. Nginx Configuration

Nginx acts as the front-facing proxy server handling SSL (managed via Certbot), static files, and reverse proxy routing.

- **Nginx Config Path**: `/etc/nginx/sites-available/pepew-light` (symlinked to `/etc/nginx/sites-enabled/pepew-light`)
- **Nginx Config Template**: `deploy/nginx/pepew-light`

---

## 4. Wallet Build and Deploy Flow

The wallet build process is managed by `deploy_wallet.sh`. The script executes the following:
1. Navigates to `/home/ubuntu/pepepow-light-wallet`
2. Appends custom Node binaries (under `/home/ubuntu/node-dist/bin`) to system `PATH`
3. Installs dependencies using `npm ci` (or `npm install`)
4. Builds the wallet package (`packages/wallet-core` first, then `apps/web`) using `npm run build`
5. Clears previous deployment files under `/home/ubuntu/pepepow-electrumx-service/frontend/static/wallet`
6. Copies the built assets from `apps/web/dist` to `/home/ubuntu/pepepow-electrumx-service/frontend/static/wallet`

---

## 5. Complete Deployment Steps

To redeploy the service and wallet, execute the following commands sequence:

```bash
# 1. Update backend repo
cd /home/ubuntu/pepepow-electrumx-service
git pull

# 2. Update wallet repo
cd /home/ubuntu/pepepow-light-wallet
git pull

# 3. Build & deploy wallet static assets
/home/ubuntu/pepepow-electrumx-service/deploy/deploy_wallet.sh

# 4. Check Nginx syntax validity
sudo nginx -t

# 5. Reload Nginx configuration
sudo systemctl reload nginx

# 6. Restart FastAPI backend service
sudo systemctl restart pepew-light
```

---

## 6. Post-Deployment Verification

Verify the deployment is working correctly by checking HTTP response statuses:

```bash
# Check landing page
curl -I https://light.pepepow.net/

# Check wallet static page
curl -I https://light.pepepow.net/wallet/

# Check wallet health API
curl -s https://light.pepepow.net/api/health

# Check electrumx status API
curl -s https://light.pepepow.net/api/status
```
