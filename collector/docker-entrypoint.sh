#!/bin/sh
set -u

mkdir -p /data /data/mosquitto

MQTT_PORT="${MQTT_PORT:-1883}"
ENABLE_MQTT="${ENABLE_MQTT:-true}"

start_mosquitto() {
  CONF=/tmp/mosquitto.conf
  cat > "$CONF" <<EOF
listener ${MQTT_PORT}
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
    echo "mqtt_auth_enabled user=$MQTT_USERNAME port=$MQTT_PORT"
  else
    echo "allow_anonymous true" >> "$CONF"
    echo "mqtt_auth_disabled allow_anonymous=true port=$MQTT_PORT"
  fi

  mosquitto -c "$CONF" &
  MQTT_PID=$!
  sleep 1
  if ! kill -0 "$MQTT_PID" 2>/dev/null; then
    echo "mqtt_start_failed port=$MQTT_PORT continuing_without_broker" >&2
    MQTT_PID=""
  fi
}

MQTT_PID=""
case "$(echo "$ENABLE_MQTT" | tr '[:upper:]' '[:lower:]')" in
  0|false|no) echo "mqtt_disabled" ;;
  *) start_mosquitto ;;
esac

python -u app.py &
APP_PID=$!

cleanup() {
  kill "$APP_PID" 2>/dev/null || true
  if [ -n "${MQTT_PID}" ]; then
    kill "$MQTT_PID" 2>/dev/null || true
  fi
  wait "$APP_PID" 2>/dev/null || true
  if [ -n "${MQTT_PID}" ]; then
    wait "$MQTT_PID" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

wait "$APP_PID"
STATUS=$?
cleanup
trap - EXIT
exit "$STATUS"
