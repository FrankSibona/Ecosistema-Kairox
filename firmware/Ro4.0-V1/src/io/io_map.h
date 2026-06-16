#pragma once
#include <stdint.h>
#include "io_catalog.h"

// ============================================================
// MAPEO PIN <-> SEÑAL LÓGICA (io_map)
// ============================================================
//
// Capa de datos pura: persiste en NVS (namespace "kx_iomap") el mapeo entre
// pines físicos del ESP32 y señales lógicas del catálogo (io_catalog.h), y
// lo sincroniza vía MQTT retained (fyntek/{device_id}/iomap).
//
// IMPORTANTE — desde IOMAP_VERSION v3:
//   - Este módulo NO llama digitalRead()/digitalWrite() (solo
//     ioMapApplyPinModes() llama pinMode()).
//   - La FSM (control.cpp) y el motor de reglas ya NO leen PIN_D*/PIN_R*
//     fijos para señales lógicas — usan Sensors::getSignal(LogicalInput),
//     que resuelve GPIO/modo/invert/default_value/debounce_ms desde este
//     mapeo. defaultIOMap() reproduce el wiring D1-D6/R1-R6 actual, por lo
//     que un equipo sin io_map custom en NVS no cambia de comportamiento.
//   - d1..d6/getD1()-getD6() (sensors.h) SIGUEN leyendo PIN_D1-D6 directo —
//     son contrato de telemetría/diagnóstico (comms.cpp) y no pasan por
//     io_map.

#define IOMAP_MODE_PULLUP   0   // INPUT_PULLUP
#define IOMAP_MODE_PULLDOWN 1   // INPUT_PULLDOWN

#define IOMAP_GPIO_NONE 0xFF    // señal lógica sin pin físico asignado

struct IOPinConfig {
    uint8_t  gpio;          // número de GPIO, o IOMAP_GPIO_NONE si no asignado
    uint8_t  mode;          // IOMAP_MODE_* — solo relevante para inputs
    uint8_t  invert;        // 1 = lógica invertida respecto al nivel físico
    uint8_t  default_value; // solo inputs — valor lógico devuelto si gpio==IOMAP_GPIO_NONE
    uint16_t debounce_ms;   // solo inputs — debounce simétrico aplicado en Sensors::getSignal()
};

struct IOMapConfig {
    IOPinConfig inputs[(uint8_t)LogicalInput::COUNT];
    IOPinConfig outputs[(uint8_t)LogicalOutput::COUNT];
    uint32_t updated_at;  // epoch seconds — version field (mismo patrón que SensorConfig)
};

// Carga el mapeo desde NVS (o aplica el default si no hay datos válidos).
// Sin efectos sobre GPIO.
void ioMapInit();

const IOMapConfig& ioMapGet();

// Aplica pinMode() a cada señal con gpio asignado (!= IOMAP_GPIO_NONE) según
// su modo (inputs: IOMAP_MODE_PULLUP/PULLDOWN: outputs: OUTPUT). Llamar una
// vez en setup(), después de ioMapInit(). Para D1-D6/R1-R6 duplica el
// pinMode() ya hecho en Sensors::begin()/Control::begin() — idempotente, sin
// efecto. Para señales nuevas (sin PIN_* fijo) es lo único necesario para que
// Sensors::getSignal()/digitalWrite() funcionen.
void ioMapApplyPinModes();

// Aplica un mapeo entrante (partial update por señal: entradas ausentes o
// inválidas conservan el valor actual). Si incoming.updated_at > 0 y es
// <= al valor actual, el mensaje completo se ignora (mismo patrón que
// Sensors::setConfig). Persiste en NVS si se aplica.
//
// Reload en caliente: para cada señal que pasa de IOMAP_GPIO_NONE a un GPIO
// real, llama pinMode() inmediatamente (sin esperar reboot). Reasignar un
// pin YA configurado (cambiar GPIO/mode/invert de una señal que ya tenía
// pin) sigue requiriendo power-cycle.
//
// Retorna true si se aplicó (y persistió) el cambio.
bool ioMapSet(const IOMapConfig& incoming);
