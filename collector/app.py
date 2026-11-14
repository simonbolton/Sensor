"""BLE collector: SwitchBot meters → MQTT + InfluxDB."""

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

import yaml
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from paho.mqtt import client as mqtt_client

from decode import SensorReading, decode_switchbot_advertisement

LOG = logging.getLogger("ble_collector")


@dataclass(frozen=True)
class SensorConfig:
    name: str
    mac: str


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def load_sensors(path: Path) -> dict[str, SensorConfig]:
    if not path.is_file():
        raise RuntimeError(
            f"Sensors file not found: {path}. Copy sensors.yaml.example to sensors.yaml."
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


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s event=%(message)s",
        stream=sys.stdout,
    )


class MqttPublisher:
    def __init__(self, host: str, port: int, username: str, password: str, topic_prefix: str):
        self._topic_prefix = topic_prefix.rstrip("/")
        self._client = mqtt_client.Client(
            mqtt_client.CallbackAPIVersion.VERSION2,
            client_id=f"ble-collector-{os.getpid()}",
        )
        self._client.username_pw_set(username, password)
        self._client.connect(host, port, keepalive=60)
        self._client.loop_start()

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


class InfluxWriter:
    def __init__(self, url: str, token: str, org: str, bucket: str):
        self._org = org
        self._bucket = bucket
        self._client = InfluxDBClient(url=url, token=token, org=org, timeout=10_000)
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)

    def write_reading(self, sensor: SensorConfig, reading: SensorReading) -> None:
        point = (
            Point("environment")
            .tag("mac", sensor.mac)
            .tag("name", sensor.name)
            .field("temperature_c", float(reading.temperature_c))
            .field("humidity_pct", float(reading.humidity_pct))
            .time(datetime.now(timezone.utc), WritePrecision.NS)
        )
        if reading.battery_pct is not None:
            point = point.field("battery_pct", int(reading.battery_pct))
        if reading.dewpoint_c is not None:
            point = point.field("dewpoint_c", float(reading.dewpoint_c))

        self._write_api.write(bucket=self._bucket, org=self._org, record=point)
        LOG.info(
            "influx_write_ok name=%s mac=%s temperature_c=%s humidity_pct=%s",
            sensor.name,
            sensor.mac,
            reading.temperature_c,
            reading.humidity_pct,
        )

    def close(self) -> None:
        self._write_api.close()
        self._client.close()


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
    mqtt: MqttPublisher,
    influx: InfluxWriter,
    scan_timeout: float,
) -> None:
    readings = await scan_once(sensors, scan_timeout)
    for mac, reading in readings.items():
        sensor = sensors[mac]
        try:
            mqtt.publish_reading(sensor, reading)
        except Exception:
            LOG.exception("mqtt_publish_failed name=%s mac=%s", sensor.name, mac)
        try:
            influx.write_reading(sensor, reading)
        except Exception:
            LOG.exception("influx_write_failed name=%s mac=%s", sensor.name, mac)


async def main() -> int:
    configure_logging()
    sensors_path = Path(env("SENSORS_FILE", "/config/sensors.yaml"))
    sensors = load_sensors(sensors_path)
    scan_interval = int(env("SCAN_INTERVAL_SECONDS", "900"))
    scan_timeout = float(env("SCAN_TIMEOUT_SECONDS", "60"))

    mqtt = MqttPublisher(
        host=env("MQTT_HOST", "127.0.0.1"),
        port=int(env("MQTT_PORT", "1883")),
        username=env("MQTT_USERNAME"),
        password=env("MQTT_PASSWORD"),
        topic_prefix=env("MQTT_TOPIC_PREFIX", "sensors/switchbot"),
    )
    influx = InfluxWriter(
        url=env("INFLUX_URL", "http://127.0.0.1:8086"),
        token=env("INFLUX_TOKEN"),
        org=env("INFLUX_ORG"),
        bucket=env("INFLUX_BUCKET"),
    )

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

    LOG.info(
        "collector_started sensor_count=%s scan_interval_seconds=%s",
        len(sensors),
        scan_interval,
    )

    try:
        while not stop.is_set():
            cycle_started = time.monotonic()
            try:
                await run_cycle(sensors, mqtt, influx, scan_timeout)
            except Exception:
                LOG.exception("cycle_failed")

            elapsed = time.monotonic() - cycle_started
            sleep_for = max(0.0, scan_interval - elapsed)
            LOG.info("cycle_complete elapsed_seconds=%.1f sleep_seconds=%.1f", elapsed, sleep_for)
            try:
                await asyncio.wait_for(stop.wait(), timeout=sleep_for)
            except TimeoutError:
                pass
    finally:
        mqtt.close()
        influx.close()
        LOG.info("collector_stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
