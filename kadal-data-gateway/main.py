"""Standalone live model-data gateway. No Kadal runtime dependencies."""
import asyncio
from datetime import datetime, timezone
import math

import httpx
from fastapi import FastAPI, HTTPException, Query, Response

app = FastAPI(title="Kadal Data Gateway", version="1.0.0")
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_FIELDS = ("wave_height", "wave_period", "wave_direction")
WEATHER_FIELDS = ("wind_speed_10m", "wind_direction_10m")


def hourly_rows(payload, fields):
    hourly = payload["hourly"]
    times = hourly["time"]
    if not isinstance(times, list) or not times:
        raise ValueError("Missing hourly times")
    if any(not isinstance(hourly[f], list) or len(hourly[f]) != len(times) for f in fields):
        raise ValueError("Incomplete hourly arrays")
    rows = {}
    for i, timestamp in enumerate(times):
        stamp = datetime.fromisoformat(timestamp)
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        if stamp in rows:
            raise ValueError("Duplicate timestamp")
        rows[stamp] = [hourly[f][i] for f in fields]
    return rows


def current_values(marine, weather, now):
    m = hourly_rows(marine, MARINE_FIELDS)
    w = hourly_rows(weather, WEATHER_FIELDS)
    # Require the same current hour in both sources, never a future or stale row.
    stamp = now.replace(minute=0, second=0, microsecond=0)
    values = [*m[stamp], *w[stamp]]
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Missing or invalid current value")
    if values[2] > 360 or values[4] > 360:
        raise ValueError("Invalid direction")
    return dict(zip(("wave_m", "wave_period_s", "wave_direction", "wind_ms", "wind_direction"), values))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/device/data")
async def device_data(response: Response,
                      latitude: float = Query(ge=7, le=14, allow_inf_nan=False),
                      longitude: float = Query(ge=77, le=81, allow_inf_nan=False)):
    params = {"latitude": latitude, "longitude": longitude, "past_hours": 6,
              "forecast_hours": 1, "timezone": "GMT"}
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8)) as client:
                marine, weather = await asyncio.gather(
                    client.get(MARINE_URL, params={**params, "hourly": ",".join(MARINE_FIELDS), "cell_selection": "sea"}),
                    client.get(WEATHER_URL, params={**params, "hourly": ",".join(WEATHER_FIELDS), "wind_speed_unit": "ms"}),
                )
            marine.raise_for_status()
            weather.raise_for_status()
            now = datetime.now(timezone.utc)
            values = current_values(marine.json(), weather.json(), now)
    except (httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise HTTPException(503, "Live marine/weather data is unavailable or incomplete. Please retry.",
                            headers={"Retry-After": "60", "Cache-Control": "no-store"}) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"latitude": latitude, "longitude": longitude,
            "fetched_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            **values, "data_type": "model_estimate",
            "source": "Open-Meteo marine and weather numerical forecasts"}
