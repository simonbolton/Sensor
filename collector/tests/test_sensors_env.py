import pytest

from app import parse_sensors_env


def test_parse_sensors_env_comma_separated() -> None:
    sensors = parse_sensors_env(
        "outdoor=AA:BB:CC:DD:EE:FF,garage=11:22:33:44:55:66"
    )
    assert set(sensors) == {"AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"}
    assert sensors["AA:BB:CC:DD:EE:FF"].name == "outdoor"
    assert sensors["11:22:33:44:55:66"].name == "garage"


def test_parse_sensors_env_semicolon_and_lowercase_mac() -> None:
    sensors = parse_sensors_env("shed=aa:bb:cc:dd:ee:ff")
    assert "AA:BB:CC:DD:EE:FF" in sensors
    assert sensors["AA:BB:CC:DD:EE:FF"].name == "shed"


def test_parse_sensors_env_rejects_bad_entry() -> None:
    with pytest.raises(RuntimeError, match="Expected name="):
        parse_sensors_env("outdoor-only")
