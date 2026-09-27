"""Live forecast adapter. Missing or old data never becomes simulated live data."""
import asyncio
from datetime import datetime, timezone, timedelta
import time

import httpx
import numpy as np

UTC = timezone.utc


class DataUnavailable(ValueError):
    pass


class OceanProvider:
    def __init__(self):
        self.cache = {}
        self.lock = asyncio.Lock()

    async def fetch(self, points):
        key = tuple((round(float(a), 5), round(float(b), 5)) for a, b in points)
        # Lock prevents duplicate bursts to upstream on concurrent identical requests.
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 600:
                return cached[1]
            params = {"latitude": ",".join(str(p[0]) for p in key), "longitude": ",".join(str(p[1]) for p in key),
                      "past_hours": 6, "forecast_hours": 49, "timezone": "GMT"}
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    marine, weather = await asyncio.gather(
                        client.get("https://marine-api.open-meteo.com/v1/marine", params={**params, "hourly": "wave_height,wave_period,wave_direction", "cell_selection": "sea"}),
                        client.get("https://api.open-meteo.com/v1/forecast", params={**params, "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms"}),
                    )
                marine.raise_for_status()
                weather.raise_for_status()
                m, w = marine.json(), weather.json()
                m, w = (m if isinstance(m, list) else [m]), (w if isinstance(w, list) else [w])
                if len(m) != len(key) or len(w) != len(key):
                    raise DataUnavailable("Forecast provider returned incomplete locations.")
                now = datetime.now(UTC)
                rows = [self.parse(a, b, p, now) for a, b, p in zip(m, w, key)]
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise DataUnavailable("Live ocean data is unavailable or incomplete. Please retry; route advice is paused.") from exc
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic(), rows)
            return rows

    @staticmethod
    def parse(marine, weather, point, now):
        mh, wh = marine["hourly"], weather["hourly"]
        wind = dict(zip(wh["time"], wh["wind_speed_10m"]))
        directions = dict(zip(wh["time"], wh["wind_direction_10m"]))
        hourly = []
        for i, ts in enumerate(mh["time"]):
            dt = datetime.fromisoformat(ts).replace(tzinfo=UTC)
            wave, speed = mh["wave_height"][i], wind.get(ts)
            if wave is None or speed is None or not np.isfinite(wave) or not np.isfinite(speed) or wave < 0 or speed < 0:
                continue
            hourly.append({"time": dt.isoformat(), "wave_m": float(wave), "wind_ms": float(speed),
                           "period_s": mh["wave_period"][i], "wave_direction": mh["wave_direction"][i], "wind_direction": directions.get(ts)})
        current = [h for h in hourly if datetime.fromisoformat(h["time"]) <= now]
        if not current or now - datetime.fromisoformat(current[-1]["time"]) > timedelta(hours=1.5):
            raise DataUnavailable("No recent forecast time is available.")
        return {"latitude": point[0], "longitude": point[1], "fetched_at": now.isoformat(),
                "source": "Open-Meteo marine and weather numerical forecasts", "data_type": "model_estimate",
                "marine_grid_point": {"latitude": marine["latitude"], "longitude": marine["longitude"]},
                "current": current[-1], "hourly": hourly}


provider = OceanProvider()
