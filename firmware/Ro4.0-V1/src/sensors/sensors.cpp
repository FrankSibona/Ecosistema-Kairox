#include "sensors.h"
#include <math.h>  // isnan, isinf
// config.h included transitively via sensors.h

// ── Statics ──────────────────────────────────────────────────────────────────
volatile unsigned long Sensors::pulsesQ1 = 0;
volatile unsigned long Sensors::pulsesQ2 = 0;

// ── ISR ──────────────────────────────────────────────────────────────────────
void IRAM_ATTR Sensors::isrQ1() { pulsesQ1++; }
void IRAM_ATTR Sensors::isrQ2() { pulsesQ2++; }

// ── NVS: volume totals ────────────────────────────────────────────────────────
// Namespace "kairox" — totals only. Independent from config namespace.

void Sensors::loadTotals() {
    prefs.begin("kairox", true);
    totalPerm = prefs.getFloat("perm", 0);
    totalRech = prefs.getFloat("rech", 0);
    prefs.end();
    lastSavedPerm = totalPerm;
    lastSavedRech = totalRech;
    Serial.println("[NVS] Totales cargados");
}

void Sensors::saveTotals() {
    prefs.begin("kairox", false);
    prefs.putFloat("perm", totalPerm);
    prefs.putFloat("rech", totalRech);
    prefs.end();
    lastSavedPerm = totalPerm;
    lastSavedRech = totalRech;
}

// ── NVS: sensor config ────────────────────────────────────────────────────────
// Namespace "kx_cfg" — calibration factors only.
// Guards against NVS corruption: if persisted values fail validation,
// defaults are used and NVS is silently corrected on next saveConfig().

