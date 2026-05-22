#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include <config.h>

// ── Sensor calibration config ────────────────────────────────────────────────
// Pushed from backend via MQTT fyntek/{device_id}/config (retained).
// Persisted in NVS namespace "kx_cfg" so it survives reboots.
//
// Integrity: magic and version are stored alongside calibration data in NVS.
// loadConfig() rejects any block where magic != CFG_MAGIC or version != CFG_VERSION.
// updated_at is the authoritative freshness field: config is applied only when
// incoming.updated_at > current.updated_at (or incoming carries no timestamp).

struct SensorConfig {
    uint32_t      magic           = CFG_MAGIC;             // integrity sentinel
    uint32_t      version         = CFG_VERSION;           // layout version
    float         flow_factor_1   = FLOW_FACTOR_DEFAULT;   // pulsos/litro — caudalímetro permeado
    float         flow_factor_2   = FLOW_FACTOR_DEFAULT;   // pulsos/litro — caudalímetro rechazo
    float         tds_temperature = TDS_TEMPERATURE_DEFAULT; // °C — compensación térmica TDS
    unsigned long updated_at      = 0;                     // unix timestamp del último update
};

class Sensors {
public:
    void begin();
    void update();

    // ── Telemetry ────────────────────────────────────────────────────────────
    float getFlow1();
    float getFlow2();
    float getPressure1();
    float getPressure2();

    float getTDS1Voltage();   // V, calibrated
    float getTDS2Voltage();
    float getTDS1Ppm();       // ppm, temperature-compensated
    float getTDS2Ppm();

    float getTotalPerm();
    float getTotalRech();

    // ── Digital inputs ───────────────────────────────────────────────────────
    bool getD1();
    bool getD2();
    bool getD3();
    bool getD4();
    bool getD5();
    bool getD6();

    bool getDemand();
    bool getCrudoOK();
    bool getDoseOK();
    bool getPresostato();

    bool demanda();
    bool crudoDisponible();
    bool presionOK();

    // ── Config management ────────────────────────────────────────────────────
    // setConfig(): validates, compares updated_at, applies, persists to NVS.
    // Returns true if the incoming config was accepted and applied.
    bool         setConfig(const SensorConfig& incoming);

    // resetConfig(): restores compile-time defaults, clears NVS "kx_cfg".
    // Called on MQTT reset command or physical button. Never leaves device
    // in an unusable state.
    void         resetConfig();

    SensorConfig getConfig() const;

private:
    float flow1 = 0, flow2 = 0;
    float p1 = 0,    p2 = 0;
    float tds1_v = 0, tds2_v = 0;   // filtered voltage (V)
    float tds1_ppm = 0, tds2_ppm = 0;

    float totalPerm = 0, totalRech = 0;
    float lastSavedPerm = 0, lastSavedRech = 0;
    unsigned long lastSaveTime = 0;

    bool d1, d2, d3, d4, d5, d6;

    Preferences prefs;  // used exclusively for totals (namespace "kairox")

    SensorConfig _cfg;  // active runtime config

    void loadTotals();
    void saveTotals();
    void loadConfig();
    void saveConfig();

    static bool  isValidConfig(const SensorConfig& c);
    static float voltageToPpm(float voltage, float temperature);
    static float median5(const float* buf);

    static void IRAM_ATTR isrQ1();
    static void IRAM_ATTR isrQ2();
    static volatile unsigned long pulsesQ1;
    static volatile unsigned long pulsesQ2;

    unsigned long lastFlowTime = 0;

    // Pressure EWMA
    float p1_f = 0, p2_f = 0;

    // TDS: 5-sample circular median buffer (stores voltage)
    static constexpr int TDS_BUF = 5;
    float tds1_buf[TDS_BUF] = {0};
    float tds2_buf[TDS_BUF] = {0};
    int   tds_idx = 0;
};
