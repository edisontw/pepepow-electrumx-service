#!/usr/bin/env bash
set -euo pipefail

export PATH="/home/ubuntu/node-dist/bin:$PATH"

WALLET_DIR="/home/ubuntu/pepepow-light-wallet"
TARGET_DIR="/home/ubuntu/pepepow-electrumx-service/frontend/static/wallet"

echo "[deploy_wallet] Building wallet from: $WALLET_DIR"
cd "$WALLET_DIR"

if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

npm run build

echo "[deploy_wallet] Deploying to: $TARGET_DIR"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -a apps/web/dist/. "$TARGET_DIR/"

echo "[deploy_wallet] Done."
echo "[deploy_wallet] Check: https://light.pepepow.net/wallet/"
