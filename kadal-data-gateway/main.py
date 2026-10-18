"""Standalone live model-data gateway. No Kadal runtime dependencies."""
import asyncio
from datetime import datetime, timedelta, timezone
import math
import logging
from collections import OrderedDict
from email.utils import parsedate_to_datetime
import random
import re
from time import monotonic

import httpx
from fastapi import FastAPI, HTTPException, Query, Response

app = FastAPI(title="Kadal Data Gateway", version="1.0.0")
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_FIELDS = ("wave_height", "wave_period", "wave_direction")
MAX_DATA_AGE = timedelta(hours=2)
logger = logging.getLogger("uvicorn.error.kadal.gateway")
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


def aligned_values(marine, weather, now):
    m = hourly_rows(marine, MARINE_FIELDS)
    w = hourly_rows(weather, WEATHER_FIELDS)
    common = [stamp for stamp in m.keys() & w.keys()
              if now - MAX_DATA_AGE <= stamp <= now]
    if not common:
        raise ValueError("No common marine/weather timestamp within the last 2 hours")
    stamp = max(common)
    values = [*m[stamp], *w[stamp]]
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Missing or invalid current value")
    if values[2] > 360 or values[4] > 360:
        raise ValueError("Invalid direction")
    return dict(zip(("wave_m", "wave_period_s", "wave_direction", "wind_ms", "wind_direction"), values)), stamp


def current_values(marine, weather, now):
    return aligned_values(marine, weather, now)[0]


CACHE_TTL = 600
CACHE_LIMIT = 128
MAX_INFLIGHT = 32
REQUEST_BUDGET = 25
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
cache = OrderedDict()
inflight = {}
provider_cooldowns = {}
upstream_slots = asyncio.Semaphore(4)
sleep = asyncio.sleep
ERROR_DETAIL = "Live marine/weather data is unavailable or incomplete. Please retry."


def utcnow():
    return datetime.now(timezone.utc)


def unavailable(retry_after=60):
    return HTTPException(503, ERROR_DETAIL, headers={
        "Retry-After": str(max(1, math.ceil(retry_after))), "Cache-Control": "no-store"})


def retry_after_seconds(value):
    if not value:
        return None
    try:
        if value.strip().isdigit():
            return int(value.strip())
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, (stamp - utcnow()).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def safe_body(response):
    # No request headers, credentials or full response documents in logs.
    text = response.text[:2048]
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'''(?ix)(["']?(?:api[_-]?key|token|secret|password|authorization|cookie|set-cookie)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,}&]+)''',
                  r'\1[redacted]', text)
    return " ".join(text.split())[:256]


async def fetch(client, name, url, query, diagnostics, deadline):
    for attempt in range(MAX_ATTEMPTS):
        # Cooldowns are provider-wide, including other locations on this process.
        async with upstream_slots:
            cooldown = provider_cooldowns.get(name, 0) - monotonic()
            if cooldown > 0:
                logger.warning("Retry skipped provider=%s cooldown=%.1fs", name, cooldown)
                raise unavailable(cooldown)
            logger.info("Upstream started provider=%s location=%s,%s attempt=%s", name,
                        query["latitude"], query["longitude"], attempt + 1)
            try:
                result = await client.get(url, params=query)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                logger.warning("Upstream transport failure provider=%s type=%s", name, type(exc).__name__)
                failure = exc
                delay = 2 ** attempt + random.uniform(0, 0.5)
            else:
                diagnostics[name + "_status"] = result.status_code
                logger.info("Upstream completed provider=%s status=%s", name, result.status_code)
                try:
                    times = result.json().get("hourly", {}).get("time", [])
                    diagnostics[name + "_timestamps"] = repr(times[-32:])[:1200] if isinstance(times, list) else "invalid"
                except (ValueError, AttributeError, TypeError):
                    diagnostics[name + "_timestamps"] = "unavailable"
                if result.is_success:
                    return result.json()
                logger.warning("Upstream HTTP failure provider=%s status=%s location=%s,%s body=%r", name,
                               result.status_code, query["latitude"], query["longitude"], safe_body(result))
                try:
                    result.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    failure = exc
                if result.status_code not in RETRYABLE_STATUS:
                    raise failure
                requested_delay = retry_after_seconds(result.headers.get("Retry-After"))
                delay = max(2 ** attempt + random.uniform(0, 0.5), requested_delay or 0)
                if requested_delay is not None and requested_delay > 0:
                    provider_cooldowns[name] = max(provider_cooldowns.get(name, 0), monotonic() + delay)
                if result.status_code == 429:
                    # Without provider guidance, do not hammer again in this request.
                    delay = max(delay, 60 if requested_delay is None else requested_delay)
                    provider_cooldowns[name] = max(provider_cooldowns.get(name, 0), monotonic() + delay)
        if attempt + 1 == MAX_ATTEMPTS or delay + 1 >= deadline - monotonic():
            logger.warning("Retry skipped provider=%s attempt=%s delay=%.1fs budget_remaining=%.1fs",
                           name, attempt + 1, delay, max(0, deadline - monotonic()))
            raise failure
        logger.info("Retry scheduled provider=%s delay=%.1fs attempt=%s", name, delay, attempt + 2)
        await sleep(delay)
    raise RuntimeError("Unreachable retry state")


