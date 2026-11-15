# SwitchBot sensor box

Reads SwitchBot BLE thermometer/hygrometer ads, stores them in SQLite, and shows a simple web page.

**Recommended on Ubuntu:** install directly on the server (no Docker). Bluetooth works reliably this way.

## Native Ubuntu install (recommended)

On the server:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/simonbolton/Sensor.git
cd Sensor
sudo ./deploy/install.sh
```

Edit your meter MAC:

```bash
sudo nano /etc/sensor.env
```

Set:

```bash
SENSORS=garage=F2:A9:C0:86:55:21
WEB_PORT=8678
```

Use the BLE MAC from the SwitchBot app (**Settings → Device Info → BLE MAC**), or from:

```bash
sudo bluetoothctl scan on
```

Restart:

```bash
sudo systemctl restart sensor
sudo journalctl -u sensor -f
```

Open:

```text
http://<server-ip>:8678
```

### Useful commands

```bash
sudo systemctl status sensor
sudo journalctl -u sensor -f
sudo systemctl restart sensor
```

### Update later

```bash
cd Sensor
git pull
sudo ./deploy/install.sh
```

## What you get

| Piece | Detail |
| --- | --- |
| Web UI | `http://<server-ip>:8678` |
| Database | SQLite at `/var/lib/sensor/sensors.db` |
| Config | `/etc/sensor.env` |
| Service | `sensor.service` (systemd) |

No phone pairing is required on the server. The meter broadcasts BLE ads; the service only scans them.

## Docker / Dockge (optional)

Docker on Ubuntu often hits AppArmor/D-Bus issues with Bluetooth. Prefer the native install above.

If you still want Docker, use host networking, privileged mode, D-Bus mount, and `security_opt: apparmor:unconfined`. See `docker-compose.yml`.

Image: `ghcr.io/simonbolton/sensor/collector:latest`