void Sensors::loadConfig() {
    Preferences p;
    p.begin("kx_cfg", true);

    // Read integrity fields first. If they don't match, discard everything —
    // the rest of the stored data may be partially written or from old firmware.
    uint32_t stored_magic   = p.getUInt("magic",   0);
    uint32_t stored_version = p.getUInt("version", 0);

    if (stored_magic != CFG_MAGIC || stored_version != CFG_VERSION) {
        p.end();
        Serial.printf("[CFG] NVS: magic=0x%08X ver=%u — inválido, usando defaults\n",
                      stored_magic, stored_version);
        _cfg = SensorConfig{};
        return;
    }

    SensorConfig loaded;
    loaded.flow_factor_1             = p.getFloat ("ff1",    FLOW_FACTOR_DEFAULT);
    loaded.flow_factor_2             = p.getFloat ("ff2",    FLOW_FACTOR_DEFAULT);
    loaded.tds_temperature           = p.getFloat ("tds_t",  TDS_TEMPERATURE_DEFAULT);
    loaded.tds1_cal_slope             = p.getFloat ("t1_sl",  TDS1_CAL_SLOPE_DEFAULT);
    loaded.tds1_cal_offset            = p.getFloat ("t1_of",  TDS1_CAL_OFFSET_DEFAULT);
    loaded.tds2_cal_slope             = p.getFloat ("t2_sl",  TDS2_CAL_SLOPE_DEFAULT);
    loaded.tds2_cal_offset            = p.getFloat ("t2_of",  TDS2_CAL_OFFSET_DEFAULT);
    loaded.min_flow_lpm              = p.getFloat ("min_fl", MIN_FLOW_LPM_DEFAULT);
    loaded.max_flow_lpm              = p.getFloat ("max_fl", MAX_FLOW_LPM_DEFAULT);
    loaded.flow_fault_delay_sec      = p.getUInt  ("flt_d",  FLOW_FAULT_DELAY_SEC_DEFAULT);
    loaded.min_recovery_pct          = p.getFloat ("min_rec",MIN_RECOVERY_PCT_DEFAULT);
    loaded.max_recovery_pct          = p.getFloat ("max_rec",MAX_RECOVERY_PCT_DEFAULT);
    loaded.recovery_fault_delay_sec  = p.getUInt  ("rec_d",  RECOVERY_FAULT_DELAY_SEC_DEFAULT);
    loaded.pressure_membrane_enabled        = p.getUChar("pm_en",   PRESSURE_MEMBRANE_ENABLED_DEFAULT);
    loaded.pressure_membrane_min_voltage    = p.getFloat("pm_minv", PRESSURE_MEMBRANE_MIN_VOLTAGE_DEFAULT);
    loaded.pressure_membrane_max_voltage    = p.getFloat("pm_maxv", PRESSURE_MEMBRANE_MAX_VOLTAGE_DEFAULT);
    loaded.pressure_membrane_min_bar        = p.getFloat("pm_minb", PRESSURE_MEMBRANE_MIN_BAR_DEFAULT);
    loaded.pressure_membrane_max_bar        = p.getFloat("pm_maxb", PRESSURE_MEMBRANE_MAX_BAR_DEFAULT);
    loaded.pressure_membrane_limits_enabled = p.getUChar("pm_lim",  PRESSURE_MEMBRANE_LIMITS_ENABLED_DEFAULT);
    loaded.pressure_membrane_high_limit     = p.getFloat("pm_hi",   PRESSURE_MEMBRANE_HIGH_LIMIT_DEFAULT);
    loaded.pressure_fault_delay_sec         = p.getUInt ("p_fdly",  PRESSURE_FAULT_DELAY_SEC_DEFAULT);
    loaded.pressure_brine_enabled           = p.getUChar("pb_en",   PRESSURE_BRINE_ENABLED_DEFAULT);
    loaded.pressure_brine_min_voltage       = p.getFloat("pb_minv", PRESSURE_BRINE_MIN_VOLTAGE_DEFAULT);
    loaded.pressure_brine_max_voltage       = p.getFloat("pb_maxv", PRESSURE_BRINE_MAX_VOLTAGE_DEFAULT);
    loaded.pressure_brine_min_bar           = p.getFloat("pb_minb", PRESSURE_BRINE_MIN_BAR_DEFAULT);
    loaded.pressure_brine_max_bar           = p.getFloat("pb_maxb", PRESSURE_BRINE_MAX_BAR_DEFAULT);
    loaded.updated_at                = p.getULong ("ts",     0);
    p.end();

    if (!isValidConfig(loaded)) {
        Serial.println("[CFG] NVS: valores fuera de rango — usando defaults");
        _cfg = SensorConfig{};
        return;
    }

    _cfg = loaded;
    Serial.printf("[CFG] Cargado: ff1=%.1f ff2=%.1f tds_t=%.1f "
                  "tds1_cal=%.4f/%.2f tds2_cal=%.4f/%.2f "
                  "min_fl=%.2f max_fl=%.1f flt_d=%u "
                  "min_rec=%.1f max_rec=%.1f rec_d=%u ts=%lu\n",
                  _cfg.flow_factor_1, _cfg.flow_factor_2, _cfg.tds_temperature,
                  _cfg.tds1_cal_slope, _cfg.tds1_cal_offset,
                  _cfg.tds2_cal_slope, _cfg.tds2_cal_offset,
                  _cfg.min_flow_lpm, _cfg.max_flow_lpm, _cfg.flow_fault_delay_sec,
                  _cfg.min_recovery_pct, _cfg.max_recovery_pct,
                  _cfg.recovery_fault_delay_sec, _cfg.updated_at);
}

void Sensors::saveConfig() {
    Preferences p;
    p.begin("kx_cfg", false);
    p.putUInt ("magic",   _cfg.magic);
    p.putUInt ("version", _cfg.version);
    p.putFloat("ff1",     _cfg.flow_factor_1);
    p.putFloat("ff2",     _cfg.flow_factor_2);
    p.putFloat("tds_t",   _cfg.tds_temperature);
    p.putFloat("t1_sl",   _cfg.tds1_cal_slope);
    p.putFloat("t1_of",   _cfg.tds1_cal_offset);
    p.putFloat("t2_sl",   _cfg.tds2_cal_slope);
    p.putFloat("t2_of",   _cfg.tds2_cal_offset);
    p.putFloat("min_fl",  _cfg.min_flow_lpm);
    p.putFloat("max_fl",  _cfg.max_flow_lpm);
    p.putUInt ("flt_d",   _cfg.flow_fault_delay_sec);
    p.putFloat("min_rec", _cfg.min_recovery_pct);
    p.putFloat("max_rec", _cfg.max_recovery_pct);
    p.putUInt ("rec_d",   _cfg.recovery_fault_delay_sec);
    p.putUChar("pm_en",   _cfg.pressure_membrane_enabled);
    p.putFloat("pm_minv", _cfg.pressure_membrane_min_voltage);
    p.putFloat("pm_maxv", _cfg.pressure_membrane_max_voltage);
    p.putFloat("pm_minb", _cfg.pressure_membrane_min_bar);
    p.putFloat("pm_maxb", _cfg.pressure_membrane_max_bar);
    p.putUChar("pm_lim",  _cfg.pressure_membrane_limits_enabled);
    p.putFloat("pm_hi",   _cfg.pressure_membrane_high_limit);
    p.putUInt ("p_fdly",  _cfg.pressure_fault_delay_sec);
    p.putUChar("pb_en",   _cfg.pressure_brine_enabled);
    p.putFloat("pb_minv", _cfg.pressure_brine_min_voltage);
    p.putFloat("pb_maxv", _cfg.pressure_brine_max_voltage);
    p.putFloat("pb_minb", _cfg.pressure_brine_min_bar);
    p.putFloat("pb_maxb", _cfg.pressure_brine_max_bar);
    p.putULong("ts",      _cfg.updated_at);
    p.end();
}

