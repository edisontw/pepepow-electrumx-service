#!/usr/bin/env bash
set -euo pipefail

export PATH="/home/ubuntu/node-dist/bin:$PATH"

WALLET_DIR="/home/ubuntu/pepepow-light-wallet"
TARGET_DIR="/var/www/pepew-light/wallet"

echo "[deploy_wallet] Building wallet from: $WALLET_DIR"
cd "$WALLET_DIR"

if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

npm run build

echo "[deploy_wallet] Deploying to: $TARGET_DIR"
sudo mkdir -p "$TARGET_DIR"
sudo rm -rf "$TARGET_DIR"
sudo mkdir -p "$TARGET_DIR"
sudo cp -a apps/web/dist/. "$TARGET_DIR/"

# Copy root-level favicon/PWA assets to /var/www/pepew-light
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/favicon.ico /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/favicon.svg /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/apple-touch-icon.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/icon-192.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/icon-512.png /var/www/pepew-light/
sudo cp -a /home/ubuntu/pepepow-electrumx-service/frontend/static/site.webmanifest /var/www/pepew-light/

sudo chown -R www-data:www-data /var/www/pepew-light
sudo find /var/www/pepew-light -type d -exec chmod 755 {} \;
sudo find /var/www/pepew-light -type f -exec chmod 644 {} \;

echo "[deploy_wallet] Done."
echo "[deploy_wallet] Check: https://light.pepepow.net/wallet/"
