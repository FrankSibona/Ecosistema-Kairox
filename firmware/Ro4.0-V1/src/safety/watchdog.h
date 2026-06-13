#pragma once

// Watchdog hardware (ESP32 Task WDT) — única red de seguridad ante cuelgues
// reales (deadlock, bucle infinito, bloqueo de librería). Alimentar
// SOLO desde el loop principal con watchdogReset().
void watchdogInit();
void watchdogReset();

// Imprime por Serial la causa del último reinicio (esp_reset_reason) —
// diagnóstico de campo: distingue power-on normal de panic/task-wdt/etc.
void logResetReason();
