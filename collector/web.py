"""Simple web UI for sensor readings."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from database import SensorDatabase

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(db: SensorDatabase, config_error: str | None = None) -> FastAPI:
    app = FastAPI(title="SwitchBot Sensors", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/latest")
    def api_latest() -> list[dict]:
        return [reading.__dict__ for reading in db.latest_per_sensor()]

    @app.get("/api/history")
    def api_history(
        hours: int = Query(default=24, ge=1, le=24 * 365),
        mac: str | None = None,
    ) -> list[dict]:
        return [reading.__dict__ for reading in db.history(hours=hours, mac=mac)]

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, hours: int = Query(default=24, ge=1, le=24 * 14)):
        latest = db.latest_per_sensor()
        history = db.history(hours=hours)
        series: dict[str, dict[str, list]] = {}
        for row in history:
            bucket = series.setdefault(
                row.name,
                {"labels": [], "temperature": [], "humidity": []},
            )
            bucket["labels"].append(row.recorded_at)
            bucket["temperature"].append(row.temperature_c)
            bucket["humidity"].append(row.humidity_pct)

        return TEMPLATES.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "latest": latest,
                "series": series,
                "hours": hours,
                "config_error": config_error,
            },
        )

    return app
