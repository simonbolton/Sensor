"""All-in-one: BLE collector → SQLite + MQTT, with web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from paho.mqtt import client as mqtt_client

from database import SensorDatabase
from decode import SensorReading, decode_switchbot_advertisement
from web import create_app

LOG = logging.getLogger("sensor")


@dataclass(frozen=True)
class SensorConfig:
    name: str
    mac: str


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def parse_sensors_env(value: str) -> dict[str, SensorConfig]:
    by_mac: dict[str, SensorConfig] = {}
    chunks = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if not chunks:
        raise RuntimeError("SENSORS is set but empty")

    for chunk in chunks:
        if "=" not in chunk:
            raise RuntimeError(
                f"Invalid SENSORS entry {chunk!r}. Expected name=AA:BB:CC:DD:EE:FF"
            )
        name, mac = chunk.split("=", 1)
        name = name.strip()
        mac = mac.strip().upper()
        if not name or not mac:
            raise RuntimeError(f"Invalid SENSORS entry {chunk!r}")
        by_mac[mac] = SensorConfig(name=name, mac=mac)
    return by_mac


def load_sensors_file(path: Path) -> dict[str, SensorConfig]:
    if not path.is_file():
        raise RuntimeError(
            f"Sensors file not found: {path}. "
            "Set SENSORS env (name=MAC,...) or mount sensors.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("sensors") or []
    if not items:
        raise RuntimeError(f"No sensors configured in {path}")

    by_mac: dict[str, SensorConfig] = {}
    for item in items:
        name = str(item["name"]).strip()
        mac = str(item["mac"]).strip().upper()
        if not name or not mac:
            raise RuntimeError(f"Invalid sensor entry: {item!r}")
        by_mac[mac] = SensorConfig(name=name, mac=mac)
    return by_mac


def load_sensors() -> dict[str, SensorConfig]:
    sensors_env = os.getenv("SENSORS", "").strip()
    if sensors_env:
        return parse_sensors_env(sensors_env)
    sensors_path = Path(os.getenv("SENSORS_FILE", "/config/sensors.yaml"))
    return load_sensors_file(sensors_path)


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s event=%(message)s",
        stream=sys.stdout,
        force=True,
    )


def log_startup_diagnostics() -> None:
    LOG.info("startup_begin pid=%s python=%s", os.getpid(), sys.version.replace("\n", " "))
    interesting = [
        "SENSORS",
        "SENSORS_FILE",
        "SQLITE_PATH",
        "WEB_HOST",
        "WEB_PORT",
        "ENABLE_MQTT",
        "MQTT_HOST",
        "MQTT_PORT",
        "MQTT_USERNAME",
        "MQTT_TOPIC_PREFIX",
        "SCAN_INTERVAL_SECONDS",
        "SCAN_TIMEOUT_SECONDS",
        "RETENTION_DAYS",
        "LOG_LEVEL",
        "FATAL_HOLD_SECONDS",
    ]
    for key in interesting:
        raw = os.getenv(key)
        if raw is None:
            LOG.info("env_missing key=%s", key)
            continue
        if key == "MQTT_PASSWORD":
            continue
        LOG.info("env_set key=%s value=%r", key, raw)

    password_set = bool(os.getenv("MQTT_PASSWORD", "").strip())
    LOG.info("env_secret_present key=MQTT_PASSWORD present=%s", password_set)

    dbus_path = Path("/var/run/dbus/system_bus_socket")
    LOG.info(
        "dbus_socket path=%s exists=%s is_socket=%s",
        dbus_path,
        dbus_path.exists(),
        dbus_path.is_socket() if dbus_path.exists() else False,
    )

    data_dir = Path(os.getenv("SQLITE_PATH", "/data/sensors.db")).parent
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        LOG.info("data_dir_writable path=%s", data_dir)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("data_dir_not_writable path=%s error=%s", data_dir, exc)


async def probe_bluetooth() -> None:
    LOG.info("ble_probe_start")
    try:
        devices = await BleakScanner.discover(timeout=3.0, return_adv=False)
        LOG.info("ble_probe_ok device_count=%s", len(devices))
        for device in devices[:10]:
            LOG.info(
                "ble_probe_seen address=%s name=%r",
                getattr(device, "address", "?"),
                getattr(device, "name", None),
            )
        if len(devices) > 10:
            LOG.info("ble_probe_truncated remaining=%s", len(devices) - 10)
    except Exception as exc:  # noqa: BLE001
        LOG.exception(
            "ble_probe_failed error=%s hint='Need privileged=true, host network, "
            "and /var/run/dbus/system_bus_socket mounted'",
            exc,
        )


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        topic_prefix: str,
        connect_retries: int = 12,
        connect_retry_seconds: float = 5.0,
    ):
        self._topic_prefix = topic_prefix.rstrip("/")
        self._client = mqtt_client.Client(
            mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=f"sensor-collector-{os.getpid()}",
        )
        if username:
            self._client.username_pw_set(username, password or "")
        last_error: Exception | None = None
        for attempt in range(1, connect_retries + 1):
            try:
                self._client.connect(host, port, keepalive=60)
                self._client.loop_start()
                LOG.info("mqtt_connect_ok host=%s port=%s attempt=%s", host, port, attempt)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                LOG.warning(
                    "mqtt_connect_retry host=%s port=%s attempt=%s/%s error=%s",
                    host,
                    port,
                    attempt,
                    connect_retries,
                    exc,
                )
                time.sleep(connect_retry_seconds)
        raise RuntimeError(f"MQTT connect failed after {connect_retries} attempts: {last_error}")

    def publish_reading(self, sensor: SensorConfig, reading: SensorReading) -> None:
        topic = f"{self._topic_prefix}/{sensor.mac.lower().replace(':', '')}/telemetry"
        payload = {
            "name": sensor.name,
            "mac": sensor.mac,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **reading.as_dict(),
        }
        info = self._client.publish(topic, json.dumps(payload), qos=1, retain=True)
        info.wait_for_publish(timeout=10)
        LOG.info(
            "mqtt_publish_ok topic=%s name=%s temperature_c=%s humidity_pct=%s",
            topic,
            sensor.name,
            reading.temperature_c,
            reading.humidity_pct,
        )

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


async def scan_once(
    sensors: dict[str, SensorConfig],
    timeout_seconds: float,
) -> dict[str, SensorReading]:
    found: dict[str, SensorReading] = {}
    pending = set(sensors.keys())
    loop = asyncio.get_running_loop()
    done_event = asyncio.Event()

    def detection_callback(device: BLEDevice, advertisement: AdvertisementData) -> None:
        mac = device.address.upper()
        if mac not in pending or mac in found:
            return
        reading = decode_switchbot_advertisement(
            advertisement.manufacturer_data,
            advertisement.service_data,
        )
        if reading is None:
            return
        found[mac] = reading
        pending.discard(mac)
        LOG.info(
            "ble_reading_decoded name=%s mac=%s temperature_c=%s humidity_pct=%s battery_pct=%s",
            sensors[mac].name,
            mac,
            reading.temperature_c,
            reading.humidity_pct,
            reading.battery_pct,
        )
        if not pending:
            loop.call_soon_threadsafe(done_event.set)

    scanner = BleakScanner(detection_callback=detection_callback)
    LOG.info(
        "ble_scan_start timeout_seconds=%s sensor_count=%s",
        timeout_seconds,
        len(sensors),
    )
    await scanner.start()
    try:
        try:
            await asyncio.wait_for(done_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            missing = sorted(pending)
            LOG.warning(
                "ble_scan_timeout missing_count=%s missing_macs=%s",
                len(missing),
                ",".join(missing) if missing else "",
            )
    finally:
        await scanner.stop()

    LOG.info("ble_scan_complete found_count=%s", len(found))
    return found


async def run_cycle(
    sensors: dict[str, SensorConfig],
    mqtt: MqttPublisher | None,
    db: SensorDatabase,
    scan_timeout: float,
) -> None:
    readings = await scan_once(sensors, scan_timeout)
    for mac, reading in readings.items():
        sensor = sensors[mac]
        try:
            db.insert_reading(
                name=sensor.name,
                mac=sensor.mac,
                temperature_c=reading.temperature_c,
                humidity_pct=reading.humidity_pct,
                battery_pct=reading.battery_pct,
                dewpoint_c=reading.dewpoint_c,
            )
            LOG.info(
                "sqlite_write_ok name=%s mac=%s temperature_c=%s humidity_pct=%s",
                sensor.name,
                sensor.mac,
                reading.temperature_c,
                reading.humidity_pct,
            )
        except Exception:
            LOG.exception("sqlite_write_failed name=%s mac=%s", sensor.name, mac)

        if mqtt is not None:
            try:
                mqtt.publish_reading(sensor, reading)
            except Exception:
                LOG.exception("mqtt_publish_failed name=%s mac=%s", sensor.name, mac)

    deleted = db.purge_expired()
    if deleted:
        LOG.info("sqlite_purge_ok deleted=%s", deleted)


async def collector_loop(
    sensors: dict[str, SensorConfig],
    mqtt: MqttPublisher | None,
    db: SensorDatabase,
    scan_interval: int,
    scan_timeout: float,
    stop: asyncio.Event,
) -> None:
    LOG.info(
        "collector_started sensor_count=%s scan_interval_seconds=%s",
        len(sensors),
        scan_interval,
    )
    while not stop.is_set():
        cycle_started = time.monotonic()
        try:
            await run_cycle(sensors, mqtt, db, scan_timeout)
        except Exception:
            LOG.exception("cycle_failed")

        elapsed = time.monotonic() - cycle_started
        sleep_for = max(0.0, scan_interval - elapsed)
        LOG.info("cycle_complete elapsed_seconds=%.1f sleep_seconds=%.1f", elapsed, sleep_for)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except TimeoutError:
            pass
    LOG.info("collector_stopped")


async def main() -> int:
    configure_logging()
    log_startup_diagnostics()

    scan_interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "900"))
    scan_timeout = float(os.getenv("SCAN_TIMEOUT_SECONDS", "60"))
    retention_days = int(os.getenv("RETENTION_DAYS", "730"))
    db_path = Path(os.getenv("SQLITE_PATH", "/data/sensors.db"))
    web_host = os.getenv("WEB_HOST", "0.0.0.0")
    web_port = int(os.getenv("WEB_PORT", "8080"))
    enable_mqtt = os.getenv("ENABLE_MQTT", "true").lower() in {"1", "true", "yes"}

    LOG.info(
        "config_parsed scan_interval=%s scan_timeout=%s retention_days=%s "
        "web_host=%s web_port=%s enable_mqtt=%s db_path=%s",
        scan_interval,
        scan_timeout,
        retention_days,
        web_host,
        web_port,
        enable_mqtt,
        db_path,
    )

    try:
        sensors = load_sensors()
    except Exception:
        LOG.exception(
            "sensors_config_invalid hint='Use SENSORS=name=AA:BB:CC:DD:EE:FF "
            "(comma-separated for more)'"
        )
        raise

    for sensor in sensors.values():
        LOG.info("sensor_configured name=%s mac=%s", sensor.name, sensor.mac)

    await probe_bluetooth()

    try:
        db = SensorDatabase(db_path, retention_days=retention_days)
        LOG.info("sqlite_ready path=%s", db_path)
    except Exception:
        LOG.exception("sqlite_init_failed path=%s", db_path)
        raise

    mqtt: MqttPublisher | None = None
    if enable_mqtt:
        mqtt_user = os.getenv("MQTT_USERNAME", "").strip() or None
        mqtt_pass = os.getenv("MQTT_PASSWORD", "").strip() or None
        mqtt_host = os.getenv("MQTT_HOST", "127.0.0.1")
        mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
        LOG.info(
            "mqtt_connecting host=%s port=%s username_set=%s",
            mqtt_host,
            mqtt_port,
            bool(mqtt_user),
        )
        try:
            mqtt = MqttPublisher(
                host=mqtt_host,
                port=mqtt_port,
                username=mqtt_user,
                password=mqtt_pass,
                topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "sensors/switchbot"),
                connect_retries=int(os.getenv("MQTT_CONNECT_RETRIES", "6")),
                connect_retry_seconds=float(os.getenv("MQTT_CONNECT_RETRY_SECONDS", "2")),
            )
        except Exception:
            LOG.exception(
                "mqtt_unavailable continuing_without_mqtt "
                "hint='Port may already be in use on the host; "
                "set ENABLE_MQTT=false or MQTT_PORT to a free port'"
            )
            mqtt = None
    else:
        LOG.info("mqtt_skipped enable_mqtt=false")

    stop = asyncio.Event()

    def _request_stop(*_: Any) -> None:
        LOG.info("shutdown_requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())

    app = create_app(db)
    config = uvicorn.Config(app, host=web_host, port=web_port, log_level="info")
    server = uvicorn.Server(config)

    LOG.info(
        "web_starting host=%s port=%s sqlite_path=%s sensor_count=%s mqtt_enabled=%s",
        web_host,
        web_port,
        db_path,
        len(sensors),
        mqtt is not None,
    )

    collector_task = asyncio.create_task(
        collector_loop(sensors, mqtt, db, scan_interval, scan_timeout, stop),
        name="collector",
    )
    server_task = asyncio.create_task(server.serve(), name="web")

    done, pending = await asyncio.wait(
        {collector_task, server_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )
    stop.set()
    server.should_exit = True
    for task in pending:
        task.cancel()
    await asyncio.gather(collector_task, server_task, return_exceptions=True)
    if mqtt is not None:
        mqtt.close()

    for task in done:
        if task.cancelled():
            LOG.error("fatal_task_cancelled name=%s", task.get_name())
            return 1
        exc = task.exception()
        if exc is not None:
            LOG.error("fatal_task_failed name=%s error=%s", task.get_name(), exc)
            LOG.exception("fatal_task_traceback", exc_info=exc)
            return 1
        LOG.info("task_exited_cleanly name=%s", task.get_name())
    return 0


def _hold_before_exit(exit_code: int) -> int:
    hold = int(os.getenv("FATAL_HOLD_SECONDS", "30"))
    if exit_code != 0 and hold > 0:
        LOG.error(
            "fatal_hold_seconds=%s exit_code=%s hint='Read the logs above before restart'",
            hold,
            exit_code,
        )
        time.sleep(hold)
    return exit_code


if __name__ == "__main__":
    configure_logging()
    try:
        code = asyncio.run(main())
    except Exception:
        LOG.exception("fatal_unhandled_exception")
        raise SystemExit(_hold_before_exit(1)) from None
    raise SystemExit(_hold_before_exit(code))
