#include "comms.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <string.h>
#include <math.h>  // isnan

#include "../sensors/sensors.h"
#include "../control/control.h"
#include "../commands/commands.h"
#include "../diag/diag_mode.h"
#include "../diag/flight_recorder.h"
#include "../io/io_map.h"
#include "../io/io_catalog.h"
#include "../rules/rules.h"
#include "../control/process_config.h"
#include "../safety/antifreeze.h"
#include <config.h>

// ================= DEVICE ID =================
// Must be declared before mqttCallback — static free functions only see
// names declared above their definition in the same translation unit.

String device_id;

// ================= COMMAND CALLBACK =================

static Commands*       s_cmds      = nullptr;
static Sensors*        s_sensors   = nullptr;
static DiagMode*       s_diag      = nullptr;
static FlightRecorder* s_flightrec = nullptr;

// Forward-declarations — usadas por mqttCallback (definido antes de las
// implementaciones en la sección TIMERS/HELPERS).
extern WiFiManager wm;
extern bool fallbackPortalActive;
extern uint8_t lastWifiChannel;
String fallbackPortalSSID();
String fallbackPortalPassword();

// Maps ReceiveResult to a human-readable string for field logs.
// ACCEPTED(0) DUPLICATE(1) BUSY(2) INVALID_JSON(3) UNKNOWN_COMMAND(4)
static const char* receiveResultName(ReceiveResult r) {
    switch (r) {
        case ReceiveResult::ACCEPTED:        return "ACCEPTED";
        case ReceiveResult::DUPLICATE:       return "DUPLICATE";
        case ReceiveResult::BUSY:            return "BUSY";
        case ReceiveResult::INVALID_JSON:    return "INVALID_JSON";
        case ReceiveResult::UNKNOWN_COMMAND: return "UNKNOWN_COMMAND";
        default:                             return "UNKNOWN";
    }
}

// Aplica un objeto JSON {"gpio":.., "mode":.., "invert":.., "default_value":..,
// "debounce_ms":..} sobre el IOPinConfig actual (partial update por campo):
//   - "gpio" ausente   -> conserva el gpio actual
//   - "gpio": null     -> IOMAP_GPIO_NONE (señal sin pin asignado)
//   - "gpio": <num>    -> ese GPIO
//   - "mode" solo aplica si hasMode (inputs); valores desconocidos se ignoran
//   - "invert" ausente -> conserva el valor actual
//   - "default_value"/"debounce_ms" solo aplican si hasMode (inputs); ausentes
//     -> conservan el valor actual (compat con payloads de backends viejos)
static IOPinConfig parseIOEntry(JsonObject obj, const IOPinConfig& cur, bool hasMode) {
    IOPinConfig out = cur;
    if (obj.containsKey("gpio")) {
        JsonVariant g = obj["gpio"];
        out.gpio = g.isNull() ? IOMAP_GPIO_NONE : (uint8_t)g.as<unsigned int>();
    }
    if (hasMode && obj.containsKey("mode")) {
        const char* m = obj["mode"] | "";
        if      (strcmp(m, "pulldown") == 0) out.mode = IOMAP_MODE_PULLDOWN;
        else if (strcmp(m, "pullup")   == 0) out.mode = IOMAP_MODE_PULLUP;
    }
    if (obj.containsKey("invert")) {
        out.invert = (obj["invert"].as<int>() != 0) ? 1 : 0;
    }
    if (hasMode && obj.containsKey("default_value")) {
        out.default_value = (obj["default_value"].as<int>() != 0) ? 1 : 0;
    }
    if (hasMode && obj.containsKey("debounce_ms")) {
        out.debounce_ms = (uint16_t)obj["debounce_ms"].as<unsigned int>();
    }
    return out;
}

// Parsea {"op":"AND"|"OR","terms":[{"signal":"...","source":"input"|"derived","negate":bool}]}
// hacia una RuleConfig. "op" desconocido/ausente -> OR. Términos con
// signal/source desconocidos se ignoran (igual criterio que parseIOEntry);
// más de RULE_MAX_TERMS términos -> los excedentes se ignoran.
static RuleConfig parseRuleEntry(JsonObject obj) {
    RuleConfig out = { RuleOp::OR, 0, {} };

    const char* op = obj["op"] | "OR";
    out.op = (strcmp(op, "AND") == 0) ? RuleOp::AND : RuleOp::OR;

    JsonArray terms = obj["terms"].as<JsonArray>();
    for (JsonVariant v : terms) {
        if (out.term_count >= RULE_MAX_TERMS) break;
        JsonObject t = v.as<JsonObject>();

        const char* signal = t["signal"] | "";
        const char* source = t["source"] | "input";

        uint8_t   sigId;
        SignalSrc src;
        if (strcmp(source, "derived") == 0) {
            DerivedSignal d = derivedSignalFromName(signal);
            if (d == DerivedSignal::COUNT) {
                Serial.printf("[RULES] WARN: señal derivada desconocida '%s' — término ignorado\n", signal);
                continue;
            }
            sigId = (uint8_t)d;
            src   = SignalSrc::DERIVED;
        } else {
            LogicalInput in = logicalInputFromName(signal);
            if (in == LogicalInput::COUNT) {
                Serial.printf("[RULES] WARN: señal de entrada desconocida '%s' — término ignorado\n", signal);
                continue;
            }
            sigId = (uint8_t)in;
            src   = SignalSrc::SIG_INPUT;
        }

        out.terms[out.term_count] = { sigId, src, (uint8_t)((t["negate"] | false) ? 1 : 0) };
        out.term_count++;
    }
    return out;
}

