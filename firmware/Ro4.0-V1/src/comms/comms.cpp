#include "comms.h"
#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <string.h>

#include "../sensors/sensors.h"
#include "../control/control.h"
#include "../commands/commands.h"
#include <config.h>

// ================= DEVICE ID =================
// Must be declared before mqttCallback — static free functions only see
// names declared above their definition in the same translation unit.

String device_id;

// ================= COMMAND CALLBACK =================

static Commands* s_cmds    = nullptr;
static Sensors*  s_sensors = nullptr;

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

        StaticJsonDocument<256> doc;
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
        incoming.min_flow_lpm              = doc["min_flow_lpm"]              | cur.min_flow_lpm;
        incoming.max_flow_lpm              = doc["max_flow_lpm"]              | cur.max_flow_lpm;
        incoming.flow_fault_delay_sec      = doc["flow_fault_delay_sec"]      | cur.flow_fault_delay_sec;
        incoming.min_recovery_pct          = doc["min_recovery_pct"]          | cur.min_recovery_pct;
        incoming.max_recovery_pct          = doc["max_recovery_pct"]          | cur.max_recovery_pct;
        incoming.recovery_fault_delay_sec  = doc["recovery_fault_delay_sec"]  | cur.recovery_fault_delay_sec;
        incoming.updated_at                = doc["updated_at"]                | (unsigned long)0;

        s_sensors->setConfig(incoming);
        return;
    }
}

// ================= CONFIG =================

const char* mqtt_server = "159.112.132.176";
const int mqtt_port = 1883;

const char* mqtt_user = "kairox";
const char* mqtt_pass = "admin0102";

const char* fw_version = "1.1.0";

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

// ================= HELPERS =================

long getTimestamp() {
    struct tm timeinfo;
    if (!getLocalTime(&timeinfo)) return 0;
    return mktime(&timeinfo);
}

String baseTopic(String sub) {
    return "fyntek/" + device_id + "/" + sub;
}

// ── I/O publish helpers ───────────────────────────────────────────────────────
// Single source of truth for /outputs and /inputs payloads.
// Called from both on-change detection and periodic heartbeat so the two
// paths can never diverge when fields are added or renamed.

static void publishOutputs(Control& c, long ts) {
    OutputsState out = c.getOutputs();
    String json = "{";
    json += "\"device_id\":\"" + device_id + "\",";
    json += "\"ts\":" + String(ts) + ",";
    json += "\"pump_low\":"    + String(out.pumpLow)    + ",";
    json += "\"pump_high\":"   + String(out.pumpHigh)   + ",";
    json += "\"pump_inlet\":"  + String(out.pumpInlet)  + ",";
    json += "\"pump_dose\":"   + String(out.pumpDose)   + ",";
    json += "\"valve_flush\":" + String(out.valveFlush) + ",";
    json += "\"valve_inlet\":" + String(out.valveInlet);
    json += "}";
    mqttClient.publish(baseTopic("outputs").c_str(), json.c_str());
}

static void publishInputs(Sensors& s, long ts) {
    String json = "{";
    json += "\"device_id\":\"" + device_id + "\",";
    json += "\"ts\":" + String(ts) + ",";
    json += "\"demand\":"              + String(s.getD1()) + ",";
    json += "\"raw_water_ok\":"        + String(s.getD2()) + ",";
    json += "\"dose_ok\":"             + String(s.getD3()) + ",";
    json += "\"pressure_switch\":"     + String(s.getD4()) + ",";
    json += "\"feed_tank_level_low\":" + String(s.getD5()) + ",";
    json += "\"spare2\":"              + String(s.getD6());
    json += "}";
    mqttClient.publish(baseTopic("inputs").c_str(), json.c_str());
}

// ================= WIFI =================

void setupWiFi() {
    WiFi.mode(WIFI_STA);

    if (!wm.autoConnect("FYNTEK_SETUP")) {
        Serial.println("❌ No WiFi → restart");
        delay(3000);
        ESP.restart();
    }

    Serial.println("✅ WiFi conectado");

    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
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

        // 🔥 SNAPSHOT REAL
        sendSnapshot = true;

    } else {
        Serial.println("❌ MQTT reconectando...");
    }
}

// ================= INIT =================

void Comms::begin(Commands &cmds, Sensors &s) {

    Serial.println("[COMMS] Init");

    device_id = getDeviceID();

    Serial.print("DEVICE ID: ");
    Serial.println(device_id);

    setupWiFi();

    s_cmds    = &cmds;
    s_sensors = &s;
    mqttClient.setServer(mqtt_server, mqtt_port);
    mqttClient.setCallback(mqttCallback);
}

// ================= UPDATE =================

void Comms::update(Sensors &s, Control &c, Commands &cmds) {

    unsigned long now = millis();

    // ===== WIFI =====
    if (now - lastWifiCheck > 10000) {
        lastWifiCheck = now;

        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("[COMMS] WiFi reconectando...");
            WiFi.reconnect();
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
            json += "\"volume_permeate_l\":" + String(s.getTotalPerm()) + ",";
            json += "\"volume_reject_l\":" + String(s.getTotalRech());
            json += "}";

            mqttClient.publish(baseTopic("process").c_str(), json.c_str());
        }

        // ===== QUALITY =====
        {
            char json[256];
            snprintf(json, sizeof(json),
                "{\"device_id\":\"%s\",\"fw_version\":\"%s\",\"ts\":%ld,"
                "\"tds_in_voltage\":%.4f,\"tds_in_ppm\":%.1f,"
                "\"tds_out_voltage\":%.4f,\"tds_out_ppm\":%.1f}",
                device_id.c_str(), fw_version, ts,
                s.getTDS1Voltage(), s.getTDS1Ppm(),
                s.getTDS2Voltage(), s.getTDS2Ppm());
            mqttClient.publish(baseTopic("quality").c_str(), json);
        }

        // ===== STATE =====
        {
            String json = "{";
            json += "\"device_id\":\"" + device_id + "\",";
            json += "\"ts\":" + String(ts) + ",";
            json += "\"state\":\"" + String(c.getStateName()) + "\",";
            json += "\"running\":" + String(c.isRunning()) + ",";
            json += "\"retry\":" + String(c.getRetryCount()) + ",";
            json += "\"fault_reason\":\"" + String(c.getFaultReasonName()) + "\"";
            json += "}";

            mqttClient.publish(baseTopic("state").c_str(), json.c_str());
        }

        // ===== OUTPUTS =====
        publishOutputs(c, ts);

        // ===== INPUTS =====
        publishInputs(s, ts);
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
        json += "\"volume_permeate_l\":" + String(s.getTotalPerm()) + ",";
        json += "\"volume_reject_l\":" + String(s.getTotalRech());

        json += "}";

        mqttClient.publish(baseTopic("process").c_str(), json.c_str());
    }

    // ================= QUALITY =================
    if (now - lastQuality > 10000) {

        lastQuality = now;

        char json[256];
        snprintf(json, sizeof(json),
            "{\"device_id\":\"%s\",\"fw_version\":\"%s\",\"ts\":%ld,"
            "\"tds_in_voltage\":%.4f,\"tds_in_ppm\":%.1f,"
            "\"tds_out_voltage\":%.4f,\"tds_out_ppm\":%.1f}",
            device_id.c_str(), fw_version, ts,
            s.getTDS1Voltage(), s.getTDS1Ppm(),
            s.getTDS2Voltage(), s.getTDS2Ppm());
        mqttClient.publish(baseTopic("quality").c_str(), json);
    }

    // ================= STATE =================
    static String lastStateSent = "";

    String currentState = String(c.getStateName());

    if (currentState != lastStateSent) {

        lastStateSent = currentState;

        String json = "{";
        json += "\"device_id\":\"" + device_id + "\",";
        json += "\"ts\":" + String(ts) + ",";
        json += "\"state\":\"" + currentState + "\",";
        json += "\"running\":" + String(c.isRunning()) + ",";
        json += "\"retry\":" + String(c.getRetryCount()) + ",";
        json += "\"fault_reason\":\"" + String(c.getFaultReasonName()) + "\"";
        json += "}";

        mqttClient.publish(baseTopic("state").c_str(), json.c_str());
    }

    // ================= OUTPUTS =================
    static OutputsState lastOut = {0};

    OutputsState out = c.getOutputs();

    if (memcmp(&out, &lastOut, sizeof(out)) != 0) {
        lastOut = out;
        publishOutputs(c, ts);
    }

    // ================= INPUTS =================
    static String lastInputs = "";

    String inputs = String(s.getD1()) + String(s.getD2()) + String(s.getD3()) +
                    String(s.getD4()) + String(s.getD5()) + String(s.getD6());

    if (inputs != lastInputs) {
        lastInputs = inputs;
        publishInputs(s, ts);
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
            String json = "{";
            json += "\"device_id\":\"" + device_id + "\",";
            json += "\"ts\":" + String(ts) + ",";
            json += "\"status\":\"online\",";
            json += "\"state\":\"" + String(c.getStateName()) + "\"";
            json += "}";
            mqttClient.publish(baseTopic("heartbeat").c_str(), json.c_str());
        }

        publishOutputs(c, ts);
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
}