# Repository and Directory Structure

This document details the directories, files, and control pathways for the integration between `pepepow-electrumx-service` and `pepepow-light-wallet`.

## 1. Repository Locations

- **PEPEW Light ElectrumX Service (FastAPI Backend, Landing Page, & Proxy)**
  `/home/ubuntu/pepepow-electrumx-service`
- **PEPEW Light Wallet (Client-side Non-custodial Frontend)**
  `/home/ubuntu/pepepow-light-wallet`

---

## 2. Backend Main Directory Layout

The service directory `/home/ubuntu/pepepow-electrumx-service` contains:

```text
backend/app/            - FastAPI application package
backend/app/api/        - API route handlers (health, status, address, tx, wallet)
backend/app/services/   - Business logic services (status, address, tx, payment)
backend/app/templates/  - Jinja2 HTML templates for landing/status/pay pages
backend/tests/          - Backend test suite
frontend/static/        - Static assets for public landing/status/pay pages
frontend/static/wallet  - [Deprecated] Previous static path (no longer used in active production)
deploy/                 - Deployment configs and shell scripts (systemd, nginx, deploy_wallet.sh)
docs/                   - Documentation (STRUCTURE, DEPLOYMENT, WALLET_INTEGRATION, DEBUGGING)
```

---

## 3. Wallet Main Directory Layout

The wallet directory `/home/ubuntu/pepepow-light-wallet` contains:

```text
apps/web/               - Vite React Single Page Application (SPA)
apps/web/src/           - React components, state stores, and page views
apps/web/src/components/ - Reusable components (ApiStatusBar, ErrorBoundary, Layouts)
apps/web/src/pages/      - React page views (WalletHome, Send, History, Pay, Claim)
apps/web/src/services/   - Client-side data layers
apps/web/src/wallet/     - Wallet derivation/signing integration hooks
apps/web/public/         - Root-level static public assets (brand logos, etc.)
apps/web/dist/           - [Artifact] Production static assets output directory
packages/wallet-core/   - Core cryptographic / transaction derivation SDK
```

---

## 4. Production Wallet Build Output

The wallet production build generates static files in the wallet repository under:
`/home/ubuntu/pepepow-light-wallet/apps/web/dist`

During deployment, these compiled artifacts are copied to:
`/var/www/pepew-light/wallet`

> [!NOTE]
> `frontend/static/wallet` is no longer the production static serving path if `/var/www/pepew-light/wallet` is used.

---

## 5. Source vs. Build Artifacts

- **Source Code (Git tracked)**: Everything under `apps/web/src`, `packages/wallet-core/src`, and `backend/app/`.
- **Build Artifacts (Git ignored / ephemeral)**:
  - Wallet core compiled outputs: `packages/wallet-core/dist/`
  - React SPA compiled outputs: `apps/web/dist/`
  - Service wallet destination: `/var/www/pepew-light/wallet` (all contents inside this folder are build outputs and should not be manually modified).

---

## 6. Route and Configuration Control Files

- **Vite Base Path**: Controlled by `base: '/wallet/'` in [vite.config.ts](file:///home/ubuntu/pepepow-light-wallet/apps/web/vite.config.ts).
- **API Base URL**: Controlled by `import.meta.env.VITE_PEPEW_LIGHT_API_BASE_URL` in [pepewLightClient.ts](file:///home/ubuntu/pepepow-light-wallet/apps/web/src/lib/pepewLightClient.ts) and defined in [.env.production](file:///home/ubuntu/pepepow-light-wallet/apps/web/.env.production).
- **Nginx /wallet/ Route**: Controlled by Nginx configuration template [pepew-light](file:///home/ubuntu/pepepow-electrumx-service/deploy/nginx/pepew-light) and system file `/etc/nginx/sites-available/pepew-light`.
- **systemd FastAPI Service**: Controlled by [pepew-light.service](file:///home/ubuntu/pepepow-electrumx-service/deploy/systemd/pepew-light.service) and system file `/etc/systemd/system/pepew-light.service`.
- **Deploy Script**: Controlled by [deploy_wallet.sh](file:///home/ubuntu/pepepow-electrumx-service/deploy/deploy_wallet.sh).
