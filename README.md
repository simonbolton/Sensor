# SwitchBot → MQTT → InfluxDB → Grafana

Local Docker stack that reads SwitchBot IP65 (and compatible) BLE thermometer/hygrometer advertisements every 15 minutes, publishes JSON to Mosquitto, stores points in InfluxDB (2-year retention), and visualises them in Grafana.

**Security classification: Low** — local LAN stack. Keep secrets in `.env` only; do not expose MQTT/Grafana to the public internet without auth and TLS.

## Architecture

| Service | Role |
| --- | --- |
| `collector` | Python BLE scanner (`bleak`) → MQTT + InfluxDB |
| `mosquitto` | MQTT broker (username/password) |
| `influxdb` | Time-series store, bucket retention **730d** |
| `grafana` | Provisioned dashboard |

MQTT topic layout:

```text
sensors/switchbot/{mac_without_colons}/telemetry
```

Example payload:

```json
{
  "name": "outdoor",
  "mac": "AA:BB:CC:DD:EE:FF",
  "timestamp": "2026-11-14T10:00:00+00:00",
  "temperature_c": 18.4,
  "humidity_pct": 62.0,
  "battery_pct": 97,
  "dewpoint_c": 11.1
}
```

Influx measurement `environment` with tags `name`, `mac` and fields `temperature_c`, `humidity_pct`, `battery_pct`, `dewpoint_c`.

## Host requirements (Ubuntu)

1. Docker Engine + Docker Compose plugin.
2. Bluetooth hardware and BlueZ:

```bash
sudo apt update
sudo apt install -y bluez bluetooth
sudo systemctl enable --now bluetooth
bluetoothctl show
```

3. Each meter must be in BLE range of this host (walls reduce range). Find the BLE MAC in the SwitchBot app: **Settings → Device Info → BLE MAC**.

## CI

On every push and pull request to `main`, GitHub Actions builds the `collector` Docker image. Pushes to `main` also publish the image to GitHub Container Registry:

```text
ghcr.io/simonbolton/sensor/collector:latest
ghcr.io/simonbolton/sensor/collector:<git-sha>
```

Pull (package inherits repo visibility; link the package to the repo in GitHub Packages if needed):

```bash
docker pull ghcr.io/simonbolton/sensor/collector:latest
```

Compose still builds locally by default (`build:` in `docker-compose.yml`). You can point `collector` at the GHCR image instead if you prefer.

## Dockage (env-only collector)

Image: `ghcr.io/simonbolton/sensor/collector:latest`

Set these **environment variables** in Dockage (no `sensors.yaml` mount required if `SENSORS` is set):

| Variable | Example | Required |
| --- | --- | --- |
| `SENSORS` | `outdoor=AA:BB:CC:DD:EE:FF,garage=11:22:33:44:55:66` | Yes (or mount yaml) |
| `MQTT_HOST` | `192.168.1.10` or `mosquitto` | Yes |
| `MQTT_PORT` | `1883` | No (default `1883`) |
| `MQTT_USERNAME` | `sensor` | Yes |
| `MQTT_PASSWORD` | *(secret)* | Yes |
| `MQTT_TOPIC_PREFIX` | `sensors/switchbot` | No |
| `INFLUX_URL` | `http://192.168.1.10:8086` | Yes |
| `INFLUX_ORG` | `home` | Yes |
| `INFLUX_BUCKET` | `sensors` | Yes |
| `INFLUX_TOKEN` | *(secret)* | Yes |
| `SCAN_INTERVAL_SECONDS` | `900` | No |
| `SCAN_TIMEOUT_SECONDS` | `60` | No |
| `LOG_LEVEL` | `INFO` | No |

`SENSORS` format: `name=MAC,name2=MAC2` (comma or semicolon separated). MACs from SwitchBot app → Device Info → BLE MAC.

Also enable for BLE on the host:

- Privileged mode **or** host networking
- Device/socket access to Bluetooth (mount `/var/run/dbus/system_bus_socket`)

Mosquitto, InfluxDB, and Grafana still need to run separately (compose stack or other Dockage apps). Point `MQTT_HOST` / `INFLUX_URL` at those services.

## Quick start

```bash
cp .env.example .env
# Edit .env — set strong MQTT / Influx / Grafana passwords and INFLUXDB_TOKEN

cp collector/sensors.yaml.example collector/sensors.yaml
# Edit collector/sensors.yaml — set real MACs and friendly names

docker compose up -d --build
```

Services (default ports):

- Grafana: http://\<server-ip\>:3000 (admin credentials from `.env`)
- InfluxDB: http://\<server-ip\>:8086
- MQTT: tcp://\<server-ip\>:1883

Dashboard: **SwitchBot Environment** (provisioned automatically).

> If you change `INFLUXDB_BUCKET` away from `sensors`, update the Flux queries in `grafana/dashboards/sensors.json` to match.

## Verify

MQTT (from any machine that can reach the broker):

```bash
docker compose exec mosquitto \
  mosquitto_sub -h 127.0.0.1 -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" \
  -t 'sensors/switchbot/#' -v
```

Or on the host (if `mosquitto-clients` is installed), using values from `.env`.

Collector logs:

```bash
docker compose logs -f collector
```

Look for `ble_reading_decoded`, `mqtt_publish_ok`, and `influx_write_ok`. The first successful cycle may take up to `SCAN_TIMEOUT_SECONDS` (default 60s) while waiting for advertisements; thereafter every `SCAN_INTERVAL_SECONDS` (default 900 = 15 minutes).

## Collector notes

- Runs with `network_mode: host` and `privileged: true` so BlueZ/D-Bus can access the host Bluetooth adapter.
- Uses **active** BLE scanning and exits early once all configured MACs have been seen.
- Missing meters are logged as `ble_scan_timeout` and skipped for that cycle (no fake data).

## Local decode tests

```bash
cd collector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
PYTHONPATH=. pytest -q
```

## Adding sensors

Edit `collector/sensors.yaml`:

```yaml
sensors:
  - name: outdoor
    mac: "AA:BB:CC:DD:EE:FF"
  - name: garage
    mac: "11:22:33:44:55:66"
```

Then restart the collector:

```bash
docker compose up -d collector
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No BLE readings | `bluetoothctl scan on` on the host; confirm MAC; move meter closer; `docker compose logs collector` |
| MQTT auth failures | `.env` `MQTT_USERNAME` / `MQTT_PASSWORD`; recreate mosquitto container |
| Grafana empty panels | Wait for a successful collector cycle; confirm bucket name `sensors`; check Influx Data Explorer |
| Collector cannot start | Ensure `collector/sensors.yaml` exists (copy from the example) |

## Out of scope (v1)

SwitchBot Hub/cloud API, HTTPS reverse proxy, alerting, non-SwitchBot protocols, BLE range extenders.
