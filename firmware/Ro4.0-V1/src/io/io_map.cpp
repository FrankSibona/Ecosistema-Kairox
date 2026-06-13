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

    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        cfg.inputs[i] = {IOMAP_GPIO_NONE, IOMAP_MODE_PULLUP, 0};
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        cfg.outputs[i] = {IOMAP_GPIO_NONE, IOMAP_MODE_PULLUP, 0};
    }

    cfg.inputs[(uint8_t)LogicalInput::DEMAND]              = {PIN_D1, IOMAP_MODE_PULLUP,   0};
    cfg.inputs[(uint8_t)LogicalInput::RAW_WATER_AVAILABLE] = {PIN_D2, IOMAP_MODE_PULLUP,   0};
    cfg.inputs[(uint8_t)LogicalInput::DOSING_OK]           = {PIN_D3, IOMAP_MODE_PULLUP,   0};
    cfg.inputs[(uint8_t)LogicalInput::PRESSURE_OK]         = {PIN_D4, IOMAP_MODE_PULLDOWN, 0};
    cfg.inputs[(uint8_t)LogicalInput::WELL_LOW_LEVEL]      = {PIN_D5, IOMAP_MODE_PULLUP,   0};
    // PIN_D6 (reserva) y el resto de entradas (tanques, ablandador) quedan
    // sin asignar por defecto — IOMAP_GPIO_NONE.

    cfg.outputs[(uint8_t)LogicalOutput::LOW_PRESSURE_PUMP]  = {PIN_R1, IOMAP_MODE_PULLUP, 0};
    cfg.outputs[(uint8_t)LogicalOutput::HIGH_PRESSURE_PUMP] = {PIN_R2, IOMAP_MODE_PULLUP, 0};
    cfg.outputs[(uint8_t)LogicalOutput::WELL_PUMP]          = {PIN_R3, IOMAP_MODE_PULLUP, 0};
    cfg.outputs[(uint8_t)LogicalOutput::FLUSH_VALVE]        = {PIN_R5, IOMAP_MODE_PULLUP, 0};
    cfg.outputs[(uint8_t)LogicalOutput::INLET_VALVE]        = {PIN_R6, IOMAP_MODE_PULLUP, 0};
    cfg.outputs[(uint8_t)LogicalOutput::DOSING_PUMP]        = {PIN_R4, IOMAP_MODE_PULLUP, 0};
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

static bool validInputEntry(const IOPinConfig& e) {
    if (e.gpio != IOMAP_GPIO_NONE && e.gpio > 39) return false;
    if (e.mode != IOMAP_MODE_PULLUP && e.mode != IOMAP_MODE_PULLDOWN) return false;
    if (e.invert > 1) return false;
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
    if (incoming.updated_at > 0) _cfg.updated_at = incoming.updated_at;

    ioMapSave();
    Serial.printf("[IOMAP] Guardado en NVS — updated_at=%u\n", (unsigned)_cfg.updated_at);
    return true;
}
