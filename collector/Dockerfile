FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bluez \
        dbus \
        mosquitto \
        mosquitto-clients \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py decode.py database.py web.py ./
COPY templates ./templates
COPY mosquitto.conf /etc/mosquitto/mosquitto.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    APP_VERSION=2026-11-14-sensors-env \
    SQLITE_PATH=/data/sensors.db \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080 \
    MQTT_HOST=127.0.0.1 \
    MQTT_PORT=1883 \
    ENABLE_MQTT=true \
    RETENTION_DAYS=730

EXPOSE 8080 1883
VOLUME ["/data"]

ENTRYPOINT ["/docker-entrypoint.sh"]
