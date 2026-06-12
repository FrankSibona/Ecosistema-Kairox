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
    // ── Calibración TDS voltaje→ppm, por canal (CAL_MODE_LINEAR) ─────────────
    // slope == 0 → sin calibración cargada, fallback a voltageToPpm() (DFRobot).
    float         tds1_cal_slope  = TDS1_CAL_SLOPE_DEFAULT;
    float         tds1_cal_offset = TDS1_CAL_OFFSET_DEFAULT;
    float         tds2_cal_slope  = TDS2_CAL_SLOPE_DEFAULT;
    float         tds2_cal_offset = TDS2_CAL_OFFSET_DEFAULT;
    // ── Protecciones de proceso ──────────────────────────────────────────────
    float         min_flow_lpm              = MIN_FLOW_LPM_DEFAULT;
    float         max_flow_lpm              = MAX_FLOW_LPM_DEFAULT;
    uint32_t      flow_fault_delay_sec      = FLOW_FAULT_DELAY_SEC_DEFAULT;
    float         min_recovery_pct          = MIN_RECOVERY_PCT_DEFAULT;
    float         max_recovery_pct          = MAX_RECOVERY_PCT_DEFAULT;
    uint32_t      recovery_fault_delay_sec  = RECOVERY_FAULT_DELAY_SEC_DEFAULT;
    // ────────────────────────────────────────────────────────────────────────
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

    // Debug instrumentation — raw ADC values before filtering.
    // adc_raw: 12-bit count (0–4095) from analogRead().
    // mv_raw:  millivolts from analogReadMilliVolts() before /1000 conversion.
    // Remove after TDS ADC investigation is complete.
    int   getTDS1AdcRaw();
    int   getTDS2AdcRaw();
    int   getTDS1MvRaw();
    int   getTDS2MvRaw();

    float getTotalPerm();
    float getTotalRech();

    // Pressure ADC raw counts (0–4095) — for flight recorder and diagnostic
    int   getPressure1Adc();
    int   getPressure2Adc();

    // Pulse counts from the last completed 1 s flow window
    unsigned long getLastPulses1();
    unsigned long getLastPulses2();

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
    bool getNivelBajoPozo();   // D5 — flotante nivel bajo; HIGH = cisterna baja → bomba pozo ON

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

    // Debug — single latest raw sample (updated every loop iteration).
    int   tds1_adc_raw = 0, tds2_adc_raw = 0;  // 12-bit counts
    int   tds1_mv_raw  = 0, tds2_mv_raw  = 0;  // analogReadMilliVolts (pre /1000)

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

    // Legacy fallback: DFRobot SEN0244 polynomial. Used when no per-channel
    // calibration is loaded (slope == 0).
    static float voltageToPpm(float voltage, float temperature);

    // Calibration layer (voltage → ppm). Currently implements:
    //   - CAL_MODE_LINEAR (slope > 0): ppm = slope * mV + offset
    //   - CAL_MODE_LEGACY (slope == 0): voltageToPpm() fallback
    // Future modes (CAL_MODE_POLYNOMIAL, CAL_MODE_LOOKUP_TABLE) plug in here
    // without touching callers or the NVS/MQTT/backend config plumbing.
    static float calibrateTdsPpm(float voltage, float temperature, float slope, float offset);

    static float median5(const float* buf);

    static void IRAM_ATTR isrQ1();
    static void IRAM_ATTR isrQ2();
    static volatile unsigned long pulsesQ1;
    static volatile unsigned long pulsesQ2;

    unsigned long lastFlowTime = 0;

    // Pressure EWMA
    float p1_f = 0, p2_f = 0;
    int   p1_adc = 0, p2_adc = 0;          // raw ADC counts for pressure channels
    unsigned long last_pulses1 = 0, last_pulses2 = 0;  // pulses in last 1 s window

    // TDS: 5-sample circular median buffer (stores voltage)
    static constexpr int TDS_BUF = 5;
    float tds1_buf[TDS_BUF] = {0};
    float tds2_buf[TDS_BUF] = {0};
    int   tds_idx = 0;
};
