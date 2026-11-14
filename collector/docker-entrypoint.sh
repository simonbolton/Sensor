#!/bin/sh
set -u

echo "entrypoint_begin ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "entrypoint_user=$(id)"
echo "entrypoint_cwd=$(pwd)"

mkdir -p /data /data/mosquitto
echo "entrypoint_data_dir_ready path=/data"

MQTT_PORT="${MQTT_PORT:-1883}"
ENABLE_MQTT="${ENABLE_MQTT:-true}"
echo "entrypoint_config ENABLE_MQTT=$ENABLE_MQTT MQTT_PORT=$MQTT_PORT WEB_PORT=${WEB_PORT:-8080}"
echo "entrypoint_env_SENSORS=${SENSORS:-<unset>}"

if [ -S /var/run/dbus/system_bus_socket ]; then
  echo "entrypoint_dbus_socket=present"
else
  echo "entrypoint_dbus_socket=MISSING hint='Mount /var/run/dbus/system_bus_socket'"
fi

if [ -e /sys/class/bluetooth ]; then
  echo "entrypoint_bluetooth_sysfs=present"
  ls -1 /sys/class/bluetooth 2>/dev/null | while read -r iface; do
    echo "entrypoint_bluetooth_iface=$iface"
  done
else
  echo "entrypoint_bluetooth_sysfs=MISSING hint='Host Bluetooth adapter not visible'"
fi

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

  echo "mqtt_starting port=$MQTT_PORT"
  mosquitto -c "$CONF" &
  MQTT_PID=$!
  sleep 1
  if kill -0 "$MQTT_PID" 2>/dev/null; then
    echo "mqtt_started pid=$MQTT_PID port=$MQTT_PORT"
  else
    echo "mqtt_start_failed port=$MQTT_PORT continuing_without_broker" >&2
    MQTT_PID=""
  fi
}

MQTT_PID=""
case "$(echo "$ENABLE_MQTT" | tr '[:upper:]' '[:lower:]')" in
  0|false|no) echo "mqtt_disabled" ;;
  *) start_mosquitto ;;
esac

echo "app_starting command='python -u app.py'"
python -u app.py &
APP_PID=$!
echo "app_started pid=$APP_PID"

cleanup() {
  echo "entrypoint_cleanup begin"
  kill "$APP_PID" 2>/dev/null || true
  if [ -n "${MQTT_PID}" ]; then
    kill "$MQTT_PID" 2>/dev/null || true
  fi
  wait "$APP_PID" 2>/dev/null || true
  if [ -n "${MQTT_PID}" ]; then
    wait "$MQTT_PID" 2>/dev/null || true
  fi
  echo "entrypoint_cleanup done"
}
trap cleanup INT TERM EXIT

wait "$APP_PID"
STATUS=$?
echo "app_exited status=$STATUS"
cleanup
trap - EXIT
exit "$STATUS"
