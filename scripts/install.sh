#!/usr/bin/env bash
# Installs ESPresense-Pi as a systemd service on a Raspberry Pi.
#
# Usage: sudo ./scripts/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run this script with sudo: sudo ./scripts/install.sh" >&2
  exit 1
fi

INSTALL_DIR="/opt/espresense-pi"
SERVICE_NAME="espresense-pi"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing system dependencies (bluetooth, python venv)"
apt-get update -qq
apt-get install -y --no-install-recommends bluetooth bluez python3-venv python3-pip rsync

echo "==> Copying files from $REPO_DIR to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --exclude 'venv' --exclude '.git' --exclude '__pycache__' "$REPO_DIR"/ "$INSTALL_DIR"/

cd "$INSTALL_DIR"

echo "==> Creating Python virtual environment"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [[ ! -f config.yaml ]]; then
  echo "==> Creating default config.yaml (edit later via the web UI)"
  cp config.example.yaml config.yaml
fi

echo "==> Installing systemd unit"
cp systemd/espresense-pi.service /etc/systemd/system/espresense-pi.service
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "==> Done."
echo "    Service status: sudo systemctl status $SERVICE_NAME"
echo "    Logs:           sudo journalctl -u $SERVICE_NAME -f"
echo "    Web UI:         http://${IP_ADDR:-<pi-ip>}:8080"
