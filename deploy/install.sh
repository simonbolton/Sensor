#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./deploy/install.sh"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR=/opt/sensor
ENV_FILE=/etc/sensor.env
DATA_DIR=/var/lib/sensor
SERVICE_FILE=/etc/systemd/system/sensor.service

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip bluez bluetooth

echo "==> Enabling Bluetooth"
systemctl enable --now bluetooth || true
rfkill unblock bluetooth 2>/dev/null || true

echo "==> Installing app to ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
rm -rf "${INSTALL_DIR:?}/"*
cp -a "${REPO_ROOT}/collector/." "${INSTALL_DIR}/"
rm -rf "${INSTALL_DIR}/.venv" "${INSTALL_DIR}/tests" "${INSTALL_DIR}/.pytest_cache"
find "${INSTALL_DIR}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

echo "==> Creating Python virtualenv"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "==> Writing ${ENV_FILE}"
  cp "${REPO_ROOT}/deploy/sensor.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Edit ${ENV_FILE} and set SENSORS=name=AA:BB:CC:DD:EE:FF"
else
  echo "==> Keeping existing ${ENV_FILE}"
fi

echo "==> Installing systemd service"
cp "${REPO_ROOT}/deploy/sensor.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable sensor.service
systemctl restart sensor.service

sleep 2
systemctl --no-pager --full status sensor.service || true

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
WEB_PORT="$(grep -E '^WEB_PORT=' "${ENV_FILE}" | cut -d= -f2- || echo 8678)"
echo
echo "Installed."
echo "  Config:  ${ENV_FILE}"
echo "  Logs:    journalctl -u sensor -f"
echo "  Web UI:  http://${HOST_IP:-<server-ip>}:${WEB_PORT:-8678}"
echo
echo "If you just installed, edit SENSORS in ${ENV_FILE}, then:"
echo "  sudo systemctl restart sensor"