// Free function required by PubSubClient. Dispatches to one of three handlers:
//   fyntek/{device_id}/cmd          — command from backend → Commands engine
//   fyntek/{device_id}/config       — retained calibration config → Sensors
//   fyntek/{device_id}/config/reset — factory reset → Sensors::resetConfig()
// Does NOT publish — ACK for commands is deferred to comms.update().
static void mqttCallback(char* topic, byte* payload, unsigned int length) {

    // ── /cmd ────────────────────────────────────────────────────────────────
    char cmd_topic[64];
    snprintf(cmd_topic, sizeof(cmd_topic), "fyntek/%s/cmd", device_id.c_str());
    if (strcmp(topic, cmd_topic) == 0) {
        if (!s_cmds) return;
        ReceiveResult result = s_cmds->receive(payload, length);
        if (result != ReceiveResult::ACCEPTED) {
            Serial.print("[CMD] recv rechazado: ");
            Serial.println(receiveResultName(result));
        }
        return;
    }

    // ── /config/reset ────────────────────────────────────────────────────────
    char rst_topic[72];
    snprintf(rst_topic, sizeof(rst_topic), "fyntek/%s/config/reset", device_id.c_str());
    if (strcmp(topic, rst_topic) == 0) {
        if (s_sensors) s_sensors->resetConfig();
        return;
    }

    // ── /config ──────────────────────────────────────────────────────────────
    // Retained message: broker delivers it immediately on reconnect.
    // Uses current config values as fallback for any missing field (partial update).
    char cfg_topic[64];
    snprintf(cfg_topic, sizeof(cfg_topic), "fyntek/%s/config", device_id.c_str());
    if (strcmp(topic, cfg_topic) == 0) {
        if (!s_sensors) return;

        StaticJsonDocument<768> doc;
        DeserializationError err = deserializeJson(doc, payload, length);
        if (err) {
            Serial.print("[CFG] JSON inválido: ");
            Serial.println(err.c_str());
            return;
        }

        // Missing fields fall back to current config — safe partial updates.
        SensorConfig cur = s_sensors->getConfig();
        SensorConfig incoming;
        incoming.flow_factor_1             = doc["flow_factor_1"]             | cur.flow_factor_1;
        incoming.flow_factor_2             = doc["flow_factor_2"]             | cur.flow_factor_2;
        incoming.tds_temperature           = doc["tds_temperature"]           | cur.tds_temperature;
        incoming.tds1_cal_slope            = doc["tds1_cal_slope"]            | cur.tds1_cal_slope;
        incoming.tds1_cal_offset           = doc["tds1_cal_offset"]           | cur.tds1_cal_offset;
        incoming.tds2_cal_slope            = doc["tds2_cal_slope"]            | cur.tds2_cal_slope;
        incoming.tds2_cal_offset           = doc["tds2_cal_offset"]           | cur.tds2_cal_offset;
        incoming.min_flow_lpm              = doc["min_flow_lpm"]              | cur.min_flow_lpm;
        incoming.max_flow_lpm              = doc["max_flow_lpm"]              | cur.max_flow_lpm;
        incoming.flow_fault_delay_sec      = doc["flow_fault_delay_sec"]      | cur.flow_fault_delay_sec;
        incoming.min_recovery_pct          = doc["min_recovery_pct"]          | cur.min_recovery_pct;
        incoming.max_recovery_pct          = doc["max_recovery_pct"]          | cur.max_recovery_pct;
        incoming.recovery_fault_delay_sec  = doc["recovery_fault_delay_sec"]  | cur.recovery_fault_delay_sec;
        incoming.pressure_membrane_enabled        = doc["pressure_membrane_enabled"]        | cur.pressure_membrane_enabled;
        incoming.pressure_membrane_min_voltage    = doc["pressure_membrane_min_voltage"]    | cur.pressure_membrane_min_voltage;
        incoming.pressure_membrane_max_voltage    = doc["pressure_membrane_max_voltage"]    | cur.pressure_membrane_max_voltage;
        incoming.pressure_membrane_min_bar        = doc["pressure_membrane_min_bar"]        | cur.pressure_membrane_min_bar;
        incoming.pressure_membrane_max_bar        = doc["pressure_membrane_max_bar"]        | cur.pressure_membrane_max_bar;
        incoming.pressure_membrane_limits_enabled = doc["pressure_membrane_limits_enabled"] | cur.pressure_membrane_limits_enabled;
        incoming.pressure_membrane_high_limit     = doc["pressure_membrane_high_limit"]     | cur.pressure_membrane_high_limit;
        incoming.pressure_fault_delay_sec         = doc["pressure_fault_delay_sec"]         | cur.pressure_fault_delay_sec;
        incoming.pressure_brine_enabled           = doc["pressure_brine_enabled"]           | cur.pressure_brine_enabled;
        incoming.pressure_brine_min_voltage       = doc["pressure_brine_min_voltage"]       | cur.pressure_brine_min_voltage;
        incoming.pressure_brine_max_voltage       = doc["pressure_brine_max_voltage"]       | cur.pressure_brine_max_voltage;
        incoming.pressure_brine_min_bar           = doc["pressure_brine_min_bar"]           | cur.pressure_brine_min_bar;
        incoming.pressure_brine_max_bar           = doc["pressure_brine_max_bar"]           | cur.pressure_brine_max_bar;
        incoming.flow_protection_enabled          = doc["flow_protection_enabled"]          | cur.flow_protection_enabled;
        incoming.recovery_protection_enabled      = doc["recovery_protection_enabled"]      | cur.recovery_protection_enabled;
        incoming.updated_at                = doc["updated_at"]                | (unsigned long)0;

        s_sensors->setConfig(incoming);
        return;
    }

    // ── /iomap ───────────────────────────────────────────────────────────────
    // Retained: mapeo Pin<->Señal lógica (capa de abstracción de hardware).
    // Solo persistencia en NVS — no produce cambios de comportamiento en
    // Sensors/Control en esta fase. Partial update por señal: claves
    // ausentes conservan el valor actual de esa señal.
    char iomap_topic[68];
    snprintf(iomap_topic, sizeof(iomap_topic), "fyntek/%s/iomap", device_id.c_str());
    if (strcmp(topic, iomap_topic) == 0) {
        StaticJsonDocument<2048> doc;
        DeserializationError err = deserializeJson(doc, payload, length);
        if (err) {
            Serial.print("[IOMAP] JSON inválido: ");
            Serial.println(err.c_str());
            return;
        }

        IOMapConfig incoming = ioMapGet();

        JsonObject inObj = doc["inputs"].as<JsonObject>();
        for (JsonPair kv : inObj) {
            LogicalInput sig = logicalInputFromName(kv.key().c_str());
            if (sig == LogicalInput::COUNT) continue;
            uint8_t idx = (uint8_t)sig;
            incoming.inputs[idx] = parseIOEntry(kv.value().as<JsonObject>(), incoming.inputs[idx], true);
        }

        JsonObject outObj = doc["outputs"].as<JsonObject>();
        for (JsonPair kv : outObj) {
            LogicalOutput sig = logicalOutputFromName(kv.key().c_str());
            if (sig == LogicalOutput::COUNT) continue;
            uint8_t idx = (uint8_t)sig;
            incoming.outputs[idx] = parseIOEntry(kv.value().as<JsonObject>(), incoming.outputs[idx], false);
        }

        incoming.updated_at = doc["updated_at"] | (uint32_t)0;
        ioMapSet(incoming);
        return;
    }

    // ── /rules ───────────────────────────────────────────────────────────────
    // Retained: motor de reglas (process_permits / independent_outputs /
    // fault_rules). Partial update por categoría/slot — claves ausentes
    // conservan los valores actuales (mismo patrón que /iomap).
    char rules_topic[68];
    snprintf(rules_topic, sizeof(rules_topic), "fyntek/%s/rules", device_id.c_str());
    if (strcmp(topic, rules_topic) == 0) {
        StaticJsonDocument<1536> doc;
        DeserializationError err = deserializeJson(doc, payload, length);
        if (err) {
            Serial.print("[RULES] JSON inválido: ");
            Serial.println(err.c_str());
            return;
        }

        RulesConfig incoming = rulesGet();

        JsonObject permits = doc["process_permits"].as<JsonObject>();
        for (JsonPair kv : permits) {
            ProcessId p = processFromName(kv.key().c_str());
            if (p == ProcessId::COUNT) continue;
            incoming.process_permits[(uint8_t)p] = parseRuleEntry(kv.value().as<JsonObject>());
        }

        JsonObject outs = doc["independent_outputs"].as<JsonObject>();
        for (JsonPair kv : outs) {
            LogicalOutput o = logicalOutputFromName(kv.key().c_str());
            if (o == LogicalOutput::COUNT) continue;
            incoming.independent_outputs[(uint8_t)o] = parseRuleEntry(kv.value().as<JsonObject>());
        }

        // fault_rules ausente -> no se modifica fault_rules/fault_rule_count
        // actuales (incoming ya es una copia de rulesGet()).
        JsonArray faults = doc["fault_rules"].as<JsonArray>();
        if (!faults.isNull()) {
            uint8_t n = 0;
            for (JsonVariant v : faults) {
                if (n >= FAULT_RULES_MAX) break;
                JsonObject fr = v.as<JsonObject>();

                FaultReason reason = faultReasonFromName(fr["reason"] | "");
                if (reason == FaultReason::NONE) continue;  // entrada inválida — se descarta

                incoming.fault_rules[n].condition = parseRuleEntry(fr["condition"].as<JsonObject>());
                incoming.fault_rules[n].reason    = reason;
                incoming.fault_rules[n].delay_sec = fr["delay_sec"] | 0U;
                n++;
            }
            incoming.fault_rule_count = n;
        }

        incoming.updated_at = doc["updated_at"] | (uint32_t)0;
        rulesSet(incoming);
        return;
    }

    // ── /process_config ──────────────────────────────────────────────────────
    // Retained: parámetros de temporización FSM. Partial update — campos ausentes
    // conservan el valor actual (mismo patrón que /config, /iomap, /rules).
    char proccfg_topic[76];
    snprintf(proccfg_topic, sizeof(proccfg_topic), "fyntek/%s/process_config", device_id.c_str());
    if (strcmp(topic, proccfg_topic) == 0) {
        StaticJsonDocument<512> doc;
        DeserializationError err = deserializeJson(doc, payload, length);
        if (err) {
            Serial.print("[PROCCFG] JSON inválido: ");
            Serial.println(err.c_str());
            return;
        }
        ProcessConfig cur = processConfigGet();
        ProcessConfig incoming;
        incoming.pressure_stabilization_delay_sec = doc["pressure_stabilization_delay_sec"] | cur.pressure_stabilization_delay_sec;
        incoming.startup_timeout_sec              = doc["startup_timeout_sec"]              | cur.startup_timeout_sec;
        incoming.retry_interval_sec               = doc["retry_interval_sec"]               | cur.retry_interval_sec;
        incoming.max_retries                      = doc["max_retries"]                      | cur.max_retries;
        incoming.flush_duration_sec               = doc["flush_duration_sec"]               | cur.flush_duration_sec;
        incoming.updated_at                       = doc["updated_at"]                       | (uint32_t)0;
        processConfigSet(incoming);
        return;
    }

    // ── /antifreeze_config ───────────────────────────────────────────────────
    // Retained: protección anti-congelamiento opcional. Partial update —
    // campos ausentes conservan el valor actual (mismo patrón que /config,
    // /iomap, /rules, /process_config).
    char afreeze_topic[80];
    snprintf(afreeze_topic, sizeof(afreeze_topic), "fyntek/%s/antifreeze_config", device_id.c_str());
    if (strcmp(topic, afreeze_topic) == 0) {
        StaticJsonDocument<512> doc;
        DeserializationError err = deserializeJson(doc, payload, length);
        if (err) {
            Serial.print("[AFREEZE] JSON inválido: ");
            Serial.println(err.c_str());
            return;
        }
        AntifreezeConfig cur = antifreezeConfigGet();
        AntifreezeConfig incoming;
        incoming.enabled                  = doc["enabled"]                  | cur.enabled;
        incoming.sensor_enabled           = doc["sensor_enabled"]           | cur.sensor_enabled;
        incoming.sensor_gpio              = doc["sensor_gpio"]              | cur.sensor_gpio;
        incoming.temp_threshold_low_c     = doc["temp_threshold_low_c"]     | cur.temp_threshold_low_c;
        incoming.temp_threshold_high_c    = doc["temp_threshold_high_c"]    | cur.temp_threshold_high_c;
        incoming.flush_duration_sec       = doc["flush_duration_sec"]       | cur.flush_duration_sec;
        incoming.eval_interval_sec        = doc["eval_interval_sec"]        | cur.eval_interval_sec;
        incoming.boot_inhibit_sec         = doc["boot_inhibit_sec"]         | cur.boot_inhibit_sec;
        incoming.min_valid_temp_c         = doc["min_valid_temp_c"]         | cur.min_valid_temp_c;
        incoming.max_valid_temp_c         = doc["max_valid_temp_c"]         | cur.max_valid_temp_c;
        incoming.max_consecutive_failures = doc["max_consecutive_failures"] | cur.max_consecutive_failures;
        incoming.updated_at               = doc["updated_at"]               | (uint32_t)0;
        antifreezeConfigSet(incoming);
        return;
    }

    // ── /diag/ctrl ───────────────────────────────────────────────────────────
    char diag_topic[72];
    snprintf(diag_topic, sizeof(diag_topic), "fyntek/%s/diag/ctrl", device_id.c_str());
    if (strcmp(topic, diag_topic) == 0) {
        if (!s_diag) return;
        StaticJsonDocument<64> doc;
        if (deserializeJson(doc, payload, length)) return;
        if (doc["enable"] | false) {
            uint32_t dur = doc["duration_sec"] | 300U;
            s_diag->activate(dur);
            Serial.printf("[DIAG] Activado %us\n", dur);
        } else {
            s_diag->deactivate();
            Serial.println("[DIAG] Desactivado");
        }
        return;
    }

    // ── /diag/flightrec/get ──────────────────────────────────────────────────
    char fr_topic[80];
    snprintf(fr_topic, sizeof(fr_topic), "fyntek/%s/diag/flightrec/get", device_id.c_str());
    if (strcmp(topic, fr_topic) == 0) {
        if (s_flightrec) s_flightrec->startDump();
        return;
    }

    // ── /wifi/reset — forzar apertura de portal WiFiManager ─────────────────
    char wifi_topic[72];
    snprintf(wifi_topic, sizeof(wifi_topic), "fyntek/%s/wifi/reset", device_id.c_str());
    if (strcmp(topic, wifi_topic) == 0) {
        Serial.printf("[WIFI] Reset solicitado por MQTT (heap libre: %u)\n", ESP.getFreeHeap());
        if (!fallbackPortalActive) {
            wm.setConfigPortalBlocking(false);
            if (lastWifiChannel > 0) wm.setWiFiAPChannel(lastWifiChannel);
            wm.startConfigPortal(fallbackPortalSSID().c_str(), fallbackPortalPassword().c_str());
            fallbackPortalActive = true;
        }
        return;
    }
}

