#include "rules.h"
#include <Arduino.h>
#include <Preferences.h>
#include <string.h>
#include <config.h>
#include "control/control.h"

static RulesConfig _cfg;

// ============================================================
// Nombres estables (claves JSON/NVS)
// ============================================================

static const char* PROCESS_NAMES[(uint8_t)ProcessId::COUNT] = {
    "ro",
};

const char* processName(ProcessId p) {
    uint8_t i = (uint8_t)p;
    return (i < (uint8_t)ProcessId::COUNT) ? PROCESS_NAMES[i] : "";
}

ProcessId processFromName(const char* name) {
    for (uint8_t i = 0; i < (uint8_t)ProcessId::COUNT; i++) {
        if (strcmp(name, PROCESS_NAMES[i]) == 0) return (ProcessId)i;
    }
    return ProcessId::COUNT;
}

// Índice 0 (NONE) intencionalmente "" — no seleccionable, representa slot
// vacío en fault_rules[]. Debe coincidir 1:1 con FaultReason en control.h.
static const char* FAULT_REASON_NAMES[] = {
    "",                       // NONE
    "MAX_RETRIES",
    "FLOW_LOW",
    "RECOVERY_LOW",
    "RECOVERY_HIGH",
    "PRESSURE_MEMBRANE_HIGH",
    "PHASE_FAILURE",
};
#define FAULT_REASON_COUNT (sizeof(FAULT_REASON_NAMES) / sizeof(FAULT_REASON_NAMES[0]))

const char* faultReasonName(FaultReason r) {
    uint8_t i = (uint8_t)r;
    return (i < FAULT_REASON_COUNT) ? FAULT_REASON_NAMES[i] : "";
}

FaultReason faultReasonFromName(const char* name) {
    for (uint8_t i = 1; i < FAULT_REASON_COUNT; i++) {  // i=0 (NONE) no es seleccionable
        if (strcmp(name, FAULT_REASON_NAMES[i]) == 0) return (FaultReason)i;
    }
    return FaultReason::NONE;
}

// ============================================================
// Defaults — reproducen EXACTAMENTE el comportamiento actual sin reglas
// configuradas (ver docs/KAIROX_ARQUITECTURA_SENALES_REGLAS.md, sección 11)
// ============================================================

static RulesConfig defaultRules() {
    RulesConfig cfg{};

    // "Sin regla configurada" = OR con 0 términos -> evalRule() devuelve
    // false ("sin efecto" para independent_outputs / permit cerrado por
    // defecto para process_permits no asignados).
    RuleConfig noRule = { RuleOp::OR, 0, {} };

    for (uint8_t i = 0; i < (uint8_t)ProcessId::COUNT; i++) {
        cfg.process_permits[i] = noRule;
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        cfg.independent_outputs[i] = noRule;
    }
    for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) {
        cfg.fault_rules[i].condition = noRule;
        cfg.fault_rules[i].reason    = FaultReason::NONE;
        cfg.fault_rules[i].delay_sec = 0;
    }

    // process_permits["ro"] = AND(raw_water_available) — 1 término. Junto al
    // override de RAW_WATER_AVAILABLE -> crudoOK (control.cpp), reproduce
    // exactamente la condición actual `if (!crudoOK || ...)`. pressure_ok NO
    // participa — sigue siendo condición interna de la FSM (presionOK).
    cfg.process_permits[(uint8_t)ProcessId::RO] = RuleConfig{
        RuleOp::AND, 1, { RuleTerm{(uint8_t)LogicalInput::RAW_WATER_AVAILABLE, SignalSrc::INPUT, 0} }
    };

    // independent_outputs["well_pump"] = OR(well_low_level) — reproduce
    // exactamente el control directo D5->R3 actual.
    cfg.independent_outputs[(uint8_t)LogicalOutput::WELL_PUMP] = RuleConfig{
        RuleOp::OR, 1, { RuleTerm{(uint8_t)LogicalInput::WELL_LOW_LEVEL, SignalSrc::INPUT, 0} }
    };

    cfg.fault_rule_count = 0;  // sin fault_rules por defecto
    cfg.updated_at = 0;
    return cfg;
}

// ============================================================
// Persistencia NVS — mismo patrón que io_map.cpp (namespace "kx_rules")
// ============================================================

static void rulesSave() {
    Preferences p;
    p.begin("kx_rules", false);
    p.putUInt("magic", RULES_MAGIC);
    p.putUInt("version", RULES_VERSION);
    p.putBytes("data", &_cfg, sizeof(_cfg));
    p.end();
}

