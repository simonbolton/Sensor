# SwitchBot sensor box

One Docker container for Dockge:

- BLE collector (SwitchBot IP65 meters)
- Mosquitto MQTT broker
- SQLite database (2-year retention by default)
- Simple web page for live + recent history

**Security classification: Low** — local LAN use. Do not expose MQTT/web to the public internet without auth and TLS.

## Image

```text
ghcr.io/simonbolton/sensor/collector:latest
```

## Dockge / Compose (single service)

```yaml
services:
  sensor:
    image: ghcr.io/simonbolton/sensor/collector:latest
    restart: unless-stopped
    privileged: true
    network_mode: host
    security_opt:
      - apparmor:unconfined
    environment:
      SENSORS: outdoor=AA:BB:CC:DD:EE:FF
      # Optional MQTT auth:
      # MQTT_USERNAME: sensor
      # MQTT_PASSWORD: change-me
      SCAN_INTERVAL_SECONDS: "900"
      RETENTION_DAYS: "730"
      WEB_PORT: "8080"
      LOG_LEVEL: INFO
    volumes:
      - ./data:/data
      - /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket
```

Then open: `http://<host-ip>:8080`

MQTT broker (same host): `mqtt://<host-ip>:1883`

## Environment variables

| Variable | Example | Required |
| --- | --- | --- |
| `SENSORS` | `outdoor=AA:BB:CC:DD:EE:FF,garage=11:22:33:44:55:66` | Yes |
| `MQTT_USERNAME` | `sensor` | No (anonymous if empty) |
| `MQTT_PASSWORD` | secret | No |
| `MQTT_TOPIC_PREFIX` | `sensors/switchbot` | No |
| `SCAN_INTERVAL_SECONDS` | `900` | No |
| `SCAN_TIMEOUT_SECONDS` | `60` | No |
| `RETENTION_DAYS` | `730` | No |
| `WEB_PORT` | `8080` | No |
| `LOG_LEVEL` | `INFO` | No |
| `ENABLE_MQTT` | `true` | No |
| `SQLITE_PATH` | `/data/sensors.db` | No |

`SENSORS` format: `name=MAC,name2=MAC2`  
MAC from SwitchBot app → **Settings → Device Info → BLE MAC**.

## Host requirements

Ubuntu host with Bluetooth:

```bash
sudo apt update
sudo apt install -y bluez bluetooth
sudo systemctl enable --now bluetooth
```

`network_mode: host`, `privileged: true`, and `security_opt: apparmor:unconfined` are required so BLE/D-Bus works on Ubuntu (otherwise AppArmor returns `AccessDenied` on BlueZ).

## Local build

```bash
cp .env.example .env
# edit SENSORS
docker compose up -d --build
```

## API

- `GET /` — dashboard
- `GET /api/latest` — latest reading per sensor
- `GET /api/history?hours=24` — history JSON
- `GET /healthz` — health check

## CI

Push/PR to `main` builds and publishes the image to GHCR.