// ================= CONFIG =================

const char* mqtt_server = MQTT_BROKER;
const int mqtt_port = MQTT_PORT;

const char* mqtt_user = MQTT_USER;
const char* mqtt_pass = MQTT_PASS;

const char* fw_version = "2.2.0";

// NTP
const char* ntpServer = "pool.ntp.org";
const long gmtOffset_sec = -3 * 3600;
const int daylightOffset_sec = 0;

// ================= OBJETOS =================

WiFiManager wm;
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ================= TIMERS =================

unsigned long lastWifiCheck = 0;
unsigned long lastMqttReconnect = 0;

// ── WiFi fallback portal (ver setupWiFi() / WIFI_FALLBACK_* en config.h) ────
// Counter-based: cuenta checks desconectados en ventana de 5 checks (50s).
// Si >= 3 de 5 están desconectados, abre portal.
static uint8_t wifiCheckHistory[5] = {1,1,1,1,1};  // 1=ok, 0=down; init como "conectado"
static uint8_t wifiCheckIdx = 0;
bool fallbackPortalActive   = false;
unsigned long lastPortalHeapLog = 0;

// Último canal WiFi visto conectado. El ESP32 comparte un único canal de
// radio entre AP y STA en modo AP_STA: si el portal de fallback abre su AP
// en un canal distinto al del router, la STA queda físicamente incapacitada
// de reconectar mientras el portal esté activo, sin importar que la red esté
// arriba. setWiFiAPChannel() antes de cada startConfigPortal() fija el canal
// del AP al último canal real del router (0 = sin dato aún, channel-sync off).
uint8_t lastWifiChannel = 0;