// ── Config management ─────────────────────────────────────────────────────────

// Validates magic, version, and all float fields.
// A freshly constructed SensorConfig{} always passes (defaults are valid).
// An MQTT-received config also passes (magic/version initialized by default ctor).
// A corrupted NVS block fails on magic/version before floats are even checked.
bool Sensors::isValidConfig(const SensorConfig& c) {
    if (c.magic != CFG_MAGIC || c.version != CFG_VERSION) return false;
    auto inRange = [](float v, float lo, float hi) -> bool {
        return !isnan(v) && !isinf(v) && v >= lo && v <= hi;
    };
    return inRange(c.flow_factor_1,       10.0f, 5000.0f)
        && inRange(c.flow_factor_2,       10.0f, 5000.0f)
        && inRange(c.tds_temperature,      0.0f,   80.0f)
        && inRange(c.tds1_cal_slope,       0.0f,   10.0f)   // 0 = sin calibración (fallback)
        && inRange(c.tds1_cal_offset,   -500.0f,  500.0f)
        && inRange(c.tds2_cal_slope,       0.0f,   10.0f)
        && inRange(c.tds2_cal_offset,   -500.0f,  500.0f)
        && inRange(c.min_flow_lpm,         0.0f,   50.0f)
        && inRange(c.max_flow_lpm,         0.1f,  100.0f)
        && c.flow_fault_delay_sec >= 5  && c.flow_fault_delay_sec <= 300
        && inRange(c.min_recovery_pct,     1.0f,   99.0f)
        && inRange(c.max_recovery_pct,     1.0f,   99.0f)
        && c.recovery_fault_delay_sec >= 5 && c.recovery_fault_delay_sec <= 300
        && c.pressure_membrane_enabled        <= 1
        && inRange(c.pressure_membrane_min_voltage,  0.0f,  15.0f)
        && inRange(c.pressure_membrane_max_voltage,  0.0f,  15.0f)
        && inRange(c.pressure_membrane_min_bar,      0.0f,  50.0f)
        && inRange(c.pressure_membrane_max_bar,      0.0f,  50.0f)
        && c.pressure_membrane_limits_enabled <= 1
        && inRange(c.pressure_membrane_high_limit,   0.0f,  50.0f)
        && c.pressure_fault_delay_sec >= 1  && c.pressure_fault_delay_sec <= 300
        && c.pressure_brine_enabled           <= 1
        && inRange(c.pressure_brine_min_voltage,     0.0f,  15.0f)
        && inRange(c.pressure_brine_max_voltage,     0.0f,  15.0f)
        && inRange(c.pressure_brine_min_bar,         0.0f,  50.0f)
        && inRange(c.pressure_brine_max_bar,         0.0f,  50.0f);
}

