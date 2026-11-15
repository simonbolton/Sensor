#!/usr/bin/env bash
set -euo pipefail

MAC="${1:-}"
if [[ -z "${MAC}" ]]; then
  if [[ -f /etc/sensor.env ]]; then
    # shellcheck disable=SC1091
    set -a
    source /etc/sensor.env
    set +a
    # SENSORS=name=AA:BB:...,name2=...
    MAC="$(echo "${SENSORS:-}" | sed -n 's/^[^=]*=\([^,]*\).*/\1/p')"
  fi
fi

if [[ -z "${MAC}" ]]; then
  echo "Usage: sudo ./deploy/diagnose.sh F2:A9:C0:86:55:21"
  exit 1
fi

if [[ -x /opt/sensor/.venv/bin/python ]]; then
  cd /opt/sensor
  exec /opt/sensor/.venv/bin/python -u diagnose_ble.py "${MAC}" --timeout 45
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/collector"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -u diagnose_ble.py "${MAC}" --timeout 45
