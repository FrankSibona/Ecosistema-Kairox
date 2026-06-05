#pragma once
#include <Arduino.h>

// Transition counter for each digital input (D1..D6).
// Incremented on every state change while diagnostic mode is active.
// Cleared on activate().
struct DiagTransitions {
    uint16_t d[6] = {};
};

// ── DiagMode ──────────────────────────────────────────────────────────────────
// Manages the remote diagnostic mode lifecycle.
//
// Activation: backend publishes to fyntek/{id}/diag/ctrl
//   {"enable":true, "duration_sec":300}   — activate for N seconds (0 = no limit)
//   {"enable":false}                       — deactivate immediately
//
// While active, comms.cpp publishes /diag at 1 Hz with full sensor/FSM data.
// Automatically deactivates when duration_sec elapses.
// Has zero cost when inactive (no timers, no allocations, no CPU in update()).
class DiagMode {
public:
    void     activate(uint32_t duration_sec);
    void     deactivate();

    bool     isActive()     const;
    bool     hasExpired()   const;
    uint32_t remainingSec() const;

    // Call every loop iteration. Updates transition counters only when active.
    void     updateInputs(bool d1, bool d2, bool d3,
                          bool d4, bool d5, bool d6);

    const DiagTransitions& getTransitions() const;

private:
    bool            _active       = false;
    uint32_t        _duration_sec = 0;
    unsigned long   _start_ms     = 0;
    bool            _prev[6]      = {};
    DiagTransitions _tr;
};