async def load_data(key):
    latitude, longitude = key
    params = {"latitude": latitude, "longitude": longitude, "past_hours": 6,
              "forecast_hours": 1, "timezone": "GMT"}
    diagnostics = {}
    deadline = monotonic() + REQUEST_BUDGET
    try:
        async with asyncio.timeout(REQUEST_BUDGET):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8)) as client:
                results = await asyncio.gather(
                    fetch(client, "marine", MARINE_URL, {**params, "hourly": ",".join(MARINE_FIELDS), "cell_selection": "sea"}, diagnostics, deadline),
                    fetch(client, "weather", WEATHER_URL, {**params, "hourly": ",".join(WEATHER_FIELDS), "wind_speed_unit": "ms"}, diagnostics, deadline),
                    return_exceptions=True,
                )
            for result in results:
                if isinstance(result, Exception):
                    raise result
            now = utcnow()
            values, stamp = aligned_values(*results, now)
            data = {"latitude": latitude, "longitude": longitude,
                    "fetched_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    **values, "data_type": "model_estimate",
                    "source": "Open-Meteo marine and weather numerical forecasts"}
            cache[key] = (monotonic() + CACHE_TTL, stamp, data)
            cache.move_to_end(key)
            while len(cache) > CACHE_LIMIT:
                cache.popitem(last=False)
            logger.info("Successful validated response location=%s data_time=%s", key, stamp.isoformat())
            return data
    except (httpx.HTTPError, HTTPException, TimeoutError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        # Log exception type and bounded diagnostics, never HTTP headers or full URLs.
        message = str(exc) if not isinstance(exc, (httpx.HTTPError, HTTPException)) else "upstream unavailable"
        logger.error("Live data failed: %s: %s; marine_status=%s weather_status=%s; marine_timestamps=%s weather_timestamps=%s",
                     type(exc).__name__, message[:256], diagnostics.get("marine_status"),
                     diagnostics.get("weather_status"), diagnostics.get("marine_timestamps", "unavailable"),
                     diagnostics.get("weather_timestamps", "unavailable"))
        remaining = max([60, *(until - monotonic() for until in provider_cooldowns.values())])
        raise unavailable(remaining) from exc
    finally:
        inflight.pop(key, None)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/device/data")
async def device_data(response: Response,
                      latitude: float = Query(ge=7, le=14, allow_inf_nan=False),
                      longitude: float = Query(ge=77, le=81, allow_inf_nan=False)):
    # Parsed floats normalize equivalent textual coordinates without merging distinct
    # locations through rounding. All other upstream parameters are fixed constants.
    key = (latitude, longitude)
    now, tick = utcnow(), monotonic()
    for old_key, (expires, stamp, _) in list(cache.items()):
        if tick >= expires or not timedelta(0) <= now - stamp <= MAX_DATA_AGE:
            del cache[old_key]
    response.headers["Cache-Control"] = "no-store"
    if key in cache:
        cache.move_to_end(key)
        logger.info("Cache hit location=%s", key)
        return cache[key][2]  # Preserve original retrieval time, never relabel as freshly fetched.
    logger.info("Cache miss location=%s", key)
    task = inflight.get(key)
    if task is None:
        cooldown = max([0, *(until - tick for until in provider_cooldowns.values())])
        if cooldown > 0:
            logger.warning("Retry skipped location=%s provider cooldown=%.1fs", key, cooldown)
            raise unavailable(cooldown)
        if len(inflight) >= MAX_INFLIGHT:
            logger.warning("Upstream capacity reached")
            raise unavailable()
        # No await between lookup and insertion: atomic within this process's event loop.
        task = asyncio.create_task(load_data(key))
        inflight[key] = task
        # Retrieve exceptions even if all HTTP clients disconnect while the task runs.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    else:
        logger.info("Sharing in-flight request location=%s", key)
    return await asyncio.shield(task)
