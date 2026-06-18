#include "io_map.h"
#include <Arduino.h>
#include <Preferences.h>
#include <config.h>

static IOMapConfig _cfg;

// Mapeo por defecto — reproduce EXACTAMENTE el wiring hardcodeado actual
// (ver include/config.h: PIN_D1..D6 / PIN_R1..R6). Mantener sincronizado
// con DEFAULT_IO_MAP en python_iot/io_catalog.py.
static IOMapConfig defaultIOMap() {
    IOMapConfig cfg = {};

    // default_value=0/debounce_ms=0 para todas las señales salvo las 3
    // sobreescritas abajo (demand/raw_water_available/pressure_ok) — ver tabla
    // de defaults en config.h (IOMAP_VERSION v3).
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        cfg.inputs[i] = {IOMAP_GPIO_NONE, IOMAP_MODE_PULLUP, 0, 0, 0};
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        cfg.outputs[i] = {IOMAP_GPIO_NONE, IOMAP_MODE_PULLUP, 0, 0, 0};
    }

    // debounce_ms=2000 reproduce el debounce hoy hardcodeado en control.cpp
    // (demandaStart/crudoStart/presionStart). default_value aplica solo si
    // gpio==IOMAP_GPIO_NONE (equipo sin ese sensor cableado):
    //   - demand: sin sensor de demanda -> false (no hay demanda)
    //   - raw_water_available: sin sensor de nivel de entrada -> true (se asume agua disponible)
    //   - pressure_ok: sin presostato -> true (se asume presión OK)
    cfg.inputs[(uint8_t)LogicalInput::DEMAND]              = {PIN_D1, IOMAP_MODE_PULLUP,   0, 0, 2000};
    cfg.inputs[(uint8_t)LogicalInput::RAW_WATER_AVAILABLE] = {PIN_D2, IOMAP_MODE_PULLUP,   0, 1, 2000};
    cfg.inputs[(uint8_t)LogicalInput::DOSING_OK]           = {PIN_D3, IOMAP_MODE_PULLUP,   0, 0, 0};
    cfg.inputs[(uint8_t)LogicalInput::PRESSURE_OK]         = {PIN_D4, IOMAP_MODE_PULLDOWN, 0, 1, 2000};
    cfg.inputs[(uint8_t)LogicalInput::WELL_LOW_LEVEL]      = {PIN_D5, IOMAP_MODE_PULLUP,   0, 0, 0};
    // PIN_D6 (reserva) y el resto de entradas (tanques, ablandador) quedan
    // sin asignar por defecto — IOMAP_GPIO_NONE.

    cfg.outputs[(uint8_t)LogicalOutput::LOW_PRESSURE_PUMP]  = {PIN_R1, IOMAP_MODE_PULLUP, 0, 0, 0};
    cfg.outputs[(uint8_t)LogicalOutput::HIGH_PRESSURE_PUMP] = {PIN_R2, IOMAP_MODE_PULLUP, 0, 0, 0};
    cfg.outputs[(uint8_t)LogicalOutput::WELL_PUMP]          = {PIN_R3, IOMAP_MODE_PULLUP, 0, 0, 0};
    cfg.outputs[(uint8_t)LogicalOutput::FLUSH_VALVE]        = {PIN_R5, IOMAP_MODE_PULLUP, 0, 0, 0};
    cfg.outputs[(uint8_t)LogicalOutput::INLET_VALVE]        = {PIN_R6, IOMAP_MODE_PULLUP, 0, 0, 0};
    cfg.outputs[(uint8_t)LogicalOutput::DOSING_PUMP]        = {PIN_R4, IOMAP_MODE_PULLUP, 0, 0, 0};
    // TRANSFER_PUMP queda sin asignar por defecto — IOMAP_GPIO_NONE.

    cfg.updated_at = 0;
    return cfg;
}

static void ioMapSave() {
    Preferences p;
    p.begin("kx_iomap", false);
    p.putUInt("magic", IOMAP_MAGIC);
    p.putUInt("version", IOMAP_VERSION);
    p.putBytes("data", &_cfg, sizeof(_cfg));
    p.end();
}

void ioMapInit() {
    _cfg = defaultIOMap();

    Preferences p;
    p.begin("kx_iomap", true);
    uint32_t magic   = p.getUInt("magic", 0);
    uint32_t version = p.getUInt("version", 0);
    if (magic == IOMAP_MAGIC && version == IOMAP_VERSION) {
        IOMapConfig stored;
        if (p.getBytes("data", &stored, sizeof(stored)) == sizeof(stored)) {
            _cfg = stored;
        }
    } else {
        Serial.println("[IOMAP] NVS vacío/incompatible — usando mapeo por defecto");
    }
    p.end();

    Serial.printf("[IOMAP] Init OK — updated_at=%u\n", (unsigned)_cfg.updated_at);
}

