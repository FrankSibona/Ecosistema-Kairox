#include "control.h"
#include <config.h>
#include <string.h>

// ================= SETUP =================
void Control::begin() {
    pinMode(PIN_R1, OUTPUT);
    pinMode(PIN_R2, OUTPUT);
    pinMode(PIN_R3, OUTPUT);
    pinMode(PIN_R5, OUTPUT);
    pinMode(PIN_R6, OUTPUT);

    digitalWrite(PIN_R3, LOW);
    stopAll();
}

// ================= GETTERS =================

bool Control::isRunning() {
    return state == PRODUCING || state == STARTING || state == FLUSHING;
}

SystemState Control::getState() {
    return state;
}

int Control::getRetryCount() {
    return retryCount;
}

FaultReason Control::getFaultReason() {
    return faultReason;
}

bool Control::consumeFaultEvent() {
    bool v = _justFaulted;
    _justFaulted = false;
    return v;
}

bool Control::isFlowFaultArmed()     const { return flowFaultArmed; }
bool Control::isRecoveryFaultArmed() const { return recoveryFaultArmed; }

unsigned long Control::getFlowFaultElapsedMs() const {
    return flowFaultArmed ? (millis() - flowFaultTimer) : 0UL;
}

unsigned long Control::getRecoveryFaultElapsedMs() const {
    return recoveryFaultArmed ? (millis() - recoveryFaultTimer) : 0UL;
}

const char* Control::getFaultReasonName() {
    return faultReasonName(faultReason);
}

const char* Control::getStateName() {
    return stateToString(state);
}

// 👉 ESTO ES CLAVE PARA MQTT
OutputsState Control::getOutputs() {
    return outputs;
}

// ================= LOG =================

const char* Control::stateToString(SystemState s) {
    switch(s) {
        case IDLE: return "IDLE";
        case STARTING: return "STARTING";
        case PRODUCING: return "PRODUCING";
        case FLUSHING: return "FLUSHING";
        case STOPPING: return "STOPPING";
        case FAULT: return "FAULT";
        default: return "UNKNOWN";
    }
}

void Control::logStateChange(SystemState from, SystemState to) {
    Serial.print("[STATE] ");
    Serial.print(stateToString(from));
    Serial.print(" -> ");
    Serial.println(stateToString(to));
}

// ================= OUTPUTS =================

void Control::setOutputs(bool pumpLow, bool pumpHigh, bool flush, bool inlet) {

    outputs.pumpLow = pumpLow;
    outputs.pumpHigh = pumpHigh;
    outputs.valveFlush = flush;
    outputs.valveInlet = inlet;

    // no usados aún
    outputs.pumpInlet = false;
    outputs.pumpDose = false;

    digitalWrite(PIN_R1, pumpLow);
    digitalWrite(PIN_R2, pumpHigh);
    digitalWrite(PIN_R5, flush);
    digitalWrite(PIN_R6, inlet);
}

void Control::startLow() {
    setOutputs(true, false, false, true);
}

void Control::startHigh() {
    setOutputs(true, true, false, true);
}

void Control::flushOn() {
    setOutputs(true, false, true, true);
}

void Control::stopAll() {
    setOutputs(false, false, false, false);
}

// ================= FSM =================

