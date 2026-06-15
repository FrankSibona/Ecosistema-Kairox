#pragma once
#include <stdint.h>
#include "io/io_catalog.h"

// ============================================================
// MOTOR DE REGLAS — process_permits[] / independent_outputs[] / fault_rules[]
// ============================================================
//
// Tres categorías, responsabilidades disjuntas (ver
// docs/KAIROX_ARQUITECTURA_SENALES_REGLAS.md, sección 11):
//
//   - process_permits[]      → "¿este proceso puede producir ahora?"
//                               efecto: state=IDLE (espera). Nunca FAULT.
//                               Solo condiciones EXTERNAS de operación
//                               (demanda, interlocks, niveles). pressure_ok
//                               NO participa — sigue siendo condición interna
//                               de la FSM de arranque (control.cpp).
//   - independent_outputs[]  → "¿este actuador debe estar encendido?"
//                               evaluado cada loop, fuera del switch(state).
//   - fault_rules[]           → "¿esta condición es una falla que detiene el
//                               equipo?" efecto: state=FAULT. Mecanismo de
//                               extensión para señales nuevas por instalación
//                               (ej. phase_failure); FLOW_LOW/RECOVERY_*/
//                               PRESSURE_MEMBRANE_HIGH/MAX_RETRIES siguen
//                               siendo protecciones C++ fijas.
//
// Persistencia: NVS namespace "kx_rules" (RULES_MAGIC/RULES_VERSION en
// config.h), sincronizado vía MQTT retained fyntek/{device_id}/rules.
//
// NOTA — include circular: este header NO incluye control/control.h
// (control.h incluye este header para FAULT_RULES_MAX). FaultReason y
// Control se forward-declaran abajo; rules.cpp incluye control.h para las
// definiciones completas.

#define RULE_MAX_TERMS 4

enum class RuleOp : uint8_t { AND = 0, OR = 1 };
enum class SignalSrc : uint8_t { SIG_INPUT = 0, DERIVED = 1 };

// Procesos del sistema — hoy solo "ro". Append-only, igual criterio que
// LogicalInput/LogicalOutput.
enum class ProcessId : uint8_t {
    RO = 0,
    COUNT
};

const char* processName(ProcessId p);
ProcessId   processFromName(const char* name);

// Un término de regla: señal (input crudo o derivada) + negación opcional.
struct RuleTerm {
    uint8_t   signal_id;  // índice en LogicalInput o DerivedSignal según source
    SignalSrc source;
    uint8_t   negate;     // 1 = NOT
};

// op AND con term_count==0 evalúa a true (vacuously true); op OR con
// term_count==0 evalúa a false. Convención estándar — "sin regla configurada"
// usa OR/0 términos para representar "sin efecto" (false).
struct RuleConfig {
    RuleOp   op;
    uint8_t  term_count;  // 0..RULE_MAX_TERMS
    RuleTerm terms[RULE_MAX_TERMS];
};

#define FAULT_RULES_MAX 4

// FaultReason se define en control/control.h. Forward-declarado aquí (tipo
// subyacente fijo, válido desde C++11) para evitar el include circular —
// control.h incluye este header para FAULT_RULES_MAX.
enum class FaultReason : uint8_t;

// Condición configurable → FaultReason, con debounce propio (delay_sec,
// mismo patrón que pressure_fault_delay_sec). reason==NONE = slot vacío.
struct FaultRuleConfig {
    RuleConfig  condition;
    FaultReason reason;
    uint32_t    delay_sec;
};

struct RulesConfig {
    RuleConfig      process_permits[(uint8_t)ProcessId::COUNT];
    RuleConfig      independent_outputs[(uint8_t)LogicalOutput::COUNT];
    FaultRuleConfig fault_rules[FAULT_RULES_MAX];
    uint8_t         fault_rule_count;  // 0..FAULT_RULES_MAX
    uint32_t        updated_at;        // epoch seconds — version field
};

// Carga las reglas desde NVS (o aplica defaultRules() si no hay datos
// válidos). defaultRules() reproduce el comportamiento actual sin reglas
// configuradas.
void rulesInit();

const RulesConfig& rulesGet();

// Aplica reglas entrantes (partial update por slot: entradas inválidas
// conservan el valor actual). Si incoming.updated_at > 0 y es <= al valor
// actual, el mensaje completo se ignora (mismo patrón que ioMapSet).
// Persiste en NVS si se aplica. Retorna true si se aplicó.
bool rulesSet(const RulesConfig& incoming);

// Evalúa una RuleConfig contra el snapshot de señales del loop actual.
bool evalRule(const RuleConfig& r,
               const bool inputs[(uint8_t)LogicalInput::COUNT],
               const bool derived[(uint8_t)DerivedSignal::COUNT]);

// Calcula las señales derivadas (DerivedSignal) desde el estado de Control,
// sin modificar la FSM. Forward-declarado para evitar el include circular.
class Control;
void computeDerivedSignals(bool out[(uint8_t)DerivedSignal::COUNT], Control& c);

// Nombres estables para FaultReason (config /rules), mismo criterio que
// logicalInputName/derivedSignalName. FaultReason::NONE -> "" (no
// seleccionable — slot vacío en fault_rules[]).
const char* faultReasonName(FaultReason r);
FaultReason faultReasonFromName(const char* name);
