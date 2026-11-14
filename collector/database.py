"""SQLite storage for sensor readings."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class StoredReading:
    id: int
    name: str
    mac: str
    temperature_c: float
    humidity_pct: float
    battery_pct: int | None
    dewpoint_c: float | None
    recorded_at: str


class SensorDatabase:
    def __init__(self, path: Path, retention_days: int = 730):
        self._path = path
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS readings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        mac TEXT NOT NULL,
                        temperature_c REAL NOT NULL,
                        humidity_pct REAL NOT NULL,
                        battery_pct INTEGER,
                        dewpoint_c REAL,
                        recorded_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_readings_recorded_at
                    ON readings (recorded_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_readings_mac_recorded_at
                    ON readings (mac, recorded_at)
                    """
                )
                conn.commit()

    def insert_reading(
        self,
        *,
        name: str,
        mac: str,
        temperature_c: float,
        humidity_pct: float,
        battery_pct: int | None,
        dewpoint_c: float | None,
        recorded_at: datetime | None = None,
    ) -> None:
        ts = (recorded_at or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO readings (
                        name, mac, temperature_c, humidity_pct,
                        battery_pct, dewpoint_c, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, mac, temperature_c, humidity_pct, battery_pct, dewpoint_c, ts),
                )
                conn.commit()

    def latest_per_sensor(self) -> list[StoredReading]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT r.*
                    FROM readings r
                    INNER JOIN (
                        SELECT mac, MAX(id) AS max_id
                        FROM readings
                        GROUP BY mac
                    ) latest ON r.id = latest.max_id
                    ORDER BY r.name COLLATE NOCASE
                    """
                ).fetchall()
        return [self._row_to_reading(row) for row in rows]

    def history(self, *, hours: int = 24, mac: str | None = None) -> list[StoredReading]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query = """
            SELECT *
            FROM readings
            WHERE recorded_at >= ?
        """
        params: list[object] = [since]
        if mac:
            query += " AND mac = ?"
            params.append(mac.upper())
        query += " ORDER BY recorded_at ASC"
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        return [self._row_to_reading(row) for row in rows]

    def purge_expired(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM readings WHERE recorded_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return int(cursor.rowcount)

    @staticmethod
    def _row_to_reading(row: sqlite3.Row) -> StoredReading:
        return StoredReading(
            id=int(row["id"]),
            name=str(row["name"]),
            mac=str(row["mac"]),
            temperature_c=float(row["temperature_c"]),
            humidity_pct=float(row["humidity_pct"]),
            battery_pct=None if row["battery_pct"] is None else int(row["battery_pct"]),
            dewpoint_c=None if row["dewpoint_c"] is None else float(row["dewpoint_c"]),
            recorded_at=str(row["recorded_at"]),
        )