void Control::update(Sensors &s, Commands &cmds, const bool* ruleInputs, const bool* ruleDerived) {

    // ===== COMMAND ENGINE =====
    // Remote commands are processed first so they take effect this iteration.
    // Order: applyCommand() → setPendingAck(EXECUTED) → clearPending().
    // EXECUTED means the FSM transition was applied, not that hardware confirmed it.
    // If applyCommand() ever gains a return value or internal validation,
    // condition setPendingAck(EXECUTED) on its result here — no structural change needed.
    if (cmds.hasPending()) {
        if (cmds.hasExpired()) {
            cmds.clearPending();
        } else {
            const PendingCmd cmd = cmds.getPending();  // value copy before slot is cleared
            if (isValidTransition(cmd.type)) {
                applyCommand(cmd.type);
                cmds.setPendingAck(cmd.id, cmd.type, AckStatus::EXECUTED, "");
                cmds.clearPending();
            } else {
                cmds.setPendingAck(cmd.id, cmd.type, AckStatus::REJECTED, "invalid_fsm_state");
                cmds.clearPending();
            }
        }
    }

    // ===== Persistencia =====
    if (s.demanda()) {
        if (demandaStart == 0) demandaStart = millis();
    } else demandaStart = 0;

    if (s.crudoDisponible()) {
        if (crudoStart == 0) crudoStart = millis();
    } else crudoStart = 0;

    if (s.presionOK()) {
        if (presionStart == 0) presionStart = millis();
    } else presionStart = 0;

    bool demandaOK = demandaStart && (millis() - demandaStart > 2000);
    bool crudoOK   = crudoStart   && (millis() - crudoStart > 2000);
    bool presionOK = presionStart && (millis() - presionStart > 2000);

    if (state != lastState) {
        logStateChange(lastState, state);
        lastState = state;
    }

    switch(state) {

        case IDLE:
            stopAll();

            if (demandaOK && crudoOK) {
                bool retryWaiting = retryCount > 0 && (millis() - retryTimer < RETRY_DELAY);
                if (!retryWaiting) {
                    Serial.println("[EVENT] Demanda detectada -> arranque");
                    state = STARTING;
                    stateStartTime = millis();
                }
            }
            break;

        case STARTING:
            startLow();

            if (!crudoOK) {
                Serial.println("[FAULT] Sin agua de crudo");
                pMembraneHighArmed = false;
                for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) faultRuleArmed[i] = false;
                state = IDLE;
                break;
            }

            if (millis() - stateStartTime > LOW_PUMP_FILL_TIME) {
                startHigh();
            }

            if (millis() - stateStartTime > PRESSURE_CHECK_TIME) {

                if (presionOK) {
                    Serial.println("[EVENT] Presión OK");
                    retryCount = 0;
                    state = PRODUCING;
                } else {
                    retryCount++;
                    Serial.println("[FAULT] Presión no alcanzada");
                    pMembraneHighArmed = false;
                    for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) faultRuleArmed[i] = false;
                    state = IDLE;
                    retryTimer = millis();
                }
            }

            checkMembraneHighPressure(s);
            checkFaultRules(ruleInputs, ruleDerived);
            break;

        case PRODUCING:
            startHigh();

            if (!demandaOK) {
                Serial.println("[EVENT] Fin demanda -> flushing");
                // Reset protection timers on clean exit — no fault condition
                flowFaultArmed     = false;
                recoveryFaultArmed = false;
                pMembraneHighArmed = false;
                for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) faultRuleArmed[i] = false;
                state = FLUSHING;
                stateStartTime = millis();
                break;
            }

            // process_permits["ro"] — condiciones EXTERNAS de operación (demanda,
            // interlocks, niveles). RAW_WATER_AVAILABLE usa el valor debounced de la
            // FSM (compat con default = AND(raw_water_available)); el resto (incl.
            // señales nuevas Chamico) usa ruleInputs[] crudo (io_map). pressure_ok NO
            // participa del permit — sigue siendo condición interna de la FSM
            // (presionOK, abajo).
            {
                bool permitInputs[(uint8_t)LogicalInput::COUNT];
                memcpy(permitInputs, ruleInputs, sizeof(permitInputs));
                permitInputs[(uint8_t)LogicalInput::RAW_WATER_AVAILABLE] = crudoOK;

                bool permitOk = evalRule(rulesGet().process_permits[(uint8_t)ProcessId::RO], permitInputs, ruleDerived);

                if (!permitOk || !presionOK) {
                    Serial.println("[FAULT] Pérdida condición");
                    flowFaultArmed     = false;
                    recoveryFaultArmed = false;
                    pMembraneHighArmed = false;
                    for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) faultRuleArmed[i] = false;
                    state = IDLE;
                    break;
                }
            }

            // ── Protección de presión de membrana alta (única protección crítica V1) ──
            if (checkMembraneHighPressure(s)) break;

            // ── fault_rules[] configurables por instalación (ej. phase_failure) ──
            if (checkFaultRules(ruleInputs, ruleDerived)) break;

            // ── Protección de caudal de permeado ─────────────────────────────
            {
                const SensorConfig& cfg = s.getConfig();
                float flowP = s.getFlow1();
                unsigned long delayMs = (unsigned long)cfg.flow_fault_delay_sec * 1000UL;

                if (flowP < cfg.min_flow_lpm) {
                    if (!flowFaultArmed) {
                        flowFaultTimer = millis();
                        flowFaultArmed = true;
                    } else if (millis() - flowFaultTimer >= delayMs) {
                        Serial.printf("[FAULT] FLOW_LOW: %.2f L/min < %.2f L/min (umbral)\n",
                                      flowP, cfg.min_flow_lpm);
                        faultReason    = FaultReason::FLOW_LOW;
                        flowFaultArmed = false;
                        state          = FAULT;
                        break;
                    }
                } else {
                    flowFaultArmed = false;
                }
            }

            // ── Protección de recovery ────────────────────────────────────────
            {
                const SensorConfig& cfg = s.getConfig();
                float flowP = s.getFlow1();
                float flowR = s.getFlow2();
                float total = flowP + flowR;

                if (total > 0.1f) {
                    float recoveryPct = (flowP / total) * 100.0f;
                    bool outOfRange = (recoveryPct < cfg.min_recovery_pct) ||
                                      (recoveryPct > cfg.max_recovery_pct);
                    unsigned long delayMs = (unsigned long)cfg.recovery_fault_delay_sec * 1000UL;

                    if (outOfRange) {
                        if (!recoveryFaultArmed) {
                            recoveryFaultTimer = millis();
                            recoveryFaultArmed = true;
                        } else if (millis() - recoveryFaultTimer >= delayMs) {
                            if (recoveryPct < cfg.min_recovery_pct) {
                                Serial.printf("[FAULT] RECOVERY_LOW: %.1f%% < %.1f%% (umbral)\n",
                                              recoveryPct, cfg.min_recovery_pct);
                                faultReason = FaultReason::RECOVERY_LOW;
                            } else {
                                Serial.printf("[FAULT] RECOVERY_HIGH: %.1f%% > %.1f%% (umbral)\n",
                                              recoveryPct, cfg.max_recovery_pct);
                                faultReason = FaultReason::RECOVERY_HIGH;
                            }
                            recoveryFaultArmed = false;
                            state = FAULT;
                            break;
                        }
                    } else {
                        recoveryFaultArmed = false;
                    }
                } else {
                    recoveryFaultArmed = false;  // sin flujo medible — no evaluar
                }
            }
            break;

        case FLUSHING:
            flushOn();

            if (millis() - stateStartTime > FLUSH_TDS_TIME) {
                Serial.println("[EVENT] Fin flushing");
                state = IDLE;
            }
            break;

        case FAULT:
            stopAll();
            break;

        case STOPPING:
            state = IDLE;
            break;
    }

    if (retryCount >= FSM_MAX_RETRIES && state != FAULT) {
        faultReason = FaultReason::MAX_RETRIES;
        state = FAULT;
    }

    // ===== INDEPENDENT OUTPUTS — evaluadas cada loop, fuera del switch(state) =====
    bool indOut[(uint8_t)LogicalOutput::COUNT];
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        indOut[i] = evalRule(rulesGet().independent_outputs[i], ruleInputs, ruleDerived);
    }

    // well_pump — reemplaza el control directo D5->R3. Default reproduce EXACTO
    // el comportamiento actual: independent_outputs["well_pump"] = OR(well_low_level).
    outputs.pumpInlet = indOut[(uint8_t)LogicalOutput::WELL_PUMP];
    digitalWrite(PIN_R3, outputs.pumpInlet);

    // transfer_pump — sin PIN_R* fijo; usa el GPIO de io_map si está asignado
    // (Chamico/lab). Sin asignar -> sin efecto (no se llama digitalWrite).
    const IOPinConfig& transferOut = ioMapGet().outputs[(uint8_t)LogicalOutput::TRANSFER_PUMP];
    if (transferOut.gpio != IOMAP_GPIO_NONE) {
        digitalWrite(transferOut.gpio, indOut[(uint8_t)LogicalOutput::TRANSFER_PUMP]);
    }

    // Fire the fault event flag for exactly one iteration when FSM enters FAULT.
    // lastState still holds the pre-switch value here — updated next iteration.
    _justFaulted = (state == FAULT && lastState != FAULT);
}

// ================= PRESSURE PROTECTION =================

// Única protección crítica de presión en V1: membrana alta presión.
// Requiere pressure_membrane_enabled (calibración cargada) y
// pressure_membrane_limits_enabled (protección habilitada). Debounce vía
// pressure_fault_delay_sec, mismo patrón que FLOW_LOW/RECOVERY_*.
bool Control::checkMembraneHighPressure(Sensors& s) {
    const SensorConfig& cfg = s.getConfig();
    if (!cfg.pressure_membrane_enabled || !cfg.pressure_membrane_limits_enabled) {
        pMembraneHighArmed = false;
        return false;
    }

    unsigned long delayMs = (unsigned long)cfg.pressure_fault_delay_sec * 1000UL;

    if (s.getPressure1() > cfg.pressure_membrane_high_limit) {
        if (!pMembraneHighArmed) {
            pMembraneHighTimer = millis();
            pMembraneHighArmed = true;
        } else if (millis() - pMembraneHighTimer >= delayMs) {
            Serial.printf("[FAULT] PRESSURE_MEMBRANE_HIGH: %.2f bar > %.2f bar (umbral)\n",
                          s.getPressure1(), cfg.pressure_membrane_high_limit);
            faultReason        = FaultReason::PRESSURE_MEMBRANE_HIGH;
            pMembraneHighArmed = false;
            state              = FAULT;
            return true;
        }
    } else {
        pMembraneHighArmed = false;
    }
    return false;
}

// fault_rules[] configurables por instalación (ver src/rules/rules.h).
// Generaliza el patrón arm/timer de checkMembraneHighPressure() para
// condiciones de falla nuevas por instalación (ej. phase_failure, Chamico).
// Con fault_rule_count==0 (default) es un no-op.
bool Control::checkFaultRules(const bool* ruleInputs, const bool* ruleDerived) {
    const RulesConfig& r = rulesGet();
    for (uint8_t i = 0; i < r.fault_rule_count && i < FAULT_RULES_MAX; i++) {
        const FaultRuleConfig& fr = r.fault_rules[i];
        if (fr.reason == FaultReason::NONE) continue;

        unsigned long delayMs = (unsigned long)fr.delay_sec * 1000UL;

        if (evalRule(fr.condition, ruleInputs, ruleDerived)) {
            if (!faultRuleArmed[i]) {
                faultRuleTimer[i] = millis();
                faultRuleArmed[i] = true;
            } else if (millis() - faultRuleTimer[i] >= delayMs) {
                Serial.printf("[FAULT] fault_rules[%u] -> %s\n", i, faultReasonName(fr.reason));
                faultReason       = fr.reason;
                faultRuleArmed[i] = false;
                state             = FAULT;
                return true;
            }
        } else {
            faultRuleArmed[i] = false;
        }
    }
    return false;
}

// ================= COMMAND VALIDATION =================

bool Control::isValidTransition(CommandType cmd) const {
    switch (cmd) {
        case CommandType::START:
            return state == IDLE;
        case CommandType::STOP:
            return state == STARTING || state == PRODUCING || state == FLUSHING;
        case CommandType::FLUSH:
            return state == PRODUCING;
        case CommandType::RST:
            // Only valid in fault/error states — rejected in IDLE to prevent
            // silent no-ops becoming unsafe if reset behavior gains side-effects.
            return state == FAULT || state == STARTING || state == FLUSHING;
        default:
            return false;
    }
}

// ================= COMMAND APPLICATION =================

void Control::applyCommand(CommandType cmd) {
    switch (cmd) {
        case CommandType::START:
            state = STARTING;
            stateStartTime = millis();
            break;
        case CommandType::STOP:
            // From PRODUCING: flush membrane before halting.
            // From STARTING / FLUSHING: abort immediately.
            state = (state == PRODUCING) ? FLUSHING : IDLE;
            stateStartTime = millis();
            break;
        case CommandType::FLUSH:
            // Only reachable from PRODUCING (enforced by isValidTransition).
            state = FLUSHING;
            stateStartTime = millis();
            break;
        case CommandType::RST:
            retryCount         = 0;
            faultReason        = FaultReason::NONE;
            flowFaultArmed     = false;
            recoveryFaultArmed = false;
            pMembraneHighArmed = false;
            for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) faultRuleArmed[i] = false;
            state              = IDLE;
            break;
        default:
            break;
    }
}