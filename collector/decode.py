"""Decode SwitchBot meter BLE advertisement payloads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

# SwitchBot company ID (0x0969) and outdoor / meter service UUID.
SWITCHBOT_COMPANY_ID = 0x0969
SWITCHBOT_SERVICE_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class SensorReading:
    temperature_c: float
    humidity_pct: float
    battery_pct: int | None = None
    dewpoint_c: float | None = None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "battery_pct": self.battery_pct,
            "dewpoint_c": self.dewpoint_c,
        }


def dewpoint_celsius(temperature_c: float, humidity_pct: float) -> float:
    """Magnus approximation for dew point (°C)."""
    rh = max(0.01, min(100.0, humidity_pct))
    a = 17.625
    b = 243.04
    alpha = ((a * temperature_c) / (b + temperature_c)) + math.log(rh / 100.0)
    return (b * alpha) / (a - alpha)


def _decode_temp_humidity_from_bytes(
    temp_frac_byte: int, temp_int_byte: int, humidity_byte: int
) -> tuple[float, float]:
    temperature = (temp_frac_byte & 0x0F) * 0.1 + float(temp_int_byte & 0x7F)
    if (temp_int_byte & 0x80) == 0:
        temperature = -temperature
    humidity = float(humidity_byte & 0x7F)
    return temperature, humidity


def decode_switchbot_advertisement(
    manufacturer_data: Mapping[int, bytes] | None,
    service_data: Mapping[str, bytes] | None,
) -> SensorReading | None:
    """
    Decode SwitchBot IP65 Outdoor Meter (and compatible meters) ads.

    Manufacturer payload (after company ID) uses indices 8/9/10 for
    temperature fraction, temperature integer/sign, and humidity.
    Battery is in service UUID fd3d, byte 2 (bit6-0).
    """
    manufacturer_data = manufacturer_data or {}
    service_data = service_data or {}

    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None

    manu = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    if manu is not None and len(manu) >= 11:
        temperature, humidity = _decode_temp_humidity_from_bytes(
            manu[8], manu[9], manu[10]
        )

    # Normalise UUID keys (with/without dashes, upper/lower).
    service_payload: bytes | None = None
    for key, value in service_data.items():
        normalised = key.lower().replace("-", "")
        if normalised in {
            "0000fd3d00001000800000805f9b34fb",
            "fd3d",
        } or key.lower() == SWITCHBOT_SERVICE_UUID:
            service_payload = value
            break

    if service_payload is not None and len(service_payload) >= 3:
        battery = int(service_payload[2] & 0x7F)
        # Classic meters put temp/humidity in service data when manufacturer
        # payload is missing or short.
        if temperature is None and len(service_payload) >= 6:
            temperature, humidity = _decode_temp_humidity_from_bytes(
                service_payload[3], service_payload[4], service_payload[5]
            )

    if temperature is None or humidity is None:
        return None

    return SensorReading(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        battery_pct=battery,
        dewpoint_c=round(dewpoint_celsius(temperature, humidity), 1),
    )
