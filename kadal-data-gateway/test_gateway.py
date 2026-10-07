"""Offline regression checks; fixtures are test-only, never served by the app."""
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch

import httpx
import main

RealClient = httpx.AsyncClient


def payload(fields):
    stamp = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return {"hourly": {"time": [(stamp-timedelta(hours=1)).isoformat(), stamp.isoformat()],
                       **{field: [1.0, 2.0] for field in fields}}}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
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
                with self.assertLogs("kadal.gateway", level="ERROR"):
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
        with self.assertLogs("kadal.gateway", level="ERROR") as logs:
            response = await self.request(upstream)
        self.assertEqual(response.status_code, 503)
        self.assertIn("No common", " ".join(logs.output))

    async def test_http_failure_logs_both_sources_without_client_details(self):
        def upstream(request):
            if request.url.host.startswith("marine"):
                return httpx.Response(429, json={"reason": "test upstream limit"})
            return httpx.Response(200, json=payload(main.WEATHER_FIELDS))
        with self.assertLogs("kadal.gateway", level="ERROR") as logs:
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


if __name__ == "__main__":
    unittest.main()
