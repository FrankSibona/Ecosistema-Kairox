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
    loaded.flow_factor_1   = p.getFloat("ff1",   FLOW_FACTOR_DEFAULT);
    loaded.flow_factor_2   = p.getFloat("ff2",   FLOW_FACTOR_DEFAULT);
    loaded.tds_temperature = p.getFloat("tds_t", TDS_TEMPERATURE_DEFAULT);
    loaded.updated_at      = p.getULong("ts",    0);
    p.end();

    if (!isValidConfig(loaded)) {
        Serial.println("[CFG] NVS: valores fuera de rango — usando defaults");
        _cfg = SensorConfig{};
        return;
    }

    _cfg = loaded;
    Serial.printf("[CFG] Cargado: ff1=%.1f ff2=%.1f tds_t=%.1f ts=%lu\n",
                  _cfg.flow_factor_1, _cfg.flow_factor_2,
                  _cfg.tds_temperature, _cfg.updated_at);
}

void Sensors::saveConfig() {
    Preferences p;
    p.begin("kx_cfg", false);
    p.putUInt("magic",   _cfg.magic);
    p.putUInt("version", _cfg.version);
    p.putFloat("ff1",    _cfg.flow_factor_1);
    p.putFloat("ff2",    _cfg.flow_factor_2);
    p.putFloat("tds_t",  _cfg.tds_temperature);
    p.putULong("ts",     _cfg.updated_at);
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
    return inRange(c.flow_factor_1,   10.0f, 5000.0f)
        && inRange(c.flow_factor_2,   10.0f, 5000.0f)
        && inRange(c.tds_temperature,  0.0f,   80.0f);
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
    Serial.printf("[CFG] APLICADA: ff1=%.1f ff2=%.1f tds_t=%.1f ts=%lu\n",
                  _cfg.flow_factor_1, _cfg.flow_factor_2,
                  _cfg.tds_temperature, _cfg.updated_at);
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
    pinMode(PIN_D1, INPUT_PULLDOWN);
    pinMode(PIN_D2, INPUT_PULLDOWN);
    pinMode(PIN_D3, INPUT_PULLDOWN);
    pinMode(PIN_D4, INPUT_PULLDOWN);
    pinMode(PIN_D5, INPUT_PULLDOWN);
    pinMode(PIN_D6, INPUT_PULLDOWN);

    pinMode(PIN_Q1, INPUT_PULLDOWN);
    pinMode(PIN_Q2, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(PIN_Q1), isrQ1, RISING);
    attachInterrupt(digitalPinToInterrupt(PIN_Q2), isrQ2, RISING);

    // ADC_11db: 0–3.1V input range — covers SEN0244 output (0–2.3V) fully.
    // analogReadMilliVolts() applies ESP32 factory ADC calibration.
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    loadTotals();
    loadConfig();

    // Warm TDS buffers with a real reading to avoid transient zeros at boot.
    float v1 = analogReadMilliVolts(PIN_TDS1) / 1000.0f;
    float v2 = analogReadMilliVolts(PIN_TDS2) / 1000.0f;
    for (int i = 0; i < TDS_BUF; i++) { tds1_buf[i] = v1; tds2_buf[i] = v2; }
    tds1_v   = v1;   tds2_v   = v2;
    tds1_ppm = voltageToPpm(v1, _cfg.tds_temperature);
    tds2_ppm = voltageToPpm(v2, _cfg.tds_temperature);
}

// ── Update (called every loop iteration) ─────────────────────────────────────
void Sensors::update() {

    // ── Digital inputs ────────────────────────────────────────────────────────
    d1 = digitalRead(PIN_D1);
    d2 = digitalRead(PIN_D2);
    d3 = digitalRead(PIN_D3);
    d4 = digitalRead(PIN_D4);
    d5 = digitalRead(PIN_D5);
    d6 = digitalRead(PIN_D6);

    // ── Flow (1 Hz window) ────────────────────────────────────────────────────
    // Pulses accumulated by ISR in 1 second.
    // flow_lpm = (pulses_per_sec * 60) / flow_factor_N
    // where flow_factor = pulses/liter (sensor datasheet spec).
    if (millis() - lastFlowTime >= 1000) {
        noInterrupts();
        unsigned long p1 = pulsesQ1;
        unsigned long p2 = pulsesQ2;
        pulsesQ1 = 0;
        pulsesQ2 = 0;
        interrupts();

        flow1 = (p1 * 60.0f) / _cfg.flow_factor_1;
        flow2 = (p2 * 60.0f) / _cfg.flow_factor_2;

        totalPerm += flow1 / 60.0f;
        totalRech += flow2 / 60.0f;

        lastFlowTime = millis();
    }

    // ── Volume persistence (smart save) ──────────────────────────────────────
    bool byTime   = (millis() - lastSaveTime) > 3600000UL;  // 1 h
    bool byVolume = (totalPerm - lastSavedPerm) > 50.0f;    // 50 L
    if (byTime || byVolume) {
        saveTotals();
        lastSaveTime = millis();
    }

    // ── Pressure (EWMA α=0.3) ─────────────────────────────────────────────────
    const float alpha = 0.3f;
    float p1_raw = (analogRead(PIN_AIN0) / 4095.0f) * 10.0f;
    float p2_raw = (analogRead(PIN_AIN1) / 4095.0f) * 10.0f;
    if (p1_f == 0) p1_f = p1_raw;
    if (p2_f == 0) p2_f = p2_raw;
    p1_f = alpha * p1_raw + (1.0f - alpha) * p1_f;
    p2_f = alpha * p2_raw + (1.0f - alpha) * p2_f;
    p1 = p1_f;
    p2 = p2_f;

    // ── TDS (5-sample median + DFRobot polynomial) ────────────────────────────
    // analogReadMilliVolts() uses ESP32 factory ADC calibration (better than
    // raw/4095 * VREF which ignores ADC non-linearity).
    // Buffer stores voltages; ppm is computed once on the median voltage.
    tds1_buf[tds_idx] = analogReadMilliVolts(PIN_TDS1) / 1000.0f;
    tds2_buf[tds_idx] = analogReadMilliVolts(PIN_TDS2) / 1000.0f;
    tds_idx = (tds_idx + 1) % TDS_BUF;

    tds1_v   = median5(tds1_buf);
    tds2_v   = median5(tds2_buf);
    tds1_ppm = voltageToPpm(tds1_v, _cfg.tds_temperature);
    tds2_ppm = voltageToPpm(tds2_v, _cfg.tds_temperature);
}

// ── Getters ───────────────────────────────────────────────────────────────────
float Sensors::getFlow1()       { return flow1; }
float Sensors::getFlow2()       { return flow2; }
float Sensors::getPressure1()   { return p1; }
float Sensors::getPressure2()   { return p2; }
float Sensors::getTDS1Voltage() { return tds1_v; }
float Sensors::getTDS2Voltage() { return tds2_v; }
float Sensors::getTDS1Ppm()     { return tds1_ppm; }
float Sensors::getTDS2Ppm()     { return tds2_ppm; }
float Sensors::getTotalPerm()   { return totalPerm; }
float Sensors::getTotalRech()   { return totalRech; }

bool Sensors::getD1() { return d1; }
bool Sensors::getD2() { return d2; }
bool Sensors::getD3() { return d3; }
bool Sensors::getD4() { return d4; }
bool Sensors::getD5() { return d5; }
bool Sensors::getD6() { return d6; }

bool Sensors::getDemand()    { return d1; }
bool Sensors::getCrudoOK()   { return d2; }
bool Sensors::getDoseOK()    { return d3; }
bool Sensors::getPresostato(){ return d4; }

bool Sensors::demanda()          { return d1; }
bool Sensors::crudoDisponible()  { return d2; }
bool Sensors::presionOK()        { return d4; }
