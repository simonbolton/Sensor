"""Decode SwitchBot meter BLE advertisement payloads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

# SwitchBot company ID (0x0969) and outdoor / meter service UUID.
SWITCHBOT_COMPANY_ID = 0x0969
SWITCHBOT_SERVICE_UUIDS = {
    "0000fd3d00001000800000805f9b34fb",
    "00000d0000001000800000805f9b34fb",
    "fd3d",
    "0d00",
}


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
) -> tuple[float, float] | None:
    temperature = (temp_frac_byte & 0x0F) * 0.1 + float(temp_int_byte & 0x7F)
    if (temp_int_byte & 0x80) == 0:
        temperature = -temperature
    humidity = float(humidity_byte & 0x7F)
    # Reject clearly impossible values from wrong offsets.
    if humidity > 100 or temperature < -40 or temperature > 80:
        return None
    return temperature, humidity


def _extract_service_payload(service_data: Mapping[str, bytes]) -> bytes | None:
    for key, value in service_data.items():
        normalised = key.lower().replace("-", "")
        if normalised in SWITCHBOT_SERVICE_UUIDS or normalised.endswith("fd3d") or normalised.endswith("0d00"):
            return value
    # Fall back to any short service payload that looks like SwitchBot device types.
    for value in service_data.values():
        if len(value) >= 3 and value[0] in {0x77, 0x54, 0x69}:
            return value
    return None


def _try_manufacturer_payload(payload: bytes) -> tuple[float, float] | None:
    """
    Try known SwitchBot outdoor / meter layouts.

    Bleak manufacturer payloads usually exclude the 2-byte company ID, so
    temp/humidity sit at indexes 8/9/10. Some stacks include the company ID,
    shifting values to 10/11/12.
    """
    candidates: list[tuple[int, int, int]] = []
    # Only use the shifted layout when the payload looks like it still includes
    # the SwitchBot company ID bytes (0x69 0x09).
    includes_company_id = (
        len(payload) >= 13 and payload[0] == 0x69 and payload[1] == 0x09
    )
    if includes_company_id:
        candidates.append((10, 11, 12))
    if len(payload) >= 11:
        candidates.append((8, 9, 10))

    for frac_i, int_i, hum_i in candidates:
        decoded = _decode_temp_humidity_from_bytes(
            payload[frac_i], payload[int_i], payload[hum_i]
        )
        if decoded is not None:
            return decoded
    return None


def decode_switchbot_advertisement(
    manufacturer_data: Mapping[int, bytes] | None,
    service_data: Mapping[str, bytes] | None,
) -> SensorReading | None:
    """
    Decode SwitchBot IP65 Outdoor Meter (and compatible meters) ads.

    Prefers SwitchBot company ID 0x0969, but will try any manufacturer
    payload and service-data fallbacks used by meter variants.
    """
    manufacturer_data = manufacturer_data or {}
    service_data = service_data or {}

    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None

    payloads: list[bytes] = []
    preferred = manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    if preferred is not None:
        payloads.append(preferred)
    for company_id, payload in manufacturer_data.items():
        if company_id == SWITCHBOT_COMPANY_ID:
            continue
        payloads.append(payload)

    for payload in payloads:
        decoded = _try_manufacturer_payload(payload)
        if decoded is not None:
            temperature, humidity = decoded
            break

    service_payload = _extract_service_payload(service_data)
    if service_payload is not None and len(service_payload) >= 3:
        battery = int(service_payload[2] & 0x7F)
        if temperature is None and len(service_payload) >= 6:
            decoded = _decode_temp_humidity_from_bytes(
                service_payload[3], service_payload[4], service_payload[5]
            )
            if decoded is not None:
                temperature, humidity = decoded

    if temperature is None or humidity is None:
        return None

    return SensorReading(
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        battery_pct=battery,
        dewpoint_c=round(dewpoint_celsius(temperature, humidity), 1),
    )
