#include "antifreeze.h"
#include <Preferences.h>
#include <Arduino.h>
#include <math.h>
#include <DHTesp.h>
#include <config.h>

static AntifreezeConfig _cfg;

static DHTesp _dht;
static int16_t _attachedGpio = -1;  // -1 = sin adjuntar todavía

static float   _lastTempC     = NAN;
static float   _lastHumidity  = NAN;
static uint8_t _consecutiveFailures = 0;
static bool    _sensorFault   = false;
static unsigned long _lastSensorPollAt = 0;

static bool          _riskActive    = false;  // histéresis: latch de "temperatura en zona de riesgo"
static bool           _cycleActive   = false;  // ciclo de circulación en curso
static unsigned long _cycleStartedAt = 0;
static unsigned long _nextEvalAt     = 0;      // 0 = evaluar en la próxima llamada

// ================= DEFAULTS / VALIDACIÓN =================

static AntifreezeConfig defaultAntifreezeConfig() {
    AntifreezeConfig c;
    c.magic                      = AFREEZE_MAGIC;
    c.version                    = AFREEZE_VERSION;
    c.enabled                    = AFREEZE_ENABLED_DEFAULT;
    c.sensor_enabled             = AFREEZE_SENSOR_ENABLED_DEFAULT;
    c.sensor_gpio                = PIN_AFREEZE_DHT_DEFAULT;
    c.temp_threshold_low_c       = AFREEZE_TEMP_THRESHOLD_LOW_C_DEFAULT;
    c.temp_threshold_high_c      = AFREEZE_TEMP_THRESHOLD_HIGH_C_DEFAULT;
    c.flush_duration_sec         = AFREEZE_FLUSH_DURATION_SEC_DEFAULT;
    c.eval_interval_sec          = AFREEZE_EVAL_INTERVAL_SEC_DEFAULT;
    c.boot_inhibit_sec           = AFREEZE_BOOT_INHIBIT_SEC_DEFAULT;
    c.min_valid_temp_c           = AFREEZE_MIN_VALID_TEMP_C_DEFAULT;
    c.max_valid_temp_c           = AFREEZE_MAX_VALID_TEMP_C_DEFAULT;
    c.max_consecutive_failures   = AFREEZE_MAX_CONSECUTIVE_FAILURES_DEFAULT;
    c.updated_at                 = 0;
    return c;
}

static bool antifreezeConfigValid(const AntifreezeConfig& c) {
    if (c.enabled > 1 || c.sensor_enabled > 1) return false;
    if (c.sensor_gpio > 39) return false;
    if (c.temp_threshold_low_c  < -40.0f || c.temp_threshold_low_c  > 60.0f) return false;
    if (c.temp_threshold_high_c < -40.0f || c.temp_threshold_high_c > 60.0f) return false;
    if (c.temp_threshold_high_c <= c.temp_threshold_low_c) return false;
    if (c.flush_duration_sec < 10 || c.flush_duration_sec > 3600) return false;
    if (c.eval_interval_sec < 60 || c.eval_interval_sec > 86400) return false;
    if (c.eval_interval_sec <= c.flush_duration_sec) return false;
    if (c.boot_inhibit_sec > 3600) return false;
    if (c.min_valid_temp_c < -40.0f || c.min_valid_temp_c > 80.0f) return false;
    if (c.max_valid_temp_c < -40.0f || c.max_valid_temp_c > 80.0f) return false;
    if (c.max_valid_temp_c <= c.min_valid_temp_c) return false;
    if (c.max_consecutive_failures < 1 || c.max_consecutive_failures > 20) return false;
    return true;
}

// ================= PERSISTENCIA NVS =================