// Applies config if: (a) it passes validation AND (b) it is newer than current.
// updated_at == 0 is treated as "no timestamp" and accepted unconditionally
// if the current config also has ts == 0 (bootstrap / first push).
bool Sensors::setConfig(const SensorConfig& incoming) {
    if (!isValidConfig(incoming)) {
        Serial.println("[CFG] RECHAZADA — valores fuera de rango o inválidos");
        return false;
    }
    // Reject if incoming timestamp is present AND not newer
    if (incoming.updated_at > 0 && incoming.updated_at <= _cfg.updated_at) {
        Serial.printf("[CFG] IGNORADA — ts %lu <= actual %lu\n",
                      incoming.updated_at, _cfg.updated_at);
        return false;
    }
    _cfg = incoming;
    saveConfig();
    Serial.printf("[CFG] APLICADA: ff1=%.1f ff2=%.1f tds_t=%.1f "
                  "tds1_cal=%.4f/%.2f tds2_cal=%.4f/%.2f "
                  "min_fl=%.2f max_fl=%.1f flt_d=%u "
                  "min_rec=%.1f max_rec=%.1f rec_d=%u ts=%lu\n",
                  _cfg.flow_factor_1, _cfg.flow_factor_2, _cfg.tds_temperature,
                  _cfg.tds1_cal_slope, _cfg.tds1_cal_offset,
                  _cfg.tds2_cal_slope, _cfg.tds2_cal_offset,
                  _cfg.min_flow_lpm, _cfg.max_flow_lpm, _cfg.flow_fault_delay_sec,
                  _cfg.min_recovery_pct, _cfg.max_recovery_pct,
                  _cfg.recovery_fault_delay_sec, _cfg.updated_at);
    return true;
}

// Restores compile-time defaults and wipes NVS namespace "kx_cfg".
// Called on MQTT reset command. Device is always left operational.
void Sensors::resetConfig() {
    _cfg = SensorConfig{};
    Preferences p;
    p.begin("kx_cfg", false);
    p.clear();
    p.end();
    Serial.println("[CFG] RESET a defaults — NVS kx_cfg limpiado");
}

SensorConfig Sensors::getConfig() const {
    return _cfg;
}

// ── TDS conversion ────────────────────────────────────────────────────────────
// DFRobot SEN0244 polynomial (official formula, inline — no external library).
//
// Input:  filtered voltage from analogReadMilliVolts (0–3.1V range with ADC_11db)
//         temperature in °C for compensation (default 25°C)
// Output: TDS in ppm (clamped to [0, ∞))
//
// Limitations:
//   - ESP32 ADC non-linearity ~±5% even with analogReadMilliVolts calibration.
//   - Formula valid for 0–2.3V output from SEN0244 at 5V VCC.
//   - Temperature compensation assumes linear coefficient 0.02/°C.
//   - For high-precision calibration, a 2-point standard solution is required.
float Sensors::voltageToPpm(float voltage, float temperature) {
    if (voltage < 0.0f) voltage = 0.0f;
    float coeff = 1.0f + 0.02f * (temperature - 25.0f);
    float v     = voltage / coeff;
    float ppm   = (133.42f * v * v * v - 255.86f * v * v + 857.39f * v) * 0.5f;
    return ppm < 0.0f ? 0.0f : ppm;
}

// ── Calibration layer (voltage → ppm) ─────────────────────────────────────────
// Replaceable per-channel calibration, configurable via NVS/MQTT (no reflash).
//
//   slope == 0.0f → CAL_MODE_LEGACY: sin calibración cargada, usa voltageToPpm()
//                    (polinomio DFRobot). Estado de fábrica / equipos no calibrados.
//   slope >  0.0f → CAL_MODE_LINEAR: ppm = slope * mV + offset, con la misma
//                    compensación térmica que el polinomio (coeff = 1+0.02*(T-25)).
//
// Futuro: CAL_MODE_POLYNOMIAL / CAL_MODE_LOOKUP_TABLE se agregan como nuevas
// ramas aquí, sin tocar update()/begin() ni la capa NVS/MQTT/backend.
float Sensors::calibrateTdsPpm(float voltage, float temperature, float slope, float offset) {
    if (slope <= 0.0f) {
        return voltageToPpm(voltage, temperature);  // CAL_MODE_LEGACY
    }
    // CAL_MODE_LINEAR
    if (voltage < 0.0f) voltage = 0.0f;
    float coeff = 1.0f + 0.02f * (temperature - 25.0f);
    float mv    = (voltage / coeff) * 1000.0f;
    float ppm   = slope * mv + offset;
    return ppm < 0.0f ? 0.0f : ppm;
}

