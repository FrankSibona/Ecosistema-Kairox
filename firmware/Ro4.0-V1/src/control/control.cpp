#include "control.h"
#include "process_config.h"
#include "../safety/antifreeze.h"
#include <config.h>
#include <string.h>

// ================= SETUP =================
void Control::begin() {
    // Pin modes para outputs son aplicados por ioMapApplyPinModes() en setup()
    // antes de llamar a begin() — ver main.cpp. stopAll() usa setOutputs() →
    // ioMapGet() que ya está disponible.
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

const char* Control::getActivityName() {
    if (state == IDLE && antifreezeActive) return "ANTIFREEZE";
    if (state == FLUSHING) return "FLUSH_NORMAL";
    return stateToString(state);
}

bool Control::isAntifreezeActive() const {
    return antifreezeActive;
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
    outputs.pumpLow    = pumpLow;
    outputs.pumpHigh   = pumpHigh;
    outputs.valveFlush = flush;
    outputs.valveInlet = inlet;
    outputs.pumpInlet  = false;
    outputs.pumpDose   = false;

    const IOMapConfig& m = ioMapGet();
    const IOPinConfig& p1 = m.outputs[(uint8_t)LogicalOutput::LOW_PRESSURE_PUMP];
    const IOPinConfig& p2 = m.outputs[(uint8_t)LogicalOutput::HIGH_PRESSURE_PUMP];
    const IOPinConfig& p5 = m.outputs[(uint8_t)LogicalOutput::FLUSH_VALVE];
    const IOPinConfig& p6 = m.outputs[(uint8_t)LogicalOutput::INLET_VALVE];

    if (p1.gpio != IOMAP_GPIO_NONE) digitalWrite(p1.gpio, p1.invert ? !pumpLow  : (int)pumpLow);
    if (p2.gpio != IOMAP_GPIO_NONE) digitalWrite(p2.gpio, p2.invert ? !pumpHigh : (int)pumpHigh);
    if (p5.gpio != IOMAP_GPIO_NONE) digitalWrite(p5.gpio, p5.invert ? !flush    : (int)flush);
    if (p6.gpio != IOMAP_GPIO_NONE) digitalWrite(p6.gpio, p6.invert ? !inlet    : (int)inlet);
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

    // ===== Señales de gate de la FSM ─────────────────────────────────────────
    // demand/raw_water_available/pressure_ok: debounce simétrico aplicado en
    // Sensors::getSignal() (io_map.debounce_ms, default 2000ms — reproduce el
    // debounce hoy hardcodeado, ver IOMAP_VERSION v3 en config.h).
    bool demandaOK = ruleInputs[(uint8_t)LogicalInput::DEMAND];
    bool crudoOK   = ruleInputs[(uint8_t)LogicalInput::RAW_WATER_AVAILABLE];
    bool presionOK = ruleInputs[(uint8_t)LogicalInput::PRESSURE_OK];

    // Recalculado desde cero cada tick — solo case IDLE (rama sin arranque
    // por demanda) lo vuelve a poner en true. Garantiza que la telemetría
    // nunca arrastre "antifreeze activo" stale al salir de IDLE por
    // cualquier vía (demanda, comando, fault_rules, protecciones).
    antifreezeActive = false;

    if (state != lastState) {
        logStateChange(lastState, state);
        lastState = state;
    }

    switch(state) {

        case IDLE:
            {
                // process_permits["ro"] actúa como interlock en IDLE: aunque haya
                // demanda y agua cruda, si un permiso externo está bloqueado
                // (ej. softener_regenerating) el arranque no se habilita.
                bool permitOk = evalRule(rulesGet().process_permits[(uint8_t)ProcessId::RO], ruleInputs, ruleDerived);
                const ProcessConfig& pc = processConfigGet();
                bool retryWaiting = retryCount > 0 && (millis() - retryTimer < pc.retry_interval_sec * 1000UL);
                if (demandaOK && crudoOK && permitOk && !retryWaiting) {
                    Serial.println("[EVENT] Demanda detectada -> arranque");
                    antifreezeAbort();  // cede el control de inmediato — ver antifreeze.h
                    state = STARTING;
                    stateStartTime = millis();
                    break;
                }

                // Sin arranque por demanda este tick — protección anti-
                // congelamiento (prioridad mínima, nunca compite con
                // producción real). Usa exactamente las mismas condiciones
                // mínimas (crudoOK/permitOk) que un arranque normal — si un
                // arranque real tampoco podría ocurrir, antifreeze tampoco
                // actúa. No-op si antifreeze_enabled=0 (default).
                antifreezeActive = antifreezeEvaluate(millis(), crudoOK, permitOk);
            }
            if (antifreezeActive) {
                flushOn();
            } else {
                stopAll();
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

            {
                const ProcessConfig& pc = processConfigGet();
                if (millis() - stateStartTime > pc.pressure_stabilization_delay_sec * 1000UL) {
                    startHigh();
                }

                if (millis() - stateStartTime > pc.startup_timeout_sec * 1000UL) {
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
            // interlocks, niveles). ruleInputs[] ya contiene las señales
            // estabilizadas por Sensors::getSignal() (io_map: debounce +
            // default_value), incluido RAW_WATER_AVAILABLE (compat con default =
            // AND(raw_water_available)). pressure_ok NO participa del permit —
            // sigue siendo condición interna de la FSM (presionOK, abajo).
            {
                bool permitOk = evalRule(rulesGet().process_permits[(uint8_t)ProcessId::RO], ruleInputs, ruleDerived);

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
            if (checkFlowProtection(s)) break;

            // ── Protección de recovery ────────────────────────────────────────
            if (checkRecoveryProtection(s)) break;
            break;

        case FLUSHING:
            flushOn();

            if (millis() - stateStartTime > processConfigGet().flush_duration_sec * 1000UL) {
                Serial.println("[EVENT] Fin flushing");
                state = IDLE;
            }
            break;

        case FAULT:
            stopAll();
            break;

        // Reservado para secuencia de apagado controlado (despresurizar, cerrar válvulas en orden).
        case STOPPING:
            state = IDLE;
            break;
    }

    if (processConfigGet().max_retries > 0 && retryCount >= (int)processConfigGet().max_retries && state != FAULT) {
        faultReason = FaultReason::MAX_RETRIES;
        state = FAULT;
    }

    // ===== INDEPENDENT OUTPUTS — evaluadas cada loop, fuera del switch(state) =====
    bool indOut[(uint8_t)LogicalOutput::COUNT];
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        indOut[i] = evalRule(rulesGet().independent_outputs[i], ruleInputs, ruleDerived);
    }

    // well_pump — controlado por independent_outputs["well_pump"] (io_map).
    outputs.pumpInlet = indOut[(uint8_t)LogicalOutput::WELL_PUMP];
    {
        const IOPinConfig& p3 = ioMapGet().outputs[(uint8_t)LogicalOutput::WELL_PUMP];
        if (p3.gpio != IOMAP_GPIO_NONE) {
            digitalWrite(p3.gpio, p3.invert ? !outputs.pumpInlet : (int)outputs.pumpInlet);
        }
    }

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

// ================= FLOW / RECOVERY PROTECTIONS =================

// Respeta flow_protection_enabled — si 0, desarma el timer y retorna false
// (misma semántica que checkMembraneHighPressure con *_limits_enabled=0).
bool Control::checkFlowProtection(Sensors& s) {
    const SensorConfig& cfg = s.getConfig();
    if (!cfg.flow_protection_enabled) {
        flowFaultArmed = false;
        return false;
    }
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
            return true;
        }
    } else {
        flowFaultArmed = false;
    }
    return false;
}

// Respeta recovery_protection_enabled — si 0, desarma el timer y retorna false.
bool Control::checkRecoveryProtection(Sensors& s) {
    const SensorConfig& cfg = s.getConfig();
    if (!cfg.recovery_protection_enabled) {
        recoveryFaultArmed = false;
        return false;
    }
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
                return true;
            }
        } else {
            recoveryFaultArmed = false;
        }
    } else {
        recoveryFaultArmed = false;
    }
    return false;
}

