#!/bin/sh
set -eu

mkdir -p /data /data/mosquitto

CONF=/tmp/mosquitto.conf
cat > "$CONF" <<'EOF'
listener 1883
protocol mqtt
persistence true
persistence_location /data/mosquitto/
log_dest stdout
log_type error
log_type warning
log_type notice
EOF

if [ -n "${MQTT_USERNAME:-}" ] && [ -n "${MQTT_PASSWORD:-}" ]; then
  mosquitto_passwd -b -c /data/mosquitto/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
  {
    echo "allow_anonymous false"
    echo "password_file /data/mosquitto/passwd"
  } >> "$CONF"
  echo "mqtt_auth_enabled user=$MQTT_USERNAME"
else
  echo "allow_anonymous true" >> "$CONF"
  echo "mqtt_auth_disabled allow_anonymous=true"
fi

mosquitto -c "$CONF" &
MQTT_PID=$!
sleep 1

python -u app.py &
APP_PID=$!

cleanup() {
  kill "$APP_PID" "$MQTT_PID" 2>/dev/null || true
  wait "$APP_PID" "$MQTT_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait "$APP_PID"
STATUS=$?
cleanup
exit "$STATUS"
