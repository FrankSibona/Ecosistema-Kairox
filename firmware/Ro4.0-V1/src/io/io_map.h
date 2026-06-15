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
// IMPORTANTE — alcance de esta fase:
//   - Este módulo NO llama pinMode()/digitalRead()/digitalWrite().
//   - Sensors y Control siguen operando sobre los PIN_* fijos de config.h,
//     sin cambios de comportamiento ni de hardware.
//   - ioMapGet() queda disponible para que una fase futura reemplace los
//     PIN_* fijos por resolución dinámica Pin<->Señal, sin tocar el formato
//     NVS/MQTT definido aquí.

#define IOMAP_MODE_PULLUP   0   // INPUT_PULLUP
#define IOMAP_MODE_PULLDOWN 1   // INPUT_PULLDOWN

#define IOMAP_GPIO_NONE 0xFF    // señal lógica sin pin físico asignado

struct IOPinConfig {
    uint8_t gpio;    // número de GPIO, o IOMAP_GPIO_NONE si no asignado
    uint8_t mode;    // IOMAP_MODE_* — solo relevante para inputs
    uint8_t invert;  // 1 = lógica invertida respecto al nivel físico
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
// readSignal()/digitalWrite() funcionen.
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
