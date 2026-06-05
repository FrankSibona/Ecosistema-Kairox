#include "diag_mode.h"
#include <string.h>

void DiagMode::activate(uint32_t duration_sec) {
    _active       = true;
    _duration_sec = duration_sec;
    _start_ms     = millis();
    _tr           = DiagTransitions{};
    memset(_prev, 0, sizeof(_prev));
    if (duration_sec > 0)
        Serial.printf("[DIAG] Activado: %us\n", duration_sec);
    else
        Serial.println("[DIAG] Activado: sin limite de tiempo");
}

void DiagMode::deactivate() {
    _active = false;
    Serial.println("[DIAG] Desactivado");
}

bool DiagMode::isActive() const { return _active; }

bool DiagMode::hasExpired() const {
    if (!_active || _duration_sec == 0) return false;
    return (millis() - _start_ms) >= (unsigned long)_duration_sec * 1000UL;
}

uint32_t DiagMode::remainingSec() const {
    if (!_active || _duration_sec == 0) return 0;
    unsigned long elapsed = (millis() - _start_ms) / 1000UL;
    return (elapsed >= _duration_sec) ? 0 : (_duration_sec - (uint32_t)elapsed);
}

void DiagMode::updateInputs(bool d1, bool d2, bool d3,
                             bool d4, bool d5, bool d6) {
    if (!_active) return;
    bool cur[6] = {d1, d2, d3, d4, d5, d6};
    for (int i = 0; i < 6; i++) {
        if (cur[i] != _prev[i]) _tr.d[i]++;
        _prev[i] = cur[i];
    }
}

const DiagTransitions& DiagMode::getTransitions() const { return _tr; }
