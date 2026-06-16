#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include <math.h>  // NAN
#include <config.h>
#include "io/io_map.h"

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
    uint8_t       flow_protection_enabled   = FLOW_PROTECTION_ENABLED_DEFAULT;
    float         min_recovery_pct          = MIN_RECOVERY_PCT_DEFAULT;
    float         max_recovery_pct          = MAX_RECOVERY_PCT_DEFAULT;
    uint32_t      recovery_fault_delay_sec  = RECOVERY_FAULT_DELAY_SEC_DEFAULT;
    uint8_t       recovery_protection_enabled = RECOVERY_PROTECTION_ENABLED_DEFAULT;
    // ── Calibración de presión (voltaje→bar), por canal ──────────────────────
    // *_enabled=0 (default): pressure_membrane_bar/pressure_brine_bar usan la
    // fórmula legacy (sin cambios). *_enabled=1: calibración lineal min/max.
    uint8_t       pressure_membrane_enabled        = PRESSURE_MEMBRANE_ENABLED_DEFAULT;
    float         pressure_membrane_min_voltage    = PRESSURE_MEMBRANE_MIN_VOLTAGE_DEFAULT;
    float         pressure_membrane_max_voltage    = PRESSURE_MEMBRANE_MAX_VOLTAGE_DEFAULT;
    float         pressure_membrane_min_bar        = PRESSURE_MEMBRANE_MIN_BAR_DEFAULT;
    float         pressure_membrane_max_bar        = PRESSURE_MEMBRANE_MAX_BAR_DEFAULT;
    uint8_t       pressure_membrane_limits_enabled = PRESSURE_MEMBRANE_LIMITS_ENABLED_DEFAULT;
    float         pressure_membrane_high_limit     = PRESSURE_MEMBRANE_HIGH_LIMIT_DEFAULT;
    uint32_t      pressure_fault_delay_sec         = PRESSURE_FAULT_DELAY_SEC_DEFAULT;
    uint8_t       pressure_brine_enabled           = PRESSURE_BRINE_ENABLED_DEFAULT;
    float         pressure_brine_min_voltage       = PRESSURE_BRINE_MIN_VOLTAGE_DEFAULT;
    float         pressure_brine_max_voltage       = PRESSURE_BRINE_MAX_VOLTAGE_DEFAULT;
    float         pressure_brine_min_bar           = PRESSURE_BRINE_MIN_BAR_DEFAULT;
    float         pressure_brine_max_bar           = PRESSURE_BRINE_MAX_BAR_DEFAULT;
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

    // Pressure: filtered channel voltage (V), siempre calculado (independiente
    // de *_enabled). Útil para calibración y diagnóstico.
    float getPressureMembraneVoltage();
    float getPressureBrineVoltage();

    // delta_p_bar = pressure_membrane_bar - pressure_brine_bar.
    // NAN si pressure_membrane_enabled o pressure_brine_enabled es false
    // (no se publica un valor artificial).
    float getDeltaPBar();

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

    // Lectura desacoplada vía io_map — resuelve GPIO/modo/invert/default_value
    // para la señal lógica indicada y aplica el debounce simétrico configurado
    // (debounce_ms, 0 = sin debounce). gpio==IOMAP_GPIO_NONE -> default_value.
    // Calculada una vez por loop en update() (cacheada). Usada por el motor de
    // reglas (rules.h) para construir ruleInputs[] y por la FSM (reemplaza
    // demanda()/crudoDisponible()/presionOK()).
    bool getSignal(LogicalInput sig) const;

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

    // ── Estado de debounce simétrico por señal lógica (getSignal()) ──────────
    bool          _sigRaw[(uint8_t)LogicalInput::COUNT]    = {false};
    bool          _sigStable[(uint8_t)LogicalInput::COUNT] = {false};
    unsigned long _sigEdgeStart[(uint8_t)LogicalInput::COUNT] = {0};
    void updateSignals();

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

    // Calibración lineal genérica voltaje→valor físico, usada para presión.
    static float calibrateLinear(float v, float v_min, float v_max, float y_min, float y_max);

    static float median5(const float* buf);

    static void IRAM_ATTR isrQ1();
    static void IRAM_ATTR isrQ2();
    static volatile unsigned long pulsesQ1;
    static volatile unsigned long pulsesQ2;

    unsigned long lastFlowTime = 0;

    // Pressure EWMA (legacy bar, usado cuando *_enabled=false)
    float p1_f = 0, p2_f = 0;
    // Pressure: filtered channel voltage (V), siempre activo
    float pm_v_f = 0, pb_v_f = 0;
    // delta_p_bar — NAN si no ambos canales habilitados
    float dp_bar = NAN;
    int   p1_adc = 0, p2_adc = 0;          // raw ADC counts for pressure channels
    unsigned long last_pulses1 = 0, last_pulses2 = 0;  // pulses in last 1 s window

    // TDS: 5-sample circular median buffer (stores voltage)
    static constexpr int TDS_BUF = 5;
    float tds1_buf[TDS_BUF] = {0};
    float tds2_buf[TDS_BUF] = {0};
    int   tds_idx = 0;
};