// ================= COMMAND VALIDATION =================

bool Control::isValidTransition(CommandType cmd) const {
    switch (cmd) {
        case CommandType::START:
            return state == IDLE;
        case CommandType::STOP:
            // (state==IDLE && antifreezeActive): permite a un operador cancelar
            // un ciclo de circulación anti-congelamiento en curso — sin esto,
            // STOP queda rechazado mientras la FSM está "IDLE" aunque las
            // bombas estén físicamente circulando agua (ver applyCommand()).
            return state == STARTING || state == PRODUCING || state == FLUSHING
                || (state == IDLE && antifreezeActive);
        case CommandType::FLUSH:
            return state == PRODUCING;
        case CommandType::RST:
            // Only valid in fault/error states — rejected in IDLE to prevent
            // silent no-ops becoming unsafe if reset behavior gains side-effects.
            // Excepción: (state==IDLE && antifreezeActive) — mismo criterio que
            // STOP arriba, permite cancelar un ciclo anti-congelamiento activo.
            return state == FAULT || state == STARTING || state == FLUSHING
                || (state == IDLE && antifreezeActive);
        default:
            return false;
    }
}

// ================= COMMAND APPLICATION =================

void Control::applyCommand(CommandType cmd) {
    switch (cmd) {
        case CommandType::START:
            antifreezeAbort();  // cede el control de inmediato — único comando válido desde IDLE
            state = STARTING;
            stateStartTime = millis();
            break;
        case CommandType::STOP:
            // antifreezeAbort() es no-op si no había ciclo en curso (rama
            // STARTING/PRODUCING/FLUSHING, comportamiento sin cambios). Si
            // state==IDLE, solo es alcanzable con antifreezeActive==true (ver
            // isValidTransition()) — cancela el ciclo y permanece en IDLE.
            antifreezeAbort();
            // From PRODUCING: flush membrane before halting.
            // From STARTING / FLUSHING: abort immediately.
            // From IDLE (antifreeze): queda en IDLE — no hay producción que
            // detener, solo se canceló la circulación de arriba.
            state = (state == PRODUCING) ? FLUSHING : IDLE;
            stateStartTime = millis();
            break;
        case CommandType::FLUSH:
            // Only reachable from PRODUCING (enforced by isValidTransition).
            state = FLUSHING;
            stateStartTime = millis();
            break;
        case CommandType::RST:
            // antifreezeAbort() es no-op si no había ciclo en curso (rama
            // FAULT/STARTING/FLUSHING, comportamiento sin cambios). Si
            // state==IDLE, solo es alcanzable con antifreezeActive==true (ver
            // isValidTransition()) — cancela el ciclo; el resto del reset
            // (retry/fault flags) es inocuo en ese caso, ya en su default.
            antifreezeAbort();
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