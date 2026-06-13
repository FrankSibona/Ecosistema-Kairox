#include <Arduino.h>
#include "sensors/sensors.h"
#include "control/control.h"
#include "comms/comms.h"
#include "commands/commands.h"
#include "diag/diag_mode.h"
#include "diag/flight_recorder.h"
#include "safety/watchdog.h"
#include "io/io_map.h"

Sensors       sensors;
Control       control;
Comms         comms;
Commands      commands;
DiagMode      diagMode;
FlightRecorder flightRec;

void setup() {
    Serial.begin(115200);

    logResetReason();
    watchdogInit();

    sensors.begin();
    control.begin();
    ioMapInit();  // carga mapeo Pin<->Señal desde NVS — sin efectos sobre GPIO
    commands.begin();
    comms.begin(commands, sensors, diagMode, flightRec);

    Serial.println("=== SYSTEM START ===");
}

void loop() {

    // 1. Sensores
    sensors.update();

    // 2. Control + Command Engine (CRÍTICO)
    control.update(sensors, commands);

    // 3. Comunicaciones — procesa callbacks MQTT y publica ACK pendiente
    comms.update(sensors, control, commands, diagMode, flightRec);

    // 4. Debug liviano
    static unsigned long lastDebug = 0;
    if (millis() - lastDebug > 1000) {
        lastDebug = millis();

        Serial.print("[DEBUG] RUNNING: ");
        Serial.print(control.isRunning());
        Serial.print(" | STATE: ");
        Serial.println(control.getStateName());
    }

    // 5. Pequeño respiro al CPU (IMPORTANTE)
    delay(10);

    // 6. Watchdog — única señal de "estoy vivo". Alimentar SOLO aquí.
    watchdogReset();
}