#pragma once
#include <stdint.h>

// ============================================================
// CATÁLOGO DE SEÑALES LÓGICAS — capa de abstracción Pin <-> Señal
// ============================================================
//
// Enums append-only: el orden/valor numérico NO se persiste (el contrato
// NVS/MQTT usa los nombres de io_catalog.cpp), pero renombrar o reordenar
// requiere actualizar python_iot/io_catalog.py en el mismo cambio.
// Agregar nuevas señales SIEMPRE antes de *_COUNT, nunca insertar en medio.
//
// Esta capa SOLO define el catálogo (qué señales lógicas existen). El mapeo
// a pines físicos vive en io_map.h/.cpp. Ninguno de los dos archivos
// implementa lectura/escritura de GPIO ni lógica de FSM — Sensors/Control
// no se modifican en esta fase.

enum class LogicalInput : uint8_t {
    DEMAND = 0,             // demanda de agua (arranque de producción)
    RAW_WATER_AVAILABLE,    // agua cruda disponible
    FEED_TANK_HIGH,         // tanque de alimentación: nivel alto
    FEED_TANK_LOW,          // tanque de alimentación: nivel bajo
    PERMEATE_TANK_HIGH,     // tanque de permeado: nivel alto
    PERMEATE_TANK_LOW,      // tanque de permeado: nivel bajo
    FINAL_TANK_HIGH,        // tanque final/reserva: nivel alto
    FINAL_TANK_LOW,         // tanque final/reserva: nivel bajo
    PRESSURE_OK,            // presostato OK
    SOFTENER_REGENERATING,  // ablandador en regeneración (interlock)
    WELL_LOW_LEVEL,         // pozo: nivel bajo
    DOSING_OK,              // dosificación OK
    PERMEATE_TANK_DEMAND,   // demanda desde tanque de permeado (arranque RO)
    FINAL_TANK_DEMAND,      // demanda desde tanque final (bomba transferencia)
    PHASE_FAILURE,          // falla de fase — protección propia RO (fault_rules[])
    COUNT
};

enum class LogicalOutput : uint8_t {
    LOW_PRESSURE_PUMP = 0,  // bomba de baja presión
    HIGH_PRESSURE_PUMP,     // bomba de alta presión
    WELL_PUMP,              // bomba de pozo
    TRANSFER_PUMP,          // bomba de transferencia
    FLUSH_VALVE,            // válvula de flush
    INLET_VALVE,            // válvula de entrada
    DOSING_PUMP,            // bomba dosificadora
    COUNT
};

// Señales derivadas — no vienen de io_map/GPIO, se calculan cada loop desde
// el estado de un proceso (sección 2 de KAIROX_ARQUITECTURA_SENALES_REGLAS.md).
// Mismo criterio append-only que LogicalInput/LogicalOutput.
enum class DerivedSignal : uint8_t {
    RO_PRODUCING = 0,   // Control::isRunning()
    COUNT
};

// Nombres estables (claves JSON/NVS) — deben coincidir con
// python_iot/io_catalog.py (LOGICAL_INPUTS / LOGICAL_OUTPUTS / DERIVED_SIGNALS).
const char* logicalInputName(LogicalInput sig);
const char* logicalOutputName(LogicalOutput sig);
const char* derivedSignalName(DerivedSignal sig);

// Devuelven *_::COUNT si el nombre no está en el catálogo.
LogicalInput  logicalInputFromName(const char* name);
LogicalOutput logicalOutputFromName(const char* name);
DerivedSignal derivedSignalFromName(const char* name);
