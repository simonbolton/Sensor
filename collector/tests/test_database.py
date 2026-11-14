from pathlib import Path

from database import SensorDatabase


def test_insert_latest_and_history(tmp_path: Path) -> None:
    db = SensorDatabase(tmp_path / "sensors.db", retention_days=730)
    db.insert_reading(
        name="outdoor",
        mac="AA:BB:CC:DD:EE:FF",
        temperature_c=21.5,
        humidity_pct=55,
        battery_pct=90,
        dewpoint_c=12.0,
    )
    db.insert_reading(
        name="garage",
        mac="11:22:33:44:55:66",
        temperature_c=18.0,
        humidity_pct=60,
        battery_pct=80,
        dewpoint_c=10.0,
    )

    latest = db.latest_per_sensor()
    assert len(latest) == 2
    assert {row.name for row in latest} == {"outdoor", "garage"}

    history = db.history(hours=24)
    assert len(history) == 2