// ── Pressure calibration (voltage → bar) ──────────────────────────────────────
// Calibración lineal 2 puntos: y = y_min + ((v - v_min)/(v_max - v_min)) * (y_max - y_min).
// No clampea — fuera de rango es información válida (sub/sobre-presión).
float Sensors::calibrateLinear(float v, float v_min, float v_max, float y_min, float y_max) {
    if (v_max == v_min) return y_min;  // evita división por cero en config inválida
    return y_min + ((v - v_min) / (v_max - v_min)) * (y_max - y_min);
}

// 5-sample median sort (insertion sort on a local copy — O(n²) for n=5 = 10 ops max).
// Returns the 3rd-ranked (index 2) value of the sorted array.
float Sensors::median5(const float* buf) {
    float a[5];
    for (int i = 0; i < 5; i++) a[i] = buf[i];
    for (int i = 1; i < 5; i++) {
        float key = a[i];
        int   j   = i - 1;
        while (j >= 0 && a[j] > key) { a[j + 1] = a[j]; j--; }
        a[j + 1] = key;
    }
    return a[2];
}

// ── Init ──────────────────────────────────────────────────────────────────────
void Sensors::begin() {
    // Float switches wired to GND — INPUT_PULLUP: open=HIGH, closed=LOW.
    // T2A (D1): colgando=HIGH → demanda. T1B (D2): arriba=HIGH → crudo OK.
    // T1A (D5): colgando=HIGH → cisterna baja → bomba pozo ON.
    // D4 (presostato): kept separate — verify NC/NO type and wiring before changing.
    pinMode(PIN_D1, INPUT_PULLUP);
    pinMode(PIN_D2, INPUT_PULLUP);
    pinMode(PIN_D3, INPUT_PULLUP);
    pinMode(PIN_D4, INPUT_PULLDOWN);  // presostato — confirmar tipo antes de cambiar
    pinMode(PIN_D5, INPUT_PULLUP);
    pinMode(PIN_D6, INPUT_PULLUP);

    pinMode(PIN_Q1, INPUT_PULLDOWN);
    pinMode(PIN_Q2, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(PIN_Q1), isrQ1, RISING);
    attachInterrupt(digitalPinToInterrupt(PIN_Q2), isrQ2, RISING);

    // ADC_11db: 0–3.1V input range — default, usado por PIN_AIN0/AIN1 (presión, 0.5–4.5V).
    // ADC_0db:  0–~950mV — override solo en TDS1/TDS2 (mejor resolución posible,
    // ~0.23mV/cuenta). SEN0244 en agua de ósmosis mide ~14mV — por debajo del
    // piso de no-linealidad del ADC (~100mV) en cualquier atenuación; este
    // cambio es para confirmarlo empíricamente vía el burst de diagnóstico.
    // analogReadMilliVolts() applies ESP32 factory ADC calibration.
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
    analogSetPinAttenuation(PIN_TDS1, ADC_0db);
    analogSetPinAttenuation(PIN_TDS2, ADC_0db);

    loadTotals();
    loadConfig();

    // Warm TDS buffers with a real reading to avoid transient zeros at boot.
    tds1_adc_raw = analogRead(PIN_TDS1);
    tds2_adc_raw = analogRead(PIN_TDS2);
    tds1_mv_raw  = analogReadMilliVolts(PIN_TDS1);
    tds2_mv_raw  = analogReadMilliVolts(PIN_TDS2);
    float v1 = tds1_mv_raw / 1000.0f;
    float v2 = tds2_mv_raw / 1000.0f;
    for (int i = 0; i < TDS_BUF; i++) { tds1_buf[i] = v1; tds2_buf[i] = v2; }
    tds1_v   = v1;   tds2_v   = v2;
    tds1_ppm = calibrateTdsPpm(v1, _cfg.tds_temperature, _cfg.tds1_cal_slope, _cfg.tds1_cal_offset);
    tds2_ppm = calibrateTdsPpm(v2, _cfg.tds_temperature, _cfg.tds2_cal_slope, _cfg.tds2_cal_offset);

    // ── ADC burst diagnostic — 100 samples, min/max/avg. Remove after investigation.
    // Note: analogRead() and analogReadMilliVolts() perform SEPARATE conversions —
    // they do not share a sample. esp_adc_cal returns a non-zero mV for raw=0
    // (curve intercept) — voltages below the ADC's minimum measurable input
    // produce raw=0 regardless of actual voltage. TDS1/TDS2 now run at ADC_0db
    // (~950mV full scale, ~0.23mV/cuenta) — usar este burst para medir agua de
    // ósmosis (~14mV reales) y verificar si el piso bajó respecto a 11db/6db.
    {
        const int N = 100;
        long  s1r = 0, s1m = 0, s2r = 0, s2m = 0;
        int mn1r = 4095, mx1r = 0, mn1m = 99999, mx1m = 0;
        int mn2r = 4095, mx2r = 0, mn2m = 99999, mx2m = 0;

        for (int i = 0; i < N; i++) {
            int r1 = analogRead(PIN_TDS1);
            int m1 = analogReadMilliVolts(PIN_TDS1);
            int r2 = analogRead(PIN_TDS2);
            int m2 = analogReadMilliVolts(PIN_TDS2);

            s1r += r1;  if (r1 < mn1r) mn1r = r1;  if (r1 > mx1r) mx1r = r1;
            s1m += m1;  if (m1 < mn1m) mn1m = m1;  if (m1 > mx1m) mx1m = m1;
            s2r += r2;  if (r2 < mn2r) mn2r = r2;  if (r2 > mx2r) mx2r = r2;
            s2m += m2;  if (m2 < mn2m) mn2m = m2;  if (m2 > mx2m) mx2m = m2;

            delay(1);
        }

        Serial.println("[ADC_DIAG] ========================================");
        Serial.printf ("[ADC_DIAG] Atten: ADC_0db (TDS1/TDS2)  Res: 12-bit  N=%d samples\n", N);
        Serial.printf ("[ADC_DIAG] Scale: ~950mV/4095cnt (~0.23mV/cnt)\n");
        Serial.printf ("[ADC_DIAG] GPIO%d TDS1  raw: avg=%4ld min=%4d max=%4d  "
                       "mv: avg=%4ld min=%4d max=%4d\n",
                       PIN_TDS1, s1r/N, mn1r, mx1r, s1m/N, mn1m, mx1m);
        Serial.printf ("[ADC_DIAG] GPIO%d TDS2  raw: avg=%4ld min=%4d max=%4d  "
                       "mv: avg=%4ld min=%4d max=%4d\n",
                       PIN_TDS2, s2r/N, mn2r, mx2r, s2m/N, mn2m, mx2m);
        Serial.println("[ADC_DIAG] ========================================");
    }
}

