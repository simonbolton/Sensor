from decode import decode_switchbot_advertisement, dewpoint_celsius


def test_decode_outdoor_meter_manufacturer_and_battery() -> None:
    # Manufacturer payload after company ID (indices 8/9/10 = temp/humidity).
    # temp = 0.2 + 22 = 22.2, humidity = 55, sign positive (bit7 set).
    manu = bytearray(12)
    manu[8] = 0x02
    manu[9] = 0x80 | 22
    manu[10] = 55
    service = bytes([0x77, 0x00, 0x64])  # battery 100%

    reading = decode_switchbot_advertisement(
        {0x0969: bytes(manu)},
        {"0000fd3d-0000-1000-8000-00805f9b34fb": service},
    )

    assert reading is not None
    assert reading.temperature_c == 22.2
    assert reading.humidity_pct == 55.0
    assert reading.battery_pct == 100
    assert reading.dewpoint_c is not None


def test_decode_negative_temperature() -> None:
    manu = bytearray(12)
    manu[8] = 0x05
    manu[9] = 3  # sign bit clear => negative
    manu[10] = 40

    reading = decode_switchbot_advertisement({0x0969: bytes(manu)}, {})
    assert reading is not None
    assert reading.temperature_c == -3.5
    assert reading.humidity_pct == 40.0
    assert reading.battery_pct is None


def test_decode_returns_none_without_payload() -> None:
    assert decode_switchbot_advertisement({}, {}) is None


def test_dewpoint_reasonable() -> None:
    assert 9.0 < dewpoint_celsius(20.0, 50.0) < 10.0