void antifreezeConfigInit() {
    Preferences p;
    p.begin("kx_afreeze", true);
    uint32_t magic   = p.getUInt("magic",   0);
    uint32_t version = p.getUInt("version", 0);
    if (magic == AFREEZE_MAGIC && version == AFREEZE_VERSION) {
        size_t n = p.getBytes("data", &_cfg, sizeof(_cfg));
        if (n == sizeof(_cfg) && antifreezeConfigValid(_cfg)) {
            p.end();
            Serial.printf("[AFREEZE] Init OK — enabled=%u sensor_enabled=%u updated_at=%u\n",
                          _cfg.enabled, _cfg.sensor_enabled, (unsigned)_cfg.updated_at);
            return;
        }
        if (n == sizeof(_cfg)) {
            Serial.println("[AFREEZE] NVS con valores fuera de rango — usando defaults");
        }
    }
    p.end();
    Serial.println("[AFREEZE] NVS vacío/incompatible — usando defaults (enabled=0)");
    _cfg = defaultAntifreezeConfig();
}

const AntifreezeConfig& antifreezeConfigGet() {
    return _cfg;
}

bool antifreezeConfigSet(const AntifreezeConfig& incoming) {
    if (incoming.updated_at > 0 && incoming.updated_at <= _cfg.updated_at) {
        Serial.printf("[AFREEZE] IGNORED — updated_at=%u <= actual=%u\n",
                      (unsigned)incoming.updated_at, (unsigned)_cfg.updated_at);
        return false;
    }
    if (!antifreezeConfigValid(incoming)) {
        Serial.println("[AFREEZE] REJECTED — valores fuera de rango");
        return false;
    }
    _cfg = incoming;
    _cfg.magic   = AFREEZE_MAGIC;
    _cfg.version = AFREEZE_VERSION;

    Preferences p;
    p.begin("kx_afreeze", false);
    p.putUInt("magic",   AFREEZE_MAGIC);
    p.putUInt("version", AFREEZE_VERSION);
    p.putBytes("data",   &_cfg, sizeof(_cfg));
    p.end();
    Serial.printf("[AFREEZE] Guardado en NVS — enabled=%u sensor_enabled=%u gpio=%u updated_at=%u\n",
                  _cfg.enabled, _cfg.sensor_enabled, _cfg.sensor_gpio, (unsigned)_cfg.updated_at);

    // Si cambió enabled/sensor_enabled a apagado, limpiar estado runtime para
    // que la telemetría no arrastre un "fault"/"active" obsoleto.
    if (!_cfg.sensor_enabled) {
        _sensorFault = false;
        _lastTempC = NAN;
        _lastHumidity = NAN;
    }
    if (!_cfg.enabled) {
        antifreezeAbort();
    }
    return true;
}

// ================= LECTURA DEL SENSOR =================

static void pollSensorIfDue(unsigned long now) {
    if (!_cfg.sensor_enabled) {
        _sensorFault = false;
        return;
    }
    if (_lastSensorPollAt != 0 && (now - _lastSensorPollAt < AFREEZE_SENSOR_POLL_INTERVAL_MS)) {
        return;
    }
    _lastSensorPollAt = now;

    if (_attachedGpio != (int16_t)_cfg.sensor_gpio) {
        _dht.setup(_cfg.sensor_gpio, DHTesp::DHT22);
        _attachedGpio = (int16_t)_cfg.sensor_gpio;
        Serial.printf("[AFREEZE] DHT22 adjuntado en GPIO%u\n", _cfg.sensor_gpio);
    }

    TempAndHumidity reading = _dht.getTempAndHumidity();
    DHTesp::DHT_ERROR_t err = _dht.getStatus();

    // Cubre los tres modos de falla pedidos: timeout/CRC (reportados por la
    // librería vía err), NaN, y fuera de rango físico válido (sensor
    // desconectado o en cortocircuito suele saturar en un extremo).
    bool valid = (err == DHTesp::ERROR_NONE)
              && !isnan(reading.temperature)
              && !isnan(reading.humidity)
              && reading.temperature >= _cfg.min_valid_temp_c
              && reading.temperature <= _cfg.max_valid_temp_c;

    if (valid) {
        _lastTempC          = reading.temperature;
        _lastHumidity        = reading.humidity;
        _consecutiveFailures = 0;
        _sensorFault         = false;
    } else {
        if (_consecutiveFailures < 255) _consecutiveFailures++;
        if (_consecutiveFailures >= _cfg.max_consecutive_failures) {
            _sensorFault = true;
        }
        Serial.printf("[AFREEZE] Lectura inválida (err=%d, temp=%.1f) — fallos consecutivos=%u/%u\n",
                      (int)err, reading.temperature, _consecutiveFailures, _cfg.max_consecutive_failures);
    }
}

// ================= EVALUACIÓN / DECISIÓN =================

bool antifreezeEvaluate(unsigned long now, bool waterAvailable, bool processPermitted) {
    if (!_cfg.enabled || !_cfg.sensor_enabled) {
        _riskActive  = false;
        _cycleActive = false;
        return false;
    }

    // Inhibición post-boot — debounce ante reinicios repetidos (microcortes/
    // brownouts): sin esto, un reboot pierde toda la cadencia/estado runtime
    // (no persistido en NVS a propósito, ver antifreeze.h) y podría disparar
    // un ciclo nuevo en cada arranque. now==millis(), por lo tanto representa
    // directamente el tiempo transcurrido desde el último reset del ESP32 —
    // no requiere estado persistido ni NTP. Sin efectos colaterales: durante
    // la ventana, la función no toca el sensor ni el estado runtime.
    if (now < (unsigned long)_cfg.boot_inhibit_sec * 1000UL) {
        return false;
    }

    pollSensorIfDue(now);

    // Ciclo ya en curso: sostenerlo hasta cumplir su duración completa — no
    // se reevalúa la temperatura a mitad de camino (diseño pedido). Sí se
    // verifican las condiciones mínimas de agua/permiso en cada tick, igual
    // que lo haría la FSM real durante un arranque — si se pierden, el
    // ciclo se aborta de inmediato.
    if (_cycleActive) {
        if (!waterAvailable || !processPermitted || _sensorFault) {
            Serial.println("[AFREEZE] Ciclo abortado — condición mínima perdida");
            _cycleActive = false;
            return false;
        }
        if (now - _cycleStartedAt >= _cfg.flush_duration_sec * 1000UL) {
            Serial.println("[AFREEZE] Ciclo completado");
            _cycleActive = false;
        } else {
            return true;
        }
    }

    // Sin ciclo en curso — ¿corresponde evaluar en este tick? Cadencia fija:
    // el reloj marca el intervalo ENTRE evaluaciones, no entre fin de ciclo
    // y la próxima.
    if (_nextEvalAt != 0 && now < _nextEvalAt) return false;
    _nextEvalAt = now + _cfg.eval_interval_sec * 1000UL;

    if (_sensorFault) return false;                         // sin lectura confiable -> no actuar
    if (!waterAvailable || !processPermitted) return false;  // mismas condiciones que un arranque real

    // Histéresis de dos umbrales: entra en riesgo por debajo de low_c,
    // permanece en riesgo hasta superar high_c (evita disparos repetidos
    // por oscilación cerca de un único umbral).
    if (!_riskActive) {
        if (_lastTempC < _cfg.temp_threshold_low_c) _riskActive = true;
    } else {
        if (_lastTempC > _cfg.temp_threshold_high_c) _riskActive = false;
    }

    if (_riskActive) {
        _cycleActive    = true;
        _cycleStartedAt = now;
        Serial.printf("[AFREEZE] Riesgo de congelamiento (%.1f°C, umbral %.1f°C) -> ciclo de %lus\n",
                      _lastTempC, _cfg.temp_threshold_low_c, (unsigned long)_cfg.flush_duration_sec);
        return true;
    }
    return false;
}

void antifreezeAbort() {
    if (_cycleActive) {
        Serial.println("[AFREEZE] Ciclo cancelado — la FSM tomó control de la planta");
    }
    _cycleActive = false;
}

// ================= TELEMETRÍA =================

float antifreezeGetTempC()       { return _lastTempC; }
float antifreezeGetHumidityPct() { return _lastHumidity; }
bool  antifreezeIsSensorFault()  { return _sensorFault; }
bool  antifreezeIsActive()       { return _cycleActive; }
