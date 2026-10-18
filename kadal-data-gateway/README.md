# Kadal Data Gateway

Independent Python 3.11+ service for ESP32 + SIM800L. Copy/deploy this folder alone.
It does not import, modify, or require Kadal's backend, frontend, artifacts, ML,
routing or boundary code. No predictions or route advice are produced.

## Files

- `main.py`: asynchronous FastAPI service, bounded upstream requests, timestamp alignment and validation.
- `requirements.txt`: only FastAPI, Uvicorn and HTTPX runtime dependencies (their normal dependencies are installed automatically).
- `esp32_kadal_gprs_test.ino`: standalone GPRS test client.
- `test_gateway.py`: offline standard-library unittest checks with test-only upstream fixtures.
- `README.md`: setup, deployment and device workflow.

## Local setup

From the repository root:

```bash
cd kadal-data-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

If the original Kadal app already occupies port 8000, use `--port 8001` here
and change the curl port. No need to stop or modify the original app.

In another terminal:

```bash
curl -i http://127.0.0.1:8000/api/health
curl -i 'http://127.0.0.1:8000/api/device/data?latitude=10.9&longitude=80.2'
curl -i 'http://127.0.0.1:8000/api/device/data?latitude=20&longitude=80.2'
```

Health returns `{"status":"ok"}`; it indicates service liveness, not upstream availability.
Data returns compact JSON with `latitude`, `longitude`, `fetched_at`, `wave_m`,
`wave_period_s`, `wave_direction`, `wind_ms`, `wind_direction`,
`data_type: "model_estimate"`, and
`source: "Open-Meteo marine and weather numerical forecasts"`.
Coordinates echo the requested location, not the provider's selected grid centre.
Units are metres, seconds, degrees and metres/second. Values are not fabricated,
interpolated by this service or replaced with zero when missing.

`fetched_at` is UTC retrieval time, not model issuance time. Values correspond to
the latest common timestamp in BOTH upstream responses that is not in the future
and is no more than **2 hours old** at retrieval. This tolerates different hourly
coverage and hour rollovers without mixing timestamps. No recent common timestamp
means 503. Null/invalid values at the selected timestamp also produce 503; the
service does not search older rows to hide invalid readings.
The six past hours are requested as specified but are not sent to the device.

Invalid/missing coordinates return 422 (latitude 7–14, longitude 77–81 inclusive).
Upstream timeout, HTTP errors, malformed JSON and unavailable data return 503
with a short detail and a safe `Retry-After` header. See rate-limit handling below.
Fetches use 8-second connect, 20-second HTTP operation and 25-second overall
timeouts (including concurrency waits and retries). No stale or simulated fallback
exists. Successful data and upstream errors use HTTP `no-store`; the internal
validated cache described below is independent of browser/proxy caching.

Run offline tests inside this folder:

```bash
python -m unittest -v test_gateway.py
```

## Generic free-tier deployment preparation

Choose a Python web service with a free allocation that supports the transport
needed by your device. No hosting account, payment, public hostname or deployment
is created by these files. Free availability, sleep policy and quotas depend on
the chosen provider; this folder does not guarantee perpetual free hosting.

- Service root directory: `kadal-data-gateway` (or `.` if copied to its own repository).
- Python runtime: 3.11 or newer.
- Build command: `pip install -r requirements.txt`
- Start command (shell expands the provider's PORT environment variable):

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

- Health path: `/api/health`
- Allow outbound HTTPS to both Open-Meteo APIs.
- Upload only this folder; do not deploy the parent repository's Dockerfile,
  requirements, models or frontend. No provider-specific configuration is required.

### SIM800L transport gate

The supplied sketch uses `TinyGsmClient` over **plain HTTP port 80**. Before flashing,
verify the public endpoint using curl WITHOUT `-L`:

```bash
curl -i 'http://YOUR_PUBLIC_GATEWAY_HOST/api/health'
curl -i 'http://YOUR_PUBLIC_GATEWAY_HOST/api/device/data?latitude=10.90&longitude=80.20'
```

Both must return 200 directly, not a redirect to HTTPS, login page or challenge.
Many hosting frontends enforce HTTPS. Such a host is NOT compatible with this
plain-HTTP sketch. Do not assume SIM800L firmware's TLS support will work with
modern hosting. This requires a different transport/compatible verified TLS setup
or an HTTP-capable host; changing port 80 to 443 alone does not enable TLS.
Plain HTTP is unencrypted and unauthenticated: coordinates and responses can be
observed or altered in transit. The gateway itself contacts Open-Meteo over HTTPS.

## ESP32 workflow

1. Wire SIM800L TXD to ESP32 GPIO16 (RX), and ESP32 GPIO17 (TX) to SIM800L RXD.
   Connect common ground. Use the power supply and UART level adaptation specified
   for your particular SIM800L board; do not power a bare modem from an ESP32 GPIO.
   Power on the modem using the board's required procedure; no power-key pin is assumed.
2. Fit an antenna and an active Airtel SIM with data and local 2G coverage.
   The APN is `airtelgprs.com`, with blank username/password. Disable SIM PIN or
   unlock it separately; the sketch does not guess or store a PIN.
3. Install Arduino IDE's ESP32 board support and libraries **TinyGSM**,
   **ArduinoHttpClient**, and **ArduinoJson 7**.
4. Arduino sketches need a folder matching their filename. Copy
   `esp32_kadal_gprs_test.ino` into a folder named `esp32_kadal_gprs_test`, then open it.
5. Replace `SERVER_HOST = "YOUR_PUBLIC_GATEWAY_HOST"` with the verified public
   HTTP hostname, without a scheme or path. Keep port 80 unless your host specifies
   another reachable plain-HTTP port. Do not use localhost/0.0.0.0 on a remote modem.
6. Choose your ESP32 board and serial port; compile and upload. Open Serial Monitor
   at **115200**. The modem UART is **9600**, RX=16, TX=17.
7. Watch AT communication, CPIN, CSQ, CREG and CGATT diagnostics. TinyGSM waits for
   registration, configures APN/PDP and attaches GPRS. CGATT? is a query; TinyGSM's
   connection routine performs the actual attachment.
8. The sketch requests `/api/device/data?latitude=10.90&longitude=80.20`, prints
   HTTP status and the response, validates the JSON fields, then prints CURRENT
   WAVE, WAVE PERIOD, WAVE DIRECTION, CURRENT WIND and WIND DIRECTION with units.
9. Failures print diagnostic messages instead of readings. Redirects are rejected,
   response bodies are limited to 2048 bytes, and network/body waits have timeouts.
   TinyGSM calls are synchronous and some connection phases can take minutes.
   The connection closes and a new attempt occurs after five minutes.

The test coordinates are explicitly supplied demonstration request coordinates,
not GPS readings. No physical ESP32/SIM800L or Airtel network test is implied by
Python tests. Actual registration, power stability and radio coverage must be
verified on the device. A host cold start longer than the device timeout fails
that attempt; the next scheduled attempt may succeed.

## Scope and upstream usage

This public service has no authentication or distributed rate limiter.
A cache miss normally makes two upstream calls; hits make none. Keep polling modest and enforce
appropriate quotas at hosting ingress before broader public use. No land mask
or maritime boundary checks are performed; bounding-box validation alone does
not prove a location is offshore. Marine `cell_selection=sea` selects a sea grid cell.

Open-Meteo's free API is for non-commercial use and has request limits; check its
current terms before deployment. This gateway is live numerical-model data, not
onboard sensor measurements or navigation clearance.

Official references:
- https://open-meteo.com/en/docs/marine-weather-api
- https://open-meteo.com/en/docs
- https://open-meteo.com/en/terms
- https://github.com/vshymanskyy/TinyGSM
- https://github.com/arduino-libraries/ArduinoHttpClient
- https://arduinojson.org/v7/

## Verification performed

On 2026-10-07, a clean temporary virtual environment containing only this folder's
runtime requirements passed all three unittest groups (including invalid-coordinate
and upstream-failure subcases). A temporary Uvicorn server returned health 200,
a real Open-Meteo data response of 252 bytes with HTTP 200, and invalid-coordinate
HTTP 422. Existing backend, ML source and frontend checksums remained unchanged.
The Arduino sketch has not been compiled or tested on hardware in this environment.
No public service has been deployed.


## Diagnosing gateway 503 responses

The former exact-current-hour lookup could reject usable overlapping hourly data.
The gateway now intersects timestamps, normalizes them to UTC, and selects the
newest common timestamp within the two-hour freshness limit. Output fields and
Render root/build/start settings are unchanged.

In Render logs, search for `Live data failed`. Each failure includes the exception
class and a safe failure summary, marine/weather HTTP status where available, and up
to 32 available timestamps from each response. A timeout before a response has no
HTTP status. Concurrent requests retain diagnostics from a successful source even
when the other fails. Client responses remain the same generic 503; internal
exception details are logged only on the server.

Timestamp mismatch is one possible cause, not proof of the deployed failure:
HTTP 429/403, provider outages, network timeouts and null fields still correctly
produce 503. Use the new logs to distinguish these before further changes.

Local regression and live checks (port 8001):

```bash
python -m unittest -v test_gateway.py
uvicorn main:app --host 0.0.0.0 --port 8001
# In another terminal:
curl http://127.0.0.1:8001/api/health
curl 'http://127.0.0.1:8001/api/device/data?latitude=10.9&longitude=80.2'
```


## Rate-limit handling (2026-10-18)

The supplied Render log confirms weather-provider HTTP 429. Previously, every
valid device request triggered two upstream calls, including repeated coordinates
and simultaneous requests. There was no cache or retry/cooldown handling. This
made unnecessary traffic possible; the log alone cannot establish whether quota
exhaustion came from device polling, other callers, or a shared hosting egress IP.
Switching the two calls to sequential requests would not resolve that quota.

- **Validated cache:** 600-second TTL, at most 128 locations, least-recently-used
  eviction. Keys use parsed float coordinates: `10.0` and `10.00` share an entry;
  distinct numeric coordinates are not rounded together. All other upstream
  options are fixed. No error or incomplete payload is cached.
- **Freshness on every hit:** the selected source timestamp must still be no more
  than two hours old and not in the future, even before TTL expires. Expired or
  no-longer-fresh entries are removed. `fetched_at` remains the actual original
  retrieval time; serving cached data does not change it. There is no stale fallback.
- **Concurrent callers:** same-location requests share one in-flight task, including
  its failure; a disconnected caller does not cancel the fetch for others. There
  are at most 32 distinct in-flight locations and four simultaneous upstream HTTP
  requests per process. Saturation returns the controlled 503 with Retry-After.
- **Retries:** at most three attempts per provider, only for transport/timeouts or
  HTTP 429/500/502/503/504. Backoff is 1, then 2 seconds plus 0–0.5 seconds jitter.
  Retry-After (seconds or HTTP-date) is a minimum delay. Retries are skipped when
  the wait would leave less than one second in the 25-second total fetch budget.
  Invalid JSON, missing values and alignment failures are not retried.
- **429 cooldown:** without a usable Retry-After, wait at least 60 seconds; this
  exceeds the request budget, so return 503 without retrying in that request.
  A provider's cooldown blocks new cache misses for other coordinates too, while
  validated cache hits remain available. Longer Retry-After values are retained
  rather than capped down. Retry-After from retryable 5xx responses is also respected.
  Client 503 headers indicate when to try again. No indefinite retry loop exists.
- **Scope:** all cache, in-flight sharing, concurrency limits and cooldowns are
  in-memory and per process. A restart/deploy clears them. Multiple workers or
  instances have separate caches and quotas; the unchanged single-worker start
  command is appropriate here. These controls cannot guarantee that Open-Meteo
  will lift a shared-IP or daily quota restriction.
- **Polling:** prefer one request every ten minutes for a stationary device; do
  not poll faster than the existing five-minute sketch interval. After 503, wait
  at least the returned Retry-After (and your regular interval, whichever is longer).
  The ESP32 sketch is unchanged; its existing fixed retry delay does not dynamically
  parse Retry-After. Pause testing manually if a longer cooldown is returned.
- **Payload window:** the five existing variables, six past hours and one forecast
  hour are retained. This already-small window supports overlap validation; none
  of the history is returned to the device. No extra upstream endpoint was added.

Logs distinguish cache hit/miss, in-flight sharing, upstream start/completion,
provider/status/location with truncated/redacted error body, retry scheduled/skipped,
transport failure, validation failure and successful validation. Raw credentials,
headers and full response documents are not logged. Look in Render application logs
for `kadal.gateway`. Cache diagnostics are INFO; failures are WARNING/ERROR.

### Test and deploy only these changes

From the repository root, with the existing local environment:

```bash
cd kadal-data-gateway
../.venv/bin/python -m unittest -v test_gateway.py
cd ..
git diff --check -- kadal-data-gateway
git diff -- kadal-data-gateway/main.py kadal-data-gateway/test_gateway.py kadal-data-gateway/README.md
```

When you choose to deploy, commit only the gateway files (not unrelated staged or
unstaged Kadal changes), then push the Render-linked `main` branch:

```bash
git branch --show-current
git commit --only -m "Cache gateway data and respect upstream rate limits" -- kadal-data-gateway/main.py kadal-data-gateway/test_gateway.py kadal-data-gateway/README.md
git show --stat --oneline HEAD
git push origin main
```

Confirm the current branch is `main` before the commit/push. A push includes any
other already-committed unpublished history on that branch, so review that history
if present. Render auto-deploys if enabled; otherwise use Manual Deploy → Deploy
latest commit in the existing Render service. Keep these settings unchanged:

- Root Directory: `kadal-data-gateway`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

After deployment finishes, issue each check **once**, without curl retry flags:

```bash
curl --max-time 90 -i https://ml-fishermen-safety-system.onrender.com/api/health
curl --max-time 90 -i 'https://ml-fishermen-safety-system.onrender.com/api/device/data?latitude=10.0&longitude=80.5'
```

Health does not contact Open-Meteo. If data returns 503, inspect the logs and wait
for Retry-After before testing again. Do not repeatedly refresh or restart the
service to bypass cooldowns. Unit tests mock all upstream traffic and clocks;
no real provider calls are needed to verify cache and retry behavior.