const IOMapConfig& ioMapGet() {
    return _cfg;
}

void ioMapApplyPinModes() {
    const IOMapConfig& m = ioMapGet();
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (m.inputs[i].gpio == IOMAP_GPIO_NONE) continue;
        pinMode(m.inputs[i].gpio, m.inputs[i].mode == IOMAP_MODE_PULLDOWN ? INPUT_PULLDOWN : INPUT_PULLUP);
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (m.outputs[i].gpio == IOMAP_GPIO_NONE) continue;
        pinMode(m.outputs[i].gpio, OUTPUT);
    }
}

static bool validInputEntry(const IOPinConfig& e) {
    if (e.gpio != IOMAP_GPIO_NONE && e.gpio > 39) return false;
    if (e.mode != IOMAP_MODE_PULLUP && e.mode != IOMAP_MODE_PULLDOWN) return false;
    if (e.invert > 1) return false;
    if (e.default_value > 1) return false;
    if (e.debounce_ms > 60000) return false;
    return true;
}

static bool validOutputEntry(const IOPinConfig& e) {
    if (e.gpio != IOMAP_GPIO_NONE && e.gpio > 39) return false;
    if (e.invert > 1) return false;
    return true;
}

bool ioMapSet(const IOMapConfig& incoming) {
    if (incoming.updated_at > 0 && incoming.updated_at <= _cfg.updated_at) {
        Serial.printf("[IOMAP] IGNORADO — ts %u <= actual %u\n",
                       (unsigned)incoming.updated_at, (unsigned)_cfg.updated_at);
        return false;
    }

    IOMapConfig before = _cfg;

    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (validInputEntry(incoming.inputs[i])) {
            _cfg.inputs[i] = incoming.inputs[i];
        } else {
            Serial.printf("[IOMAP] input[%u] inválido — se conserva valor actual\n", i);
        }
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (validOutputEntry(incoming.outputs[i])) {
            _cfg.outputs[i] = incoming.outputs[i];
        } else {
            Serial.printf("[IOMAP] output[%u] inválido — se conserva valor actual\n", i);
        }
    }
    // Detección de GPIO duplicados: si un GPIO aparece en más de un slot,
    // revertir el segundo al valor anterior.
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (_cfg.inputs[i].gpio == IOMAP_GPIO_NONE) continue;
        for (uint8_t j = i + 1; j < (uint8_t)LogicalInput::COUNT; j++) {
            if (_cfg.inputs[j].gpio == _cfg.inputs[i].gpio) {
                Serial.printf("[IOMAP] GPIO %u duplicado en input[%u] e input[%u] — input[%u] revertido\n",
                              _cfg.inputs[j].gpio, i, j, j);
                _cfg.inputs[j] = before.inputs[j];
            }
        }
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (_cfg.outputs[i].gpio == IOMAP_GPIO_NONE) continue;
        for (uint8_t j = i + 1; j < (uint8_t)LogicalOutput::COUNT; j++) {
            if (_cfg.outputs[j].gpio == _cfg.outputs[i].gpio) {
                Serial.printf("[IOMAP] GPIO %u duplicado en output[%u] y output[%u] — output[%u] revertido\n",
                              _cfg.outputs[j].gpio, i, j, j);
                _cfg.outputs[j] = before.outputs[j];
            }
        }
    }

    if (incoming.updated_at > 0) _cfg.updated_at = incoming.updated_at;

    ioMapSave();
    Serial.printf("[IOMAP] Guardado en NVS — updated_at=%u\n", (unsigned)_cfg.updated_at);

    // Reload en caliente: aplica pinMode() solo a señales que pasan de "sin
    // pin" (IOMAP_GPIO_NONE) a un GPIO real. Evita reconfigurar pines ya
    // activos (D1-D6/R1-R6 en producción) — reasignar un pin YA usado sigue
    // requiriendo reboot.
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (before.inputs[i].gpio == IOMAP_GPIO_NONE && _cfg.inputs[i].gpio != IOMAP_GPIO_NONE) {
            pinMode(_cfg.inputs[i].gpio, _cfg.inputs[i].mode == IOMAP_MODE_PULLDOWN ? INPUT_PULLDOWN : INPUT_PULLUP);
            Serial.printf("[IOMAP] pinMode aplicado en caliente: input[%u] -> GPIO%u\n", i, _cfg.inputs[i].gpio);
        }
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (before.outputs[i].gpio == IOMAP_GPIO_NONE && _cfg.outputs[i].gpio != IOMAP_GPIO_NONE) {
            pinMode(_cfg.outputs[i].gpio, OUTPUT);
            Serial.printf("[IOMAP] pinMode aplicado en caliente: output[%u] -> GPIO%u\n", i, _cfg.outputs[i].gpio);
        }
    }

    return true;
}