// ── Update (called every loop iteration) ─────────────────────────────────────
void Sensors::update() {

    // ── Digital inputs (telemetría/diagnóstico — contrato MQTT, sin cambios) ───
    d1 = digitalRead(PIN_D1);
    d2 = digitalRead(PIN_D2);
    d3 = digitalRead(PIN_D3);
    d4 = digitalRead(PIN_D4);
    d5 = digitalRead(PIN_D5);
    d6 = digitalRead(PIN_D6);

    // ── Señales lógicas desacopladas (io_map + debounce) — consumidas por la
    // FSM y el motor de reglas vía getSignal() ─────────────────────────────────
    updateSignals();

    // ── Flow (~1 Hz window) ───────────────────────────────────────────────────
    // Pulses accumulated by ISR since lastFlowTime.
    // flow_lpm = (pulses * 60000) / (dt_ms * flow_factor_N)
    // where flow_factor = pulses/liter (sensor datasheet spec).
    //
    // dt_ms real (no se asume 1000ms fijo): si el loop se retrasa (p.ej.
    // reconexión WiFi/MQTT bloqueante), los pulsos acumulados corresponden a
    // una ventana >1s. Dividir por dt_ms real evita picos espurios de
    // flow1/flow2 (~Nx) al resumir tras un gap.
    if (millis() - lastFlowTime >= 1000) {
        unsigned long now_ms = millis();
        unsigned long dt_ms  = now_ms - lastFlowTime;

        noInterrupts();
        unsigned long p1 = pulsesQ1;
        unsigned long p2 = pulsesQ2;
        pulsesQ1 = 0;
        pulsesQ2 = 0;
        interrupts();

        last_pulses1 = p1;
        last_pulses2 = p2;

        // Volumen acumulado: litros reales de esta ventana, independiente de dt_ms.
        totalPerm += p1 / _cfg.flow_factor_1;
        totalRech += p2 / _cfg.flow_factor_2;

        // Caudal instantáneo normalizado al dt real.
        flow1 = (p1 * 60000.0f) / (dt_ms * _cfg.flow_factor_1);
        flow2 = (p2 * 60000.0f) / (dt_ms * _cfg.flow_factor_2);

        lastFlowTime = now_ms;
    }

    // ── Volume persistence (smart save) ──────────────────────────────────────
    bool byTime   = (millis() - lastSaveTime) > 3600000UL;  // 1 h
    bool byVolume = (totalPerm - lastSavedPerm) > 50.0f;    // 50 L
    if (byTime || byVolume) {
        saveTotals();
        lastSaveTime = millis();
    }

    // ── Pressure (voltage EWMA α=0.3, siempre; bar = calibrado o legacy) ──────
    const float alpha = 0.3f;
    p1_adc = analogRead(PIN_AIN0);
    p2_adc = analogRead(PIN_AIN1);

    float pm_v_raw = (p1_adc / 4095.0f) * PRESSURE_ADC_VREF;
    float pb_v_raw = (p2_adc / 4095.0f) * PRESSURE_ADC_VREF;
    if (pm_v_f == 0) pm_v_f = pm_v_raw;
    if (pb_v_f == 0) pb_v_f = pb_v_raw;
    pm_v_f = alpha * pm_v_raw + (1.0f - alpha) * pm_v_f;
    pb_v_f = alpha * pb_v_raw + (1.0f - alpha) * pb_v_f;

    if (_cfg.pressure_membrane_enabled) {
        p1 = calibrateLinear(pm_v_f, _cfg.pressure_membrane_min_voltage, _cfg.pressure_membrane_max_voltage,
                                      _cfg.pressure_membrane_min_bar,    _cfg.pressure_membrane_max_bar);
    } else {
        // Legacy: sin cambios respecto al comportamiento previo a calibración.
        float p1_raw = (p1_adc / 4095.0f) * 10.0f;
        if (p1_f == 0) p1_f = p1_raw;
        p1_f = alpha * p1_raw + (1.0f - alpha) * p1_f;
        p1 = p1_f;
    }

    if (_cfg.pressure_brine_enabled) {
        p2 = calibrateLinear(pb_v_f, _cfg.pressure_brine_min_voltage, _cfg.pressure_brine_max_voltage,
                                      _cfg.pressure_brine_min_bar,    _cfg.pressure_brine_max_bar);
    } else {
        // Legacy: sin cambios respecto al comportamiento previo a calibración.
        float p2_raw = (p2_adc / 4095.0f) * 10.0f;
        if (p2_f == 0) p2_f = p2_raw;
        p2_f = alpha * p2_raw + (1.0f - alpha) * p2_f;
        p2 = p2_f;
    }

    dp_bar = (_cfg.pressure_membrane_enabled && _cfg.pressure_brine_enabled)
             ? (p1 - p2) : NAN;

    // ── TDS (5-sample median + DFRobot polynomial) ────────────────────────────
    // analogReadMilliVolts() uses ESP32 factory ADC calibration (better than
    // raw/4095 * VREF which ignores ADC non-linearity).
    // Buffer stores voltages; ppm is computed once on the median voltage.
    tds1_adc_raw      = analogRead(PIN_TDS1);
    tds2_adc_raw      = analogRead(PIN_TDS2);
    tds1_mv_raw       = analogReadMilliVolts(PIN_TDS1);
    tds2_mv_raw       = analogReadMilliVolts(PIN_TDS2);
    tds1_buf[tds_idx] = tds1_mv_raw / 1000.0f;
    tds2_buf[tds_idx] = tds2_mv_raw / 1000.0f;
    tds_idx = (tds_idx + 1) % TDS_BUF;

    tds1_v   = median5(tds1_buf);
    tds2_v   = median5(tds2_buf);
    tds1_ppm = calibrateTdsPpm(tds1_v, _cfg.tds_temperature, _cfg.tds1_cal_slope, _cfg.tds1_cal_offset);
    tds2_ppm = calibrateTdsPpm(tds2_v, _cfg.tds_temperature, _cfg.tds2_cal_slope, _cfg.tds2_cal_offset);

    // Debug — rate-limited to 1 Hz. Remove after TDS investigation.
    static unsigned long lastTdsLog = 0;
    if (millis() - lastTdsLog >= 1000) {
        lastTdsLog = millis();
        Serial.printf("[TDS] ch1: raw=%4d mv=%4d v=%.4f ppm=%.1f | "
                                 "ch2: raw=%4d mv=%4d v=%.4f ppm=%.1f\n",
                      tds1_adc_raw, tds1_mv_raw, tds1_v, tds1_ppm,
                      tds2_adc_raw, tds2_mv_raw, tds2_v, tds2_ppm);
    }
}

// ── Getters ───────────────────────────────────────────────────────────────────
float Sensors::getFlow1()       { return flow1; }
float Sensors::getFlow2()       { return flow2; }
float Sensors::getPressure1()   { return p1; }
float Sensors::getPressure2()   { return p2; }
float Sensors::getPressureMembraneVoltage() { return pm_v_f; }
float Sensors::getPressureBrineVoltage()    { return pb_v_f; }
float Sensors::getDeltaPBar()               { return dp_bar; }
float Sensors::getTDS1Voltage() { return tds1_v; }
float Sensors::getTDS2Voltage() { return tds2_v; }
float Sensors::getTDS1Ppm()     { return tds1_ppm; }
float Sensors::getTDS2Ppm()     { return tds2_ppm; }
int   Sensors::getTDS1AdcRaw()  { return tds1_adc_raw; }
int   Sensors::getTDS2AdcRaw()  { return tds2_adc_raw; }
int   Sensors::getTDS1MvRaw()   { return tds1_mv_raw; }
int   Sensors::getTDS2MvRaw()   { return tds2_mv_raw; }
int   Sensors::getPressure1Adc()        { return p1_adc; }
int   Sensors::getPressure2Adc()        { return p2_adc; }
unsigned long Sensors::getLastPulses1() { return last_pulses1; }
unsigned long Sensors::getLastPulses2() { return last_pulses2; }
float Sensors::getTotalPerm()   { return totalPerm; }
float Sensors::getTotalRech()   { return totalRech; }

