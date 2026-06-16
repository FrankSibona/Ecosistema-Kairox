#include <Arduino.h>
#include "sensors/sensors.h"
#include "control/control.h"
#include "comms/comms.h"
#include "commands/commands.h"
#include "diag/diag_mode.h"
#include "diag/flight_recorder.h"
#include "safety/watchdog.h"
#include "io/io_map.h"
#include "rules/rules.h"
#include "control/process_config.h"

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
    ioMapInit();          // carga io_map ANTES de control.begin() — setOutputs() usa ioMapGet()
    ioMapApplyPinModes(); // aplica pinMode() para todos los outputs/inputs del io_map
    rulesInit();          // carga motor de reglas desde NVS
    processConfigInit();  // carga parámetros de temporización FSM desde NVS
    control.begin();      // stopAll() → setOutputs() → ioMapGet() ya disponible
    commands.begin();
    comms.begin(commands, sensors, diagMode, flightRec);

    Serial.println("=== SYSTEM START ===");
}

void loop() {

    // 1. Sensores
    sensors.update();

    // 1b. Motor de reglas — snapshot de señales del loop actual (1-tick lag
    // para derivadas, por diseño: ro_producing refleja el estado previo).
    bool ruleInputs[(uint8_t)LogicalInput::COUNT];
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        ruleInputs[i] = sensors.getSignal((LogicalInput)i);
    }
    bool ruleDerived[(uint8_t)DerivedSignal::COUNT];
    computeDerivedSignals(ruleDerived, control);

    // 2. Control + Command Engine (CRÍTICO)
    control.update(sensors, commands, ruleInputs, ruleDerived);

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