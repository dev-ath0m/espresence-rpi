#!/usr/bin/env bash
# Stops and removes the ESPresense-Pi systemd service.
#
# Usage: sudo ./scripts/uninstall.sh [--purge]
# --purge also deletes /opt/espresense-pi (including config.yaml and devices.json)
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run this script with sudo: sudo ./scripts/uninstall.sh" >&2
  exit 1
fi

SERVICE_NAME="espresense-pi"
INSTALL_DIR="/opt/espresense-pi"

systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

if [[ "${1:-}" == "--purge" ]]; then
  echo "Removing $INSTALL_DIR (including config.yaml and devices.json)"
  rm -rf "$INSTALL_DIR"
else
  echo "Service removed. Files kept at $INSTALL_DIR (use --purge to delete)."
fi
