#pragma once

#include <Arduino.h>
#include "sensors/sensors.h"
#include "../commands/commands.h"

// ===================== ESTADOS =====================
enum SystemState {
    IDLE,
    STARTING,
    PRODUCING,
    FLUSHING,
    STOPPING,
    FAULT
};

// ===================== FAULT REASON =====================
// Identifies the cause of a FAULT transition.
// MAX_RETRIES: startup sequence failed after MAX_RETRIES attempts.
// FLOW_LOW:      permeate flow below min_flow_lpm for flow_fault_delay_sec.
// RECOVERY_LOW:  water recovery below min_recovery_pct for recovery_fault_delay_sec.
// RECOVERY_HIGH: water recovery above max_recovery_pct for recovery_fault_delay_sec.
// PRESSURE_MEMBRANE_HIGH: pressure_membrane_bar above pressure_membrane_high_limit
//                         for pressure_fault_delay_sec (requiere pressure_membrane_enabled
//                         y pressure_membrane_limits_enabled).
enum class FaultReason : uint8_t {
    NONE                   = 0,
    MAX_RETRIES            = 1,
    FLOW_LOW               = 2,
    RECOVERY_LOW           = 3,
    RECOVERY_HIGH          = 4,
    PRESSURE_MEMBRANE_HIGH = 5,
};

// ===================== OUTPUTS =====================
struct OutputsState {
    bool pumpLow;
    bool pumpHigh;
    bool pumpInlet;
    bool pumpDose;
    bool valveFlush;
    bool valveInlet;
};

// ===================== CONTROL =====================
class Control {
public:
    void begin();
    void update(Sensors &s, Commands &cmds);

    bool isRunning();
    SystemState getState();
    const char* getStateName();
    int getRetryCount();
    FaultReason getFaultReason();
    const char* getFaultReasonName();

    OutputsState getOutputs();

    // ── Diagnostic / Flight Recorder getters ────────────────────────────────
    // consumeFaultEvent(): returns true exactly once after a FAULT transition,
    // then resets to false. Used by comms to trigger flight recorder publish.
    bool          consumeFaultEvent();
    bool          isFlowFaultArmed()         const;
    bool          isRecoveryFaultArmed()     const;
    unsigned long getFlowFaultElapsedMs()    const; // 0 when not armed
    unsigned long getRecoveryFaultElapsedMs() const;

private:
    SystemState state     = IDLE;
    SystemState lastState = IDLE;

    unsigned long stateStartTime = 0;
    unsigned long retryTimer     = 0;
    int           retryCount     = 0;

    // ── Process protection state ─────────────────────────────────────────────
    FaultReason   faultReason          = FaultReason::NONE;
    unsigned long flowFaultTimer       = 0;
    bool          flowFaultArmed       = false;
    unsigned long recoveryFaultTimer   = 0;
    bool          recoveryFaultArmed   = false;
    unsigned long pMembraneHighTimer   = 0;
    bool          pMembraneHighArmed   = false;
    // ────────────────────────────────────────────────────────────────────────

    // Set to true for exactly one loop iteration when FSM first enters FAULT.
    // consumeFaultEvent() reads and clears it atomically.
    bool          _justFaulted         = false;

    unsigned long demandaStart = 0;
    unsigned long crudoStart   = 0;
    unsigned long presionStart = 0;

    // 🔥 NUEVO: estado REAL de salidas
    OutputsState outputs;

    // OUTPUTS
    void setOutputs(bool pumpLow, bool pumpHigh, bool flush, bool inlet);
    void startLow();
    void startHigh();
    void flushOn();
    void stopAll();

    void logStateChange(SystemState from, SystemState to);
    const char* stateToString(SystemState s);

    bool isValidTransition(CommandType cmd) const;
    void applyCommand(CommandType cmd);

    // Protección crítica: presión de membrana alta → FAULT (emergency stop).
    // Debounce via pressure_fault_delay_sec. Evaluada solo en STARTING/PRODUCING.
    bool checkMembraneHighPressure(Sensors& s);
};