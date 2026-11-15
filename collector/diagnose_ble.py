#!/usr/bin/env python3
"""Scan for a SwitchBot MAC and print raw ads + decode attempts."""

from __future__ import annotations

import argparse
import asyncio
import sys

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from decode import decode_switchbot_advertisement


def normalize_mac(value: str) -> str:
    return value.replace(":", "").replace("-", "").upper()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose SwitchBot BLE advertisements")
    parser.add_argument("mac", help="Sensor BLE MAC, e.g. F2:A9:C0:86:55:21")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    target = normalize_mac(args.mac)

    print(f"scanning for {args.mac} for {args.timeout}s (active scan)...")
    seen = 0
    decoded = 0

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal seen, decoded
        if normalize_mac(device.address) != target:
            return
        seen += 1
        manu = {
            f"0x{company:04x}": data.hex()
            for company, data in (adv.manufacturer_data or {}).items()
        }
        service = {
            str(uuid): data.hex() for uuid, data in (adv.service_data or {}).items()
        }
        reading = decode_switchbot_advertisement(
            adv.manufacturer_data, adv.service_data
        )
        print(
            f"\n[{seen}] address={device.address} name={adv.local_name!r} "
            f"rssi={getattr(adv, 'rssi', None)}"
        )
        print(f"  manufacturer_data={manu}")
        print(f"  service_data={service}")
        if reading is None:
            print("  decode=FAILED")
        else:
            decoded += 1
            print(f"  decode=OK {reading.as_dict()}")

    scanner = BleakScanner(detection_callback=callback, scanning_mode="active")
    await scanner.start()
    try:
        await asyncio.sleep(args.timeout)
    finally:
        await scanner.stop()

    print(f"\nsummary seen={seen} decoded={decoded}")
    if seen == 0:
        print(
            "No advertisements from that MAC.\n"
            "Check: bluetoothctl scan, correct MAC, sensor in range, "
            "stop other BLE scanners."
        )
        return 2
    if decoded == 0:
        print(
            "Saw the MAC but could not decode payloads.\n"
            "Paste this full output so the decoder can be adjusted."
        )
        return 3
    print("Decode succeeded — restart the sensor service after updating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
