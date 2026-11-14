#!/bin/sh
set -eu

if [ -z "${MQTT_USERNAME:-}" ] || [ -z "${MQTT_PASSWORD:-}" ]; then
  echo "MQTT_USERNAME and MQTT_PASSWORD must be set" >&2
  exit 1
fi

mosquitto_passwd -b -c /mosquitto/data/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
chown mosquitto:mosquitto /mosquitto/data/passwd
chmod 0600 /mosquitto/data/passwd

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