// Flag para forzar re-publish de /state tras reconexión MQTT.
static bool forceStatePublish = false;

// true una vez que configTime() fue llamado tras la primera conexión WiFi.
// Permite disparar la sincronización NTP también cuando la conexión se
// establece en background (fuera de setupWiFi()).
bool ntpConfigured = false;

unsigned long lastProcess = 0;
unsigned long lastQuality = 0;
unsigned long lastHeartbeat = 0;

String getDeviceID() {
    uint64_t mac = ESP.getEfuseMac();

    char id[20];
    sprintf(id, "ESP32_%04X%08X",
        (uint16_t)(mac >> 32),
        (uint32_t)mac);

    return String(id);
}

// SSID/password del portal de fallback, derivados de device_id — documentables
// y recuperables sin consultar al equipo (ej. ESP32_ECBA88C92DF4 -> SSID
// "FYNTEK_2DF4", password "kairox88c92df4").
String fallbackPortalSSID() {
    return "FYNTEK_" + device_id.substring(device_id.length() - 6);
}

String fallbackPortalPassword() {
    return "kairox2026";
}

// ================= HELPERS =================

long getTimestamp() {
    if (!ntpConfigured) return 0;
    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 10)) return 0;  // 10ms — no bloquea el loop
    return mktime(&timeinfo);
}

String baseTopic(String sub) {
    return "fyntek/" + device_id + "/" + sub;
}

// JSON no admite NaN — delta_p_bar es NAN cuando no hay calibración en ambos
// canales de presión. Se publica como `null` (sin valor artificial) en ese caso.
static String floatOrNull(float v) {
    return isnan(v) ? String("null") : String(v, 3);
}

// ── I/O publish helpers ───────────────────────────────────────────────────────
// Single source of truth for /outputs and /inputs payloads.
// Called from both on-change detection and periodic heartbeat so the two
// paths can never diverge when fields are added or renamed.

static void publishOutputs(long ts) {
    const IOMapConfig& iomap = ioMapGet();
    String json = "{";
    json += "\"device_id\":\"" + device_id + "\",";
    json += "\"fw_version\":\"" + String(fw_version) + "\",";
    json += "\"ts\":" + String(ts);
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        const IOPinConfig& pin = iomap.outputs[i];
        if (pin.gpio == IOMAP_GPIO_NONE) continue;
        bool raw     = digitalRead(pin.gpio);
        bool logical = pin.invert ? !raw : raw;
        json += ",\"";
        json += logicalOutputName((LogicalOutput)i);
        json += "\":";
        json += logical ? "1" : "0";
    }
    json += "}";
    mqttClient.publish(baseTopic("outputs").c_str(), json.c_str());
}

static void publishInputs(Sensors& s, long ts) {
    const IOMapConfig& iomap = ioMapGet();
    String json = "{";
    json += "\"device_id\":\"" + device_id + "\",";
    json += "\"fw_version\":\"" + String(fw_version) + "\",";
    json += "\"ts\":" + String(ts);
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (iomap.inputs[i].gpio == IOMAP_GPIO_NONE) continue;
        json += ",\"";
        json += logicalInputName((LogicalInput)i);
        json += "\":";
        json += s.getSignal((LogicalInput)i) ? "1" : "0";
    }
    json += "}";
    mqttClient.publish(baseTopic("inputs").c_str(), json.c_str());
}

// ================= WIFI =================

// No bloqueante para el FSM: si hay credenciales guardadas, se intenta
// conectar en background (WiFi.begin() no bloquea) y setup() continúa de
// inmediato — Comms::update() reintenta vía WiFi.reconnect(). El FSM
// (control.update()) no depende de WiFi/MQTT para operar.
//
// Solo en el primer arranque (sin credenciales guardadas) se abre el portal
// cautivo de configuración, con timeout acotado (WIFI_PORTAL_TIMEOUT_SEC) —
// si nadie configura WiFi en ese plazo, el equipo arranca offline igual.
void setupWiFi() {
    WiFi.mode(WIFI_STA);

    if (wm.getWiFiIsSaved()) {
        WiFi.begin();
        Serial.println("[WIFI] Conectando en background (credenciales guardadas)...");
        return;
    }

    wm.setConfigPortalTimeout(WIFI_PORTAL_TIMEOUT_SEC);
    if (wm.autoConnect("FYNTEK_SETUP")) {
        Serial.println("✅ WiFi conectado");
        configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
        ntpConfigured = true;
    } else {
        Serial.println("⚠ Portal de configuración expiró sin WiFi — arrancando offline");
    }
}

// ================= MQTT =================

void Comms::reconnect() {

    if (mqttClient.connected()) return;

    unsigned long now = millis();
    if (now - lastMqttReconnect < 5000) return;

    lastMqttReconnect = now;

    String clientId = "ESP32-" + device_id;

    if (mqttClient.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {

        Serial.println("✅ MQTT conectado");

        mqttClient.subscribe(baseTopic("cmd").c_str());
        mqttClient.subscribe(baseTopic("config").c_str());
        mqttClient.subscribe(baseTopic("config/reset").c_str());
        mqttClient.subscribe(baseTopic("iomap").c_str());
        mqttClient.subscribe(baseTopic("rules").c_str());
        mqttClient.subscribe(baseTopic("process_config").c_str());
        mqttClient.subscribe(baseTopic("antifreeze_config").c_str());
        mqttClient.subscribe(baseTopic("diag/ctrl").c_str());
        mqttClient.subscribe(baseTopic("diag/flightrec/get").c_str());
        mqttClient.subscribe(baseTopic("wifi/reset").c_str());

        // 🔥 SNAPSHOT REAL
        sendSnapshot = true;
        forceStatePublish = true;

    } else {
        Serial.println("❌ MQTT reconectando...");
    }
}

// ================= INIT =================

void Comms::begin(Commands &cmds, Sensors &s, DiagMode &diag, FlightRecorder &fr) {

    Serial.println("[COMMS] Init");

    device_id = getDeviceID();

    Serial.print("DEVICE ID: ");
    Serial.println(device_id);

    setupWiFi();

    s_cmds      = &cmds;
    s_sensors   = &s;
    s_diag      = &diag;
    s_flightrec = &fr;
    mqttClient.setServer(mqtt_server, mqtt_port);
    mqttClient.setBufferSize(4096);
    mqttClient.setSocketTimeout(MQTT_SOCKET_TIMEOUT_SEC);
    mqttClient.setCallback(mqttCallback);
}

// ================= UPDATE =================

// ── Diagnostic 1 Hz publish ───────────────────────────────────────────────────
// Called only when DiagMode is active. Publishes full sensor/FSM/protection
// snapshot to fyntek/{id}/diag. Uses stack-static buffer — no heap allocation.
static void publishDiag(Sensors& s, Control& c, DiagMode& diag, long ts) {
    static char buf[1024];
    const DiagTransitions& tr = diag.getTransitions();
    char dpBuf[16];
    float dp = s.getDeltaPBar();
    if (isnan(dp)) snprintf(dpBuf, sizeof(dpBuf), "null");
    else           snprintf(dpBuf, sizeof(dpBuf), "%.3f", dp);
    snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\",\"ts\":%ld,"
        "\"state\":\"%s\",\"fault_reason\":%u,\"retry\":%d,"
        "\"flow1\":%.2f,\"flow2\":%.2f,"
        "\"p1_bar\":%.2f,\"p2_bar\":%.2f,\"p1_adc\":%d,\"p2_adc\":%d,"
        "\"pm_voltage\":%.3f,\"pb_voltage\":%.3f,\"delta_p_bar\":%s,"
        "\"tds1_ppm\":%.1f,\"tds2_ppm\":%.1f,"
        "\"tds1_mv\":%d,\"tds2_mv\":%d,\"tds1_raw\":%d,\"tds2_raw\":%d,"
        "\"d1\":%u,\"d2\":%u,\"d3\":%u,\"d4\":%u,\"d5\":%u,\"d6\":%u,"
        "\"flow_fault_armed\":%u,\"flow_fault_ms\":%lu,"
        "\"rec_fault_armed\":%u,\"rec_fault_ms\":%lu,"
        "\"diag_rem_sec\":%u,"
        "\"tr_d1\":%u,\"tr_d2\":%u,\"tr_d3\":%u,"
        "\"tr_d4\":%u,\"tr_d5\":%u,\"tr_d6\":%u,"
        "\"rssi\":%d}",
        device_id.c_str(), ts,
        c.getStateName(), (uint8_t)c.getFaultReason(), c.getRetryCount(),
        s.getFlow1(), s.getFlow2(),
        s.getPressure1(), s.getPressure2(), s.getPressure1Adc(), s.getPressure2Adc(),
        s.getPressureMembraneVoltage(), s.getPressureBrineVoltage(), dpBuf,
        s.getTDS1Ppm(), s.getTDS2Ppm(),
        s.getTDS1MvRaw(), s.getTDS2MvRaw(), s.getTDS1AdcRaw(), s.getTDS2AdcRaw(),
        (uint8_t)s.getD1(), (uint8_t)s.getD2(), (uint8_t)s.getD3(),
        (uint8_t)s.getD4(), (uint8_t)s.getD5(), (uint8_t)s.getD6(),
        (uint8_t)c.isFlowFaultArmed(), c.getFlowFaultElapsedMs(),
        (uint8_t)c.isRecoveryFaultArmed(), c.getRecoveryFaultElapsedMs(),
        diag.remainingSec(),
        tr.d[0], tr.d[1], tr.d[2], tr.d[3], tr.d[4], tr.d[5],
        (int)WiFi.RSSI());
    mqttClient.publish((String("fyntek/") + device_id + "/diag").c_str(), buf);
}