void rulesInit() {
    _cfg = defaultRules();

    Preferences p;
    p.begin("kx_rules", true);
    uint32_t magic   = p.getUInt("magic", 0);
    uint32_t version = p.getUInt("version", 0);
    if (magic == RULES_MAGIC && version == RULES_VERSION) {
        RulesConfig stored;
        if (p.getBytes("data", &stored, sizeof(stored)) == sizeof(stored)) {
            _cfg = stored;
        }
    } else {
        Serial.println("[RULES] NVS vacío/incompatible — usando reglas por defecto");
    }
    p.end();

    Serial.printf("[RULES] Init OK — updated_at=%u\n", (unsigned)_cfg.updated_at);
}

const RulesConfig& rulesGet() {
    return _cfg;
}

static bool validRuleConfig(const RuleConfig& r) {
    if ((uint8_t)r.op > (uint8_t)RuleOp::OR) return false;
    if (r.term_count > RULE_MAX_TERMS) return false;
    for (uint8_t i = 0; i < r.term_count; i++) {
        const RuleTerm& t = r.terms[i];
        if ((uint8_t)t.source > (uint8_t)SignalSrc::DERIVED) return false;
        if (t.source == SignalSrc::INPUT   && t.signal_id >= (uint8_t)LogicalInput::COUNT)   return false;
        if (t.source == SignalSrc::DERIVED && t.signal_id >= (uint8_t)DerivedSignal::COUNT)  return false;
        if (t.negate > 1) return false;
    }
    return true;
}

bool rulesSet(const RulesConfig& incoming) {
    if (incoming.updated_at > 0 && incoming.updated_at <= _cfg.updated_at) {
        Serial.printf("[RULES] IGNORADO — ts %u <= actual %u\n",
                       (unsigned)incoming.updated_at, (unsigned)_cfg.updated_at);
        return false;
    }

    for (uint8_t i = 0; i < (uint8_t)ProcessId::COUNT; i++) {
        if (validRuleConfig(incoming.process_permits[i])) {
            _cfg.process_permits[i] = incoming.process_permits[i];
        } else {
            Serial.printf("[RULES] process_permits[%u] inválido — se conserva valor actual\n", i);
        }
    }
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (validRuleConfig(incoming.independent_outputs[i])) {
            _cfg.independent_outputs[i] = incoming.independent_outputs[i];
        } else {
            Serial.printf("[RULES] independent_outputs[%u] inválido — se conserva valor actual\n", i);
        }
    }
    for (uint8_t i = 0; i < FAULT_RULES_MAX; i++) {
        if (validRuleConfig(incoming.fault_rules[i].condition)) {
            _cfg.fault_rules[i] = incoming.fault_rules[i];
        } else {
            Serial.printf("[RULES] fault_rules[%u] inválido — se conserva valor actual\n", i);
        }
    }
    _cfg.fault_rule_count = (incoming.fault_rule_count <= FAULT_RULES_MAX)
                                ? incoming.fault_rule_count
                                : FAULT_RULES_MAX;

    if (incoming.updated_at > 0) _cfg.updated_at = incoming.updated_at;

    rulesSave();
    Serial.printf("[RULES] Guardado en NVS — updated_at=%u\n", (unsigned)_cfg.updated_at);
    return true;
}

// ============================================================
// Evaluación
// ============================================================

bool evalRule(const RuleConfig& r,
               const bool inputs[(uint8_t)LogicalInput::COUNT],
               const bool derived[(uint8_t)DerivedSignal::COUNT]) {
    bool result = (r.op == RuleOp::AND);  // AND vacío -> true, OR vacío -> false
    for (uint8_t i = 0; i < r.term_count && i < RULE_MAX_TERMS; i++) {
        const RuleTerm& t = r.terms[i];
        bool v;
        if (t.source == SignalSrc::INPUT) {
            v = (t.signal_id < (uint8_t)LogicalInput::COUNT) ? inputs[t.signal_id] : false;
        } else {
            v = (t.signal_id < (uint8_t)DerivedSignal::COUNT) ? derived[t.signal_id] : false;
        }
        if (t.negate) v = !v;

        if (r.op == RuleOp::AND) result = result && v;
        else                     result = result || v;
    }
    return result;
}

void computeDerivedSignals(bool out[(uint8_t)DerivedSignal::COUNT], Control& c) {
    out[(uint8_t)DerivedSignal::RO_PRODUCING] = c.isRunning();
}
