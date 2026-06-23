#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/ubuntu/pepepow-electrumx-service"
BACKEND_DIR="$APP_DIR/backend"

cd "$APP_DIR"
git pull --ff-only

cd "$BACKEND_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env

sudo systemctl daemon-reload
sudo systemctl restart pepew-light
sudo systemctl status pepew-light --no-pager