void Comms::update(Sensors &s, Control &c, Commands &cmds, DiagMode &diag, FlightRecorder &fr) {

    unsigned long now = millis();

    // ===== WIFI =====
    if (now - lastWifiCheck > 10000) {
        lastWifiCheck = now;

        // Counter-based: registra resultado de cada check (1=ok, 0=down).
        // Si >= 3 de las últimas 5 verificaciones (50s) están desconectadas, abre portal.
        bool wifiOk = (WiFi.status() == WL_CONNECTED);
        wifiCheckHistory[wifiCheckIdx] = wifiOk ? 1 : 0;
        wifiCheckIdx = (wifiCheckIdx + 1) % 5;

        if (!wifiOk) {
            Serial.println("[COMMS] WiFi reconectando...");
            WiFi.reconnect();
            ntpConfigured = false;

            uint8_t downCount = 0;
            for (uint8_t i = 0; i < 5; i++) if (wifiCheckHistory[i] == 0) downCount++;

            if (!fallbackPortalActive && downCount >= 3) {
                Serial.printf("[WIFI] %u/5 checks desconectados — portal fallback '%s' canal=%u (heap libre: %u)\n",
                    downCount, fallbackPortalSSID().c_str(), lastWifiChannel, ESP.getFreeHeap());
                wm.setConfigPortalBlocking(false);
                if (lastWifiChannel > 0) wm.setWiFiAPChannel(lastWifiChannel);
                wm.startConfigPortal(fallbackPortalSSID().c_str(), fallbackPortalPassword().c_str());
                fallbackPortalActive = true;
                lastPortalHeapLog = now;
            }
        } else {
            lastWifiChannel = WiFi.channel();
            if (!ntpConfigured) {
                configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
                ntpConfigured = true;
                Serial.println("✅ WiFi conectado — NTP sincronizado");
            }
        }
    }

    // ===== WIFI FALLBACK PORTAL =====
    // wm.process() se llama cada loop (no solo cada 10s) para que el
    // webserver/DNS del portal cautivo respondan con latencia aceptable.
    // Sin timeout propio: queda abierto mientras dure la desconexión y se
    // cierra apenas WiFi.status() vuelve a WL_CONNECTED (red original via
    // STA en background, o credenciales nuevas cargadas desde el portal).
    if (fallbackPortalActive) {
        wm.process();

        // Log periódico de heap libre — visibilidad para validar estabilidad
        // en cortes prolongados (ver WIFI_PORTAL_HEAP_LOG_SEC en config.h).
        if (now - lastPortalHeapLog >= (WIFI_PORTAL_HEAP_LOG_SEC * 1000UL)) {
            lastPortalHeapLog = now;
            Serial.printf("[WIFI] Portal fallback activo — heap libre: %u\n", ESP.getFreeHeap());
        }

        if (WiFi.status() == WL_CONNECTED) {
            wm.stopConfigPortal();
            WiFi.mode(WIFI_STA);
            fallbackPortalActive = false;
            ntpConfigured = false;  // forzar re-sync NTP en el próximo ciclo
            Serial.printf("[WIFI] Portal fallback cerrado — reconectado (heap libre: %u)\n",
                ESP.getFreeHeap());
        }
    }

    // ===== MQTT =====
    reconnect();
    mqttClient.loop();

    if (!mqttClient.connected()) return;

    long ts = getTimestamp();

    // ================= SNAPSHOT REAL =================
    if (sendSnapshot) {

        sendSnapshot = false;

        Serial.println("📸 SNAPSHOT COMPLETO");

        // ===== PROCESS =====
        {
            String json = "{";
            json += "\"device_id\":\"" + device_id + "\",";
            json += "\"fw_version\":\"" + String(fw_version) + "\",";
            json += "\"ts\":" + String(ts) + ",";
            json += "\"flow_permeate_lpm\":" + String(s.getFlow1()) + ",";
            json += "\"flow_reject_lpm\":" + String(s.getFlow2()) + ",";
            json += "\"pressure_membrane_bar\":" + String(s.getPressure1()) + ",";
            json += "\"pressure_brine_bar\":" + String(s.getPressure2()) + ",";
            json += "\"pressure_membrane_voltage\":" + String(s.getPressureMembraneVoltage(), 3) + ",";
            json += "\"pressure_brine_voltage\":" + String(s.getPressureBrineVoltage(), 3) + ",";
            json += "\"delta_p_bar\":" + floatOrNull(s.getDeltaPBar()) + ",";
            json += "\"volume_permeate_l\":" + String(s.getTotalPerm()) + ",";
            json += "\"volume_reject_l\":" + String(s.getTotalRech());
            json += "}";

            mqttClient.publish(baseTopic("process").c_str(), json.c_str());
        }

        // ===== QUALITY =====
        {
            char json[384];
            snprintf(json, sizeof(json),
                "{\"device_id\":\"%s\",\"fw_version\":\"%s\",\"ts\":%ld,"
                "\"tds_in_voltage\":%.4f,\"tds_in_ppm\":%.1f,"
                "\"tds_out_voltage\":%.4f,\"tds_out_ppm\":%.1f,"
                "\"adc1_raw\":%d,\"adc2_raw\":%d,\"adc1_mv\":%d,\"adc2_mv\":%d}",
                device_id.c_str(), fw_version, ts,
                s.getTDS1Voltage(), s.getTDS1Ppm(),
                s.getTDS2Voltage(), s.getTDS2Ppm(),
                s.getTDS1AdcRaw(), s.getTDS2AdcRaw(),
                s.getTDS1MvRaw(), s.getTDS2MvRaw());
            mqttClient.publish(baseTopic("quality").c_str(), json);
        }

        // ===== STATE =====
        {
            String json = "{";
            json += "\"device_id\":\"" + device_id + "\",";
            json += "\"fw_version\":\"" + String(fw_version) + "\",";
            json += "\"ts\":" + String(ts) + ",";
            json += "\"state\":\"" + String(c.getStateName()) + "\",";
            json += "\"running\":" + String(c.isRunning()) + ",";
            json += "\"retry\":" + String(c.getRetryCount()) + ",";
            json += "\"lockout\":" + String(c.isLockedOut() ? 1 : 0) + ",";
            json += "\"fault_reason\":\"" + String(c.getFaultReasonName()) + "\"";
            json += "}";

            mqttClient.publish(baseTopic("state").c_str(), json.c_str());
        }

        // ===== OUTPUTS =====
        publishOutputs(ts);

        // ===== INPUTS =====
        publishInputs(s, ts);

        // ===== PROCESS CONFIG (retained) =====
        {
            const ProcessConfig& pc = processConfigGet();
            char json[256];
            snprintf(json, sizeof(json),
                "{\"pressure_stabilization_delay_sec\":%u,\"startup_timeout_sec\":%u,"
                "\"retry_interval_sec\":%u,\"max_retries\":%u,\"flush_duration_sec\":%u,"
                "\"updated_at\":%u}",
                (unsigned)pc.pressure_stabilization_delay_sec,
                (unsigned)pc.startup_timeout_sec,
                (unsigned)pc.retry_interval_sec,
                (unsigned)pc.max_retries,
                (unsigned)pc.flush_duration_sec,
                (unsigned)pc.updated_at);
            mqttClient.publish(baseTopic("process_config").c_str(), json, true);
        }

        // ===== ANTIFREEZE CONFIG (retained) =====
        {
            const AntifreezeConfig& ac = antifreezeConfigGet();
            char json[384];
            snprintf(json, sizeof(json),
                "{\"enabled\":%u,\"sensor_enabled\":%u,\"sensor_gpio\":%u,"
                "\"temp_threshold_low_c\":%.1f,\"temp_threshold_high_c\":%.1f,"
                "\"flush_duration_sec\":%u,\"eval_interval_sec\":%u,\"boot_inhibit_sec\":%u,"
                "\"min_valid_temp_c\":%.1f,\"max_valid_temp_c\":%.1f,"
                "\"max_consecutive_failures\":%u,\"updated_at\":%u}",
                ac.enabled, ac.sensor_enabled, ac.sensor_gpio,
                ac.temp_threshold_low_c, ac.temp_threshold_high_c,
                (unsigned)ac.flush_duration_sec, (unsigned)ac.eval_interval_sec, (unsigned)ac.boot_inhibit_sec,
                ac.min_valid_temp_c, ac.max_valid_temp_c,
                ac.max_consecutive_failures, (unsigned)ac.updated_at);
            mqttClient.publish(baseTopic("antifreeze_config").c_str(), json, true);
        }
    }


    // ================= PROCESS =================
    if (now - lastProcess > 1000) {

        lastProcess = now;

        String json = "{";
        json += "\"device_id\":\"" + device_id + "\",";
        json += "\"fw_version\":\"" + String(fw_version) + "\",";
        json += "\"ts\":" + String(ts) + ",";

        json += "\"flow_permeate_lpm\":" + String(s.getFlow1()) + ",";
        json += "\"flow_reject_lpm\":" + String(s.getFlow2()) + ",";
        json += "\"pressure_membrane_bar\":" + String(s.getPressure1()) + ",";
        json += "\"pressure_brine_bar\":" + String(s.getPressure2()) + ",";
        json += "\"pressure_membrane_voltage\":" + String(s.getPressureMembraneVoltage(), 3) + ",";
        json += "\"pressure_brine_voltage\":" + String(s.getPressureBrineVoltage(), 3) + ",";
        json += "\"delta_p_bar\":" + floatOrNull(s.getDeltaPBar()) + ",";
        json += "\"volume_permeate_l\":" + String(s.getTotalPerm()) + ",";
        json += "\"volume_reject_l\":" + String(s.getTotalRech());

        json += "}";

        mqttClient.publish(baseTopic("process").c_str(), json.c_str());
    }

    // ================= QUALITY =================
    if (now - lastQuality > 10000) {

        lastQuality = now;

        char json[384];
        snprintf(json, sizeof(json),
            "{\"device_id\":\"%s\",\"fw_version\":\"%s\",\"ts\":%ld,"
            "\"tds_in_voltage\":%.4f,\"tds_in_ppm\":%.1f,"
            "\"tds_out_voltage\":%.4f,\"tds_out_ppm\":%.1f,"
            "\"adc1_raw\":%d,\"adc2_raw\":%d,\"adc1_mv\":%d,\"adc2_mv\":%d}",
            device_id.c_str(), fw_version, ts,
            s.getTDS1Voltage(), s.getTDS1Ppm(),
            s.getTDS2Voltage(), s.getTDS2Ppm(),
            s.getTDS1AdcRaw(), s.getTDS2AdcRaw(),
            s.getTDS1MvRaw(), s.getTDS2MvRaw());
        mqttClient.publish(baseTopic("quality").c_str(), json);
    }

    // ================= STATE =================
    static String lastStateSent = "";
    static int    lastLockoutSent = -1;  // -1 = never sent → forces first publish

    String currentState = String(c.getStateName());
    int    currentLockout = c.isLockedOut() ? 1 : 0;

    // El lockout también dispara publicación: un STOP recibido en IDLE latchea
    // sin cambiar el estado FSM — sin esta condición el backend no se entera.
    if (currentState != lastStateSent || currentLockout != lastLockoutSent
            || forceStatePublish) {

        lastStateSent   = currentState;
        lastLockoutSent = currentLockout;
        forceStatePublish = false;

        String json = "{";
        json += "\"device_id\":\"" + device_id + "\",";
        json += "\"fw_version\":\"" + String(fw_version) + "\",";
        json += "\"ts\":" + String(ts) + ",";
        json += "\"state\":\"" + currentState + "\",";
        json += "\"running\":" + String(c.isRunning()) + ",";
        json += "\"retry\":" + String(c.getRetryCount()) + ",";
        json += "\"lockout\":" + String(currentLockout) + ",";
        json += "\"fault_reason\":\"" + String(c.getFaultReasonName()) + "\"";
        json += "}";

        mqttClient.publish(baseTopic("state").c_str(), json.c_str());
    }

    // ================= OUTPUTS =================
    // Bitmask over all configured logical outputs (bit i = LogicalOutput i).
    // digitalRead on an OUTPUT-mode pin returns the last driven level on ESP32.
    {
        static uint8_t lastOutMask = 0xFF;  // invalid → forces first publish
        const IOMapConfig& outmap = ioMapGet();
        uint8_t curOutMask = 0;
        for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
            const IOPinConfig& pin = outmap.outputs[i];
            if (pin.gpio == IOMAP_GPIO_NONE) continue;
            bool raw     = digitalRead(pin.gpio);
            bool logical = pin.invert ? !raw : raw;
            if (logical) curOutMask |= (1u << i);
        }
        if (curOutMask != lastOutMask) {
            lastOutMask = curOutMask;
            publishOutputs(ts);
        }
    }

    // ================= INPUTS =================
    // Bitmask over all configured logical inputs (bit i = LogicalInput i).
    {
        static uint16_t lastInMask = 0xFFFF;  // invalid → forces first publish
        const IOMapConfig& inmap = ioMapGet();
        uint16_t curInMask = 0;
        for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
            if (inmap.inputs[i].gpio == IOMAP_GPIO_NONE) continue;
            if (s.getSignal((LogicalInput)i)) curInMask |= (1u << i);
        }
        if (curInMask != lastInMask) {
            lastInMask = curInMask;
            publishInputs(s, ts);
        }
    }

    // ================= HEARTBEAT + I/O SYNC (every 10s) =================
    // Three publications share the same timer so they are always aligned:
    //   1. /heartbeat  — connectivity + FSM state (backend uses for last_seen)
    //   2. /outputs    — real relay state (guards against QoS-0 message loss)
    //   3. /inputs     — real digital input state (same reason)
    // The on-change logic above remains the primary path for real-time updates.
    // This block guarantees convergence within 10s regardless of packet loss.
    if (now - lastHeartbeat >= 10000) {

        lastHeartbeat = now;

        // -- connectivity / FSM state --
        {
            // Edge-detector de cadencia 10s para el timestamp del último ciclo
            // de antifreeze — suficiente resolución dado que el ciclo más
            // corto razonable (flush_duration_sec, mín. 10s) no puede perderse
            // entre dos heartbeats consecutivos.
            static bool afActiveSeen = false;
            static long afLastCycleTs = 0;
            bool afActive = c.isAntifreezeActive();
            if (afActive && !afActiveSeen) afLastCycleTs = ts;
            afActiveSeen = afActive;

            String json = "{";
            json += "\"device_id\":\"" + device_id + "\",";
            json += "\"ts\":" + String(ts) + ",";
            json += "\"status\":\"online\",";
            json += "\"state\":\"" + String(c.getStateName()) + "\",";
            json += "\"activity\":\"" + String(c.getActivityName()) + "\",";
            json += "\"fault_reason\":\"" + String(c.getFaultReasonName()) + "\",";
            json += "\"antifreeze_active\":" + String(afActive) + ",";
            json += "\"antifreeze_sensor_fault\":" + String(antifreezeIsSensorFault()) + ",";
            json += "\"ambient_temp_c\":" + floatOrNull(antifreezeGetTempC()) + ",";
            json += "\"ambient_humidity_pct\":" + floatOrNull(antifreezeGetHumidityPct()) + ",";
            json += "\"antifreeze_last_cycle_ts\":" + String(afLastCycleTs);
            json += "}";
            mqttClient.publish(baseTopic("heartbeat").c_str(), json.c_str());
        }

        publishOutputs(ts);
        publishInputs(s, ts);
    }

    // ================= CMD ACK =================
    // Published after control.update() has set the pending ACK this iteration.
    // Uses snprintf into a stack buffer — no heap, no String.
    // clearAck() is called ONLY on successful publish. If publish() fails
    // (disconnect, buffer full, TCP error), the ACK stays in RAM and is
    // retried on the next loop iteration, preventing silent TIMEOUT on backend.
    if (cmds.hasPendingAck() && mqttClient.connected()) {
        const PendingAck& ack = cmds.getPendingAck();
        char json[256];
        snprintf(json, sizeof(json),
            "{\"command_id\":\"%s\",\"device_id\":\"%s\","
            "\"cmd\":\"%s\",\"ack\":\"%s\",\"reason\":\"%s\",\"ts\":%ld}",
            ack.command_id,
            device_id.c_str(),
            Commands::cmdToString(ack.cmd_type),
            Commands::ackStatusToString(ack.status),
            ack.reason,
            ts);
        bool ok = mqttClient.publish(baseTopic("cmd/ack").c_str(), json);
        if (ok) {
            cmds.clearAck();
        } else {
            Serial.println("[CMD] ACK publish failed, retry next loop");
        }
    }

    // ================= FLIGHT RECORDER — 1 Hz sample =================
    static unsigned long lastFrSample = 0;
    if (now - lastFrSample >= 1000) {
        lastFrSample = now;

        uint8_t in_byte =
            (s.getD1() ? 0x01 : 0) | (s.getD2() ? 0x02 : 0) |
            (s.getD3() ? 0x04 : 0) | (s.getD4() ? 0x08 : 0) |
            (s.getD5() ? 0x10 : 0) | (s.getD6() ? 0x20 : 0);
        OutputsState fout = c.getOutputs();
        uint8_t out_byte =
            (fout.pumpLow    ? 0x01 : 0) | (fout.pumpHigh   ? 0x02 : 0) |
            (fout.pumpInlet  ? 0x04 : 0) | (fout.pumpDose   ? 0x08 : 0) |
            (fout.valveFlush ? 0x10 : 0) | (fout.valveInlet ? 0x20 : 0);
        uint8_t flags_byte =
            (diag.isActive()           ? 0x01 : 0) |
            (c.isFlowFaultArmed()      ? 0x02 : 0) |
            (c.isRecoveryFaultArmed()  ? 0x04 : 0);

        FlightRecord rec;
        rec.ts           = (uint32_t)ts;
        rec.state        = (uint8_t)c.getState();
        rec.fault_reason = (uint8_t)c.getFaultReason();
        rec.retry_count  = (uint8_t)c.getRetryCount();
        rec.flags        = flags_byte;
        rec.flow1_lpm    = s.getFlow1();
        rec.flow2_lpm    = s.getFlow2();
        rec.p1_adc       = (int16_t)s.getPressure1Adc();
        rec.p2_adc       = (int16_t)s.getPressure2Adc();
        rec.p1_bar       = s.getPressure1();
        rec.p2_bar       = s.getPressure2();
        rec.tds1_raw     = (int16_t)s.getTDS1AdcRaw();
        rec.tds2_raw     = (int16_t)s.getTDS2AdcRaw();
        rec.tds1_mv      = (int16_t)s.getTDS1MvRaw();
        rec.tds2_mv      = (int16_t)s.getTDS2MvRaw();
        rec.inputs       = in_byte;
        rec.outputs      = out_byte;
        rec.rssi         = (int8_t)WiFi.RSSI();
        rec._pad         = 0;
        fr.record(rec);
    }

    // ================= FAULT EVENT =================
    // consumeFaultEvent() returns true exactly once per FAULT transition.
    // captureFault() marks the buffer; publishFaultEvent() sends it immediately.
    if (c.consumeFaultEvent()) {
        fr.captureFault((uint8_t)c.getFaultReason(), (uint32_t)ts);
    }
    if (fr.hasPendingFaultEvent()) {
        fr.publishFaultEvent(mqttClient, device_id);
    }

    // ================= DIAG EXPIRY =================
    if (diag.hasExpired()) {
        diag.deactivate();
        Serial.println("[DIAG] Auto-expirado");
    }

    // ================= DIAG TRANSITIONS =================
    // Always polled so counters are accurate regardless of publish rate.
    diag.updateInputs(s.getD1(), s.getD2(), s.getD3(),
                      s.getD4(), s.getD5(), s.getD6());

    // ================= DIAG PUBLISH (1 Hz, only when active) =================
    static unsigned long lastDiagPub = 0;
    if (diag.isActive() && now - lastDiagPub >= 1000) {
        lastDiagPub = now;
        publishDiag(s, c, diag, ts);
    }

    // ================= FLIGHT RECORDER DUMP (one chunk per 500 ms) =================
    static unsigned long lastDumpChunk = 0;
    if (fr.isDumping() && now - lastDumpChunk >= 500) {
        lastDumpChunk = now;
        fr.publishNextChunk(mqttClient, device_id);
    }
}