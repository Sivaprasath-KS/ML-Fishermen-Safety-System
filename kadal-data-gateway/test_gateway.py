"""Offline regression checks; fixtures are test-only, never served by the app."""
from datetime import datetime, timezone, timedelta
import unittest
import asyncio
from unittest.mock import patch

import httpx
import main

RealClient = httpx.AsyncClient


def payload(fields):
    stamp = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return {"hourly": {"time": [(stamp-timedelta(hours=1)).isoformat(), stamp.isoformat()],
                       **{field: [1.0, 2.0] for field in fields}}}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.cache.clear()
        main.inflight.clear()
        main.provider_cooldowns.clear()
        main.upstream_slots = asyncio.Semaphore(4)
        self.tick = 1000.0
        self.sleeps = []
        self.clock_patch = patch.object(main, "monotonic", side_effect=lambda: self.tick)
        self.clock_patch.start()
        async def fake_sleep(delay):
            self.sleeps.append(delay)
            self.tick += delay
        self.sleep_patch = patch.object(main, "sleep", side_effect=fake_sleep)
        self.sleep_patch.start()
        self.jitter_patch = patch.object(main.random, "uniform", return_value=0)
        self.jitter_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.jitter_patch.stop)

    async def request(self, handler, query="latitude=10.9&longitude=80.2", path="/api/device/data"):
        transport = httpx.MockTransport(handler)
        def upstream(**kwargs):
            return RealClient(transport=transport, **kwargs)
        async with RealClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            with patch.object(main.httpx, "AsyncClient", side_effect=upstream):
                return await client.get(path + "?" + query)

    async def test_health_and_validation_do_not_fetch(self):
        def forbidden(request):
            self.fail("Invalid request must not fetch upstream")
        self.assertEqual((await self.request(forbidden, path="/api/health")).json(), {"status": "ok"})
        for query in ["", "latitude=6&longitude=80", "latitude=15&longitude=80", "latitude=10&longitude=76",
                      "latitude=10&longitude=82", "latitude=nan&longitude=80", "latitude=10&longitude=inf", "latitude=abc&longitude=80"]:
            with self.subTest(query=query):
                self.assertEqual((await self.request(forbidden, query)).status_code, 422)

    async def test_compact_real_schema_and_time_alignment(self):
        def upstream(request):
            self.assertEqual(request.url.params["past_hours"], "6")
            self.assertEqual(request.url.params["forecast_hours"], "1")
            self.assertEqual(request.url.params["timezone"], "GMT")
            marine = request.url.host.startswith("marine")
            fields = main.MARINE_FIELDS if marine else main.WEATHER_FIELDS
            self.assertEqual(request.url.params["hourly"], ",".join(fields))
            self.assertEqual(request.url.params["cell_selection" if marine else "wind_speed_unit"], "sea" if marine else "ms")
            data = payload(fields)
            if not marine:
                for values in data["hourly"].values(): values.reverse()
            return httpx.Response(200, json=data)
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.content), 512)
        self.assertEqual(response.json()["wind_ms"], 2)
        self.assertEqual(response.json()["data_type"], "model_estimate")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(b"\n", response.content)

    async def test_upstream_failures(self):
        for mode in ["timeout", "http", "json", "missing", "null", "short", "stale", "direction", "negative"]:
            def upstream(request):
                if mode == "timeout": raise httpx.ReadTimeout("test", request=request)
                if mode == "http": return httpx.Response(429)
                if mode == "json": return httpx.Response(200, text="not JSON")
                fields = main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS
                data = payload(fields)
                if mode == "missing": del data["hourly"][fields[0]]
                if mode == "null": data["hourly"][fields[0]][-1] = None
                if mode == "short": data["hourly"][fields[0]].pop()
                if mode == "stale":
                    data["hourly"]["time"] = ["2000-01-01T00:00", "2000-01-01T01:00"]
                if mode == "direction": data["hourly"][fields[-1]][-1] = 400
                if mode == "negative": data["hourly"][fields[0]][-1] = -1
                return httpx.Response(200, json=data)
            with self.subTest(mode=mode):
                main.provider_cooldowns.clear()
                with self.assertLogs(main.logger.name, level="ERROR"):
                    response = await self.request(upstream)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("wave_m", response.json())


    async def test_different_latest_timestamps_use_latest_common(self):
        def upstream(request):
            marine = request.url.host.startswith("marine")
            fields = main.MARINE_FIELDS if marine else main.WEATHER_FIELDS
            data = payload(fields)
            if marine:
                for values in data["hourly"].values(): values.pop()
            return httpx.Response(200, json=data)
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["wave_m"], 1)
        self.assertEqual(response.json()["wind_ms"], 1)

    async def test_no_common_timestamp(self):
        def upstream(request):
            marine = request.url.host.startswith("marine")
            data = payload(main.MARINE_FIELDS if marine else main.WEATHER_FIELDS)
            if not marine:
                data["hourly"]["time"] = [(datetime.fromisoformat(t) + timedelta(minutes=10)).isoformat()
                                         for t in data["hourly"]["time"]]
            return httpx.Response(200, json=data)
        with self.assertLogs(main.logger.name, level="ERROR") as logs:
            response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertIn("No common", " ".join(logs.output))

    async def test_http_failure_logs_both_sources_without_client_details(self):
        def upstream(request):
            if request.url.host.startswith("marine"):
                return httpx.Response(429, json={"reason": "test upstream limit"})
            return httpx.Response(200, json=payload(main.WEATHER_FIELDS))
        with self.assertLogs(main.logger.name, level="ERROR") as logs:
            response = await self.request(upstream)
        message = " ".join(logs.output)
        for expected in ["HTTPStatusError", "marine_status=429", "weather_status=200",
                         "marine_timestamps=", "weather_timestamps="]:
            self.assertIn(expected, message)
        self.assertEqual(response.json(), {"detail": "Live marine/weather data is unavailable or incomplete. Please retry."})

    def test_time_window_future_and_timezone_normalization(self):
        now = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)
        def rows(fields, times):
            return {"hourly": {"time": times, **{f: list(range(1, len(times)+1)) for f in fields}}}
        times = ["2026-10-07T10:30:00Z", "2026-10-07T12:00:00Z", "2026-10-07T13:00:00Z"]
        m = rows(main.MARINE_FIELDS, times)
        w = rows(main.WEATHER_FIELDS, ["2026-10-07T16:00:00+05:30", "2026-10-07T17:30:00+05:30", "2026-10-07T18:30:00+05:30"])
        self.assertEqual(main.current_values(m, w, now)["wave_m"], 2)
        for timestamp, accepted in [("2026-10-07T10:30:00Z", True), ("2026-10-07T10:29:59Z", False), ("2026-10-07T13:00:00Z", False)]:
            m, w = rows(main.MARINE_FIELDS, [timestamp]), rows(main.WEATHER_FIELDS, [timestamp])
            if accepted:
                self.assertEqual(main.current_values(m, w, now)["wave_m"], 1)
            else:
                with self.assertRaises(ValueError): main.current_values(m, w, now)


    async def test_cache_keys_ttl_and_schema(self):
        calls = []
        def upstream(request):
            calls.append(request)
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        first = await self.request(upstream)
        self.assertEqual(set(first.json()), {"latitude", "longitude", "fetched_at", "wave_m", "wave_period_s", "wave_direction", "wind_ms", "wind_direction", "data_type", "source"})
        second = await self.request(upstream, "latitude=10.9000&longitude=80.200")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(calls), 2)
        self.assertEqual((await self.request(upstream, "latitude=10&longitude=80.5")).status_code, 200)
        self.assertEqual(len(calls), 4)
        self.tick += main.CACHE_TTL
        self.assertEqual((await self.request(upstream)).status_code, 200)
        self.assertEqual(len(calls), 6)

    async def test_concurrent_requests_share_success_and_failure(self):
        for status in [200, 400]:
            main.cache.clear()
            calls = []
            async def upstream(request):
                calls.append(request)
                await asyncio.sleep(0)
                return httpx.Response(status, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
            transport = httpx.MockTransport(upstream)
            async with RealClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                with patch.object(main.httpx, "AsyncClient", side_effect=lambda **kw: RealClient(transport=transport, **kw)):
                    results = await asyncio.gather(*[client.get('/api/device/data?latitude=10.9&longitude=80.2') for _ in range(12)])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(r.status_code == (200 if status == 200 else 503) for r in results))
            self.assertFalse(main.inflight)
            if status == 400: self.assertFalse(main.cache)

    async def test_retry_after_short_delay_then_success(self):
        counts = {"marine": 0, "weather": 0}
        def upstream(request):
            name = "marine" if request.url.host.startswith("marine") else "weather"
            counts[name] += 1
            if name == "weather" and counts[name] == 1:
                return httpx.Response(429, headers={"Retry-After": "3"}, json={"reason": "slow down"})
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if name == "marine" else main.WEATHER_FIELDS))
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(counts, {"marine": 1, "weather": 2})
        self.assertEqual(self.sleeps, [3])

    async def test_persistent_429_is_bounded(self):
        counts = []
        def upstream(request):
            counts.append(request.url.host)
            if request.url.host.startswith("marine"):
                return httpx.Response(200, json=payload(main.MARINE_FIELDS))
            return httpx.Response(429, headers={"Retry-After": "1"})
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(counts.count("api.open-meteo.com"), main.MAX_ATTEMPTS)
        self.assertEqual(self.sleeps, [1, 2])
        self.assertFalse(main.cache)
        before = len(counts)
        self.assertEqual((await self.request(upstream)).status_code, 503)
        self.assertEqual(len(counts), before)

    async def test_long_retry_after_cooldown_blocks_other_locations(self):
        calls = []
        def upstream(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "120"})
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 120)
        self.assertEqual(self.sleeps, [])
        count = len(calls)
        self.assertEqual((await self.request(upstream, "latitude=11&longitude=80")).status_code, 503)
        self.assertEqual(len(calls), count)
        self.assertEqual((await self.request(upstream, path="/api/health")).json(), {"status": "ok"})

    def test_retry_after_http_date_and_bad_header(self):
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        with patch.object(main, "utcnow", return_value=now):
            self.assertEqual(main.retry_after_seconds("Sun, 18 Oct 2026 12:02:00 GMT"), 120)
        self.assertIsNone(main.retry_after_seconds("not a date"))
        self.assertIsNone(main.retry_after_seconds(None))

    async def test_cached_data_cannot_age_past_safety_limit(self):
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        calls = []
        def upstream(request):
            calls.append(request)
            fields = main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS
            return httpx.Response(200, json={"hourly": {"time": ["2026-10-18T10:01:00Z"], **{f: [1] for f in fields}}})
        with patch.object(main, "utcnow", return_value=now):
            self.assertEqual((await self.request(upstream)).status_code, 200)
        with patch.object(main, "utcnow", return_value=now+timedelta(minutes=2)):
            self.assertEqual((await self.request(upstream)).status_code, 503)
        self.assertEqual(len(calls), 4)
        self.assertFalse(main.cache)

    async def test_cache_size_bounded(self):
        def upstream(request):
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        with patch.object(main, "CACHE_LIMIT", 2):
            for lat in [10, 11, 12]:
                self.assertEqual((await self.request(upstream, f"latitude={lat}&longitude=80")).status_code, 200)
            self.assertEqual(len(main.cache), 2)
            self.assertNotIn((10.0, 80.0), main.cache)

    def test_log_body_redacts_and_truncates(self):
        response = httpx.Response(429, text='{"token":"privatevalue", "reason":"limit"}' + 'x'*1000)
        self.assertNotIn("privatevalue", main.safe_body(response))
        self.assertLessEqual(len(main.safe_body(response)), 256)


    async def test_upstream_concurrency_limit(self):
        active = 0
        peak = 0
        async def upstream(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        transport = httpx.MockTransport(upstream)
        async with RealClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            with patch.object(main.httpx, "AsyncClient", side_effect=lambda **kw: RealClient(transport=transport, **kw)):
                responses = await asyncio.gather(*[client.get(f'/api/device/data?latitude={10+i/10}&longitude=80') for i in range(10)])
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertLessEqual(peak, 4)

    async def test_total_timeout_and_cleanup(self):
        async def upstream(request):
            await asyncio.Event().wait()
        with patch.object(main, "REQUEST_BUDGET", .01):
            response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(main.inflight)
        self.assertFalse(main.cache)

    async def test_non_retryable_failure_not_cached(self):
        calls = []
        status = 400
        def upstream(request):
            calls.append(request)
            return httpx.Response(status, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        self.assertEqual((await self.request(upstream)).status_code, 503)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [])
        self.assertFalse(main.cache)
        status = 200
        self.assertEqual((await self.request(upstream)).status_code, 200)
        self.assertEqual(len(calls), 4)

    async def test_retryable_server_failure_then_success(self):
        calls = []
        def upstream(request):
            calls.append(request.url.host)
            if request.url.host.startswith("marine") and calls.count(request.url.host) == 1:
                return httpx.Response(503, headers={"Retry-After": "2"})
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        self.assertEqual((await self.request(upstream)).status_code, 200)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleeps, [2])

    async def test_cached_success_available_during_provider_cooldown(self):
        def upstream(request):
            return httpx.Response(200, json=payload(main.MARINE_FIELDS if request.url.host.startswith("marine") else main.WEATHER_FIELDS))
        first = await self.request(upstream)
        main.provider_cooldowns["weather"] = self.tick + 120
        def forbidden(request):
            self.fail("Fresh cache must avoid upstream even during cooldown")
        self.assertEqual((await self.request(forbidden)).json(), first.json())


    async def test_429_without_retry_header_does_not_retry(self):
        calls = []
        def upstream(request):
            calls.append(request)
            return httpx.Response(429)
        response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleeps, [])
        self.assertGreaterEqual(int(response.headers["retry-after"]), 60)
        self.assertFalse(main.cache)
        self.assertEqual((await self.request(upstream)).status_code, 503)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