bool Sensors::getD1() { return d1; }
bool Sensors::getD2() { return d2; }
bool Sensors::getD3() { return d3; }
bool Sensors::getD4() { return d4; }
bool Sensors::getD5() { return d5; }
bool Sensors::getD6() { return d6; }

// ── Señales lógicas desacopladas (io_map) ────────────────────────────────────
// Resuelve GPIO/modo/invert/default_value para cada LogicalInput y aplica un
// debounce simétrico configurable (debounce_ms, por canal). Llamada 1x/loop
// desde update() — getSignal() solo lee el valor ya estabilizado (_sigStable).
//
// gpio==IOMAP_GPIO_NONE -> _sigStable = default_value directo, sin debounce
//                          (no hay "raw" físico que estabilizar; evita que un
//                          default_value=1 tarde debounce_ms en reflejarse al
//                          arrancar — "sin sensor = se asume OK" debe ser
//                          inmediato).
// debounce_ms==0        -> _sigStable sigue a raw sin retardo.
// debounce_ms>0         -> _sigStable solo cambia tras sostener el nuevo raw
//                          durante >= debounce_ms (simétrico: aplica igual a
//                          flancos 0->1 y 1->0).
void Sensors::updateSignals() {
    const IOMapConfig& m = ioMapGet();
    unsigned long now = millis();

    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        const IOPinConfig& e = m.inputs[i];

        if (e.gpio == IOMAP_GPIO_NONE) {
            _sigStable[i] = e.default_value;
            _sigRaw[i] = e.default_value;
            continue;
        }

        bool pin = digitalRead(e.gpio);
        bool raw = e.invert ? !pin : pin;

        if (e.debounce_ms == 0) {
            _sigStable[i] = raw;
            _sigRaw[i] = raw;
            continue;
        }

        if (raw != _sigRaw[i]) {
            _sigRaw[i] = raw;
            _sigEdgeStart[i] = now;
        }
        if (now - _sigEdgeStart[i] >= e.debounce_ms) {
            _sigStable[i] = raw;
        }
    }
}

bool Sensors::getSignal(LogicalInput sig) const {
    return _sigStable[(uint8_t)sig];
}
