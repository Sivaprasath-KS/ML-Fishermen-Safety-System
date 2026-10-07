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
                response = await self.request(upstream)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("wave_m", response.json())


if __name__ == "__main__":
    unittest.main()
