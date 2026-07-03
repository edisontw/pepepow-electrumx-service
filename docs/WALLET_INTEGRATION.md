# Wallet Integration Guide

This document describes how the `pepepow-light-wallet` integration works, its routing structure, and the critical security boundaries.

---

## 1. Why the Wallet Repo is Independent

- **Non-Custodial Design Principle**: Keeping the client-side wallet code in a separate repository ensures that no wallet secrets, seed derivation, or transaction signing algorithms are mixed into the backend repository.
- **Independent CI/CD**: The wallet is a pure static single-page application (React/Vite). Keeping it separate prevents build issues or dependencies (such as Node vs Python venv) from affecting backend deployments.

---

## 2. Why Deployed Under `/wallet/`

Oracle Cloud host resources are limited (1 core / 6GB RAM). Running a single web application server simplifies deployment:
- Serving the wallet under `/wallet/` allows Nginx to handle fast, static file rendering directly from disk, saving CPU and RAM for FastAPI, PEPEPOWd, and ElectrumX.
- A single domain name (`https://light.pepepow.net`) serves both the API gateway and the wallet, avoiding complex CORS (Cross-Origin Resource Sharing) configurations.

---

## 3. How the Wallet Calls the API

The wallet communicates with same-origin routes configured in [pepewLightClient.ts](file:///home/ubuntu/pepepow-light-wallet/apps/web/src/lib/pepewLightClient.ts):

- **Address Details**: `GET /api/wallet/address/{address}`
- **Address History**: `GET /api/wallet/history/{address}`
- **UTXO Details**: `GET /api/wallet/utxo/{address}`
- **Transaction Details**: `GET /api/wallet/tx/{txid}`
- **Broadcast Signed Transaction**: `POST /api/wallet/broadcast`

---

## 4. Vite Base Configuration

To ensure assets (JS, CSS, logos) are loaded correctly under `/wallet/`, the Vite application is configured with `base: '/wallet/'` in [vite.config.ts](file:///home/ubuntu/pepepow-light-wallet/apps/web/vite.config.ts).

---

## 5. Relative Paths vs Local Development

- **Production Build**: By leaving `VITE_PEPEW_LIGHT_API_BASE_URL` empty in `apps/web/.env.production`, the client-side code defaults to relative paths (`/api/wallet/...`).
- **Local Development**: Developers can create a local env configuration in `apps/web/.env.local` or reference `.env.example`:
  ```env
  VITE_PEPEW_LIGHT_API_BASE_URL=http://localhost:8000
  ```
  This directs local frontend servers to call the local FastAPI backend.

---

## 6. Security Boundary

> [!IMPORTANT]
> **English**:
> The backend must never receive mnemonic phrases, private keys, or unsigned signing material.
> All derivation and signing logic belongs to the client-side wallet.
> The API only receives addresses, txid parameters, query inputs, and future signed raw transactions.
>
> **中文**:
> 後端不得接收助記詞、私鑰或簽章材料。
> 所有派生與簽名邏輯都只能在 client-side wallet。
> API 僅接收地址、txid、查詢參數，以及未來的 signed raw transaction。
