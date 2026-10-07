// Libraries: TinyGSM, ArduinoHttpClient, ArduinoJson 7. Board: ESP32.
#define TINY_GSM_MODEM_SIM800
#define TINY_GSM_RX_BUFFER 1024
#include <TinyGsmClient.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include <math.h>

// Replace with a hostname ONLY: no http://, slash, or path.
const char* SERVER_HOST = "YOUR_PUBLIC_GATEWAY_HOST";
const uint16_t SERVER_PORT = 80;  // Requires plain HTTP without HTTPS redirect.
const char* APN = "airtelgprs.com";
const char* REQUEST_PATH = "/api/device/data?latitude=10.90&longitude=80.20";
HardwareSerial ModemSerial(2);
TinyGsm modem(ModemSerial);
TinyGsmClient transport(modem);
HttpClient http(transport, SERVER_HOST, SERVER_PORT);

bool diagnostic(const char* command, uint32_t timeout = 5000) {
  String answer;
  modem.sendAT(command);
  int result = modem.waitResponse(timeout, answer);
  Serial.print("AT"); Serial.println(command); Serial.println(answer);
  return result == 1;
}

bool connectGprs() {
  if (!modem.testAT(10000)) { Serial.println("Modem timeout: check power, wiring and baud."); return false; }
  if (!modem.init()) { Serial.println("Modem initialization failed."); return false; }
  if (!diagnostic("+CPIN?") || modem.getSimStatus() != SIM_READY) {
    Serial.println("SIM not ready. Check SIM/PIN."); return false;
  }
  if (!diagnostic("+CSQ") || !diagnostic("+CREG?")) return false;
  if (!modem.waitForNetwork(60000L)) { Serial.println("Network registration failed."); return false; }
  if (!diagnostic("+CGATT?")) return false;
  // TinyGSM configures APN/PDP, attaches with CGATT=1 and opens the data connection.
  if (!modem.gprsConnect(APN, "", "") || !modem.isGprsConnected()) {
    Serial.println("GPRS connection failed."); return false;
  }
  if (!diagnostic("+CGATT?")) return false;
  Serial.print("GPRS IP: "); Serial.println(modem.localIP());
  return true;
}

bool fetchData() {
  http.setHttpResponseTimeout(90000L); // Allow for slow 2G and host cold starts.
  int requestError = http.get(REQUEST_PATH);
  if (requestError != 0) { Serial.print("HTTP request error: "); Serial.println(requestError); return false; }
  int status = http.responseStatusCode();
  Serial.print("HTTP status: "); Serial.println(status);
  if (status < 0) { Serial.println("HTTP timeout or malformed headers."); return false; }
  if (status >= 300 && status < 400) {
    Serial.println("Redirect rejected. This sketch needs a direct plain-HTTP endpoint."); return false;
  }
  if (http.skipResponseHeaders() != 0) { Serial.println("HTTP header timeout or parse error."); return false; }
  if (http.contentLength() > 2048) { Serial.println("HTTP body too large."); return false; }
  // HttpClient decodes chunked HTTP; bound memory and total body-read time.
  String body;
  body.reserve(1024);
  uint32_t start = millis();
  while (!http.endOfBodyReached() && millis() - start < 90000UL) {
    while (http.available()) {
      int value = http.read();
      if (value < 0) break;
      if (body.length() >= 2048) { Serial.println("HTTP body too large."); return false; }
      body += char(value);
    }
    if (!http.connected() && !http.available()) break;
    delay(10);
  }
  Serial.println(body);
  if (!http.endOfBodyReached() && !(http.contentLength() < 0 && !http.connected() && !http.available())) {
    Serial.println("Incomplete body or timeout."); return false;
  }
  if (status != 200) { Serial.println("Gateway returned an error; no current values displayed."); return false; }
  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, body);
  if (error) { Serial.print("Malformed JSON: "); Serial.println(error.c_str()); return false; }
  const char* fields[] = {"wave_m", "wave_period_s", "wave_direction", "wind_ms", "wind_direction"};
  for (const char* field : fields) {
    if (!doc[field].is<double>() || !isfinite(doc[field].as<double>()) || doc[field].as<double>() < 0) {
      Serial.print("Missing/invalid value: "); Serial.println(field); return false;
    }
  }
  if (doc["data_type"].as<String>() != "model_estimate" || !doc["fetched_at"].is<const char*>() ||
      doc["wave_direction"].as<double>() > 360 || doc["wind_direction"].as<double>() > 360) {
    Serial.println("Unexpected data semantics or direction."); return false;
  }
  Serial.println("Live MODEL ESTIMATES, not sensor measurements.");
  Serial.print("Fetched at: "); Serial.println(doc["fetched_at"].as<const char*>());
  const char* labels[] = {"CURRENT WAVE", "WAVE PERIOD", "WAVE DIRECTION", "CURRENT WIND", "WIND DIRECTION"};
  const char* units[] = {" m", " s", " degrees", " m/s", " degrees"};
  for (int i = 0; i < 5; i++) {
    Serial.print(labels[i]); Serial.print(": "); Serial.print(doc[fields[i]].as<double>(), 2); Serial.println(units[i]);
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  ModemSerial.begin(9600, SERIAL_8N1, 16, 17); // SIM TXD -> 16; SIM RXD <- 17.
  delay(3000);
  Serial.println("Kadal GPRS gateway test");
}

void loop() {
  if (String(SERVER_HOST) == "YOUR_PUBLIC_GATEWAY_HOST") {
    Serial.println("Set SERVER_HOST to your public HTTP gateway hostname first.");
    delay(30000); return;
  }
  if (connectGprs()) fetchData();
  http.stop();
  modem.gprsDisconnect();
  Serial.println("Next attempt in 5 minutes.");
  delay(300000); // Bounded retries; avoids repeated provider requests on failures.
}
