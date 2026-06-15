"""
Catálogo del motor de reglas — process_permits[] / independent_outputs[] /
fault_rules[] (ver docs/KAIROX_ARQUITECTURA_SENALES_REGLAS.md, sección 11).

Debe mantenerse sincronizado con
firmware/Ro4.0-V1/src/rules/rules.h (RuleConfig/RuleTerm/FaultRuleConfig) y
firmware/Ro4.0-V1/src/rules/rules.cpp (defaultRules/FAULT_REASON_NAMES):
mismos nombres de señal/proceso/output/fault reason (claves usadas en
JSON/NVS/MQTT) y mismo default. Append-only — no renombrar ni eliminar claves
existentes.
"""

import copy
from typing import Optional

from io_catalog import LOGICAL_INPUTS, LOGICAL_OUTPUTS, DERIVED_SIGNALS

RULE_MAX_TERMS = 4
FAULT_RULES_MAX = 4

# Orden informativo (coincide con ProcessId en rules.h).
PROCESSES = [
    "ro",
]

# independent_outputs[] cubre el catálogo completo de LogicalOutput.
INDEPENDENT_OUTPUTS = LOGICAL_OUTPUTS

# FaultReason — debe coincidir 1:1 (salvo NONE) con FAULT_REASON_NAMES en
# rules.cpp. NONE no es seleccionable (representa slot vacío).
FAULT_REASONS = [
    "MAX_RETRIES",
    "FLOW_LOW",
    "RECOVERY_LOW",
    "RECOVERY_HIGH",
    "PRESSURE_MEMBRANE_HIGH",
    "PHASE_FAILURE",
]

PROCESS_LABELS = {
    "ro": "Ósmosis inversa",
}

DERIVED_LABELS = {
    "ro_producing": "RO produciendo",
}

FAULT_REASON_LABELS = {
    "MAX_RETRIES":            "Máximo de reintentos de arranque",
    "FLOW_LOW":               "Caudal de permeado bajo",
    "RECOVERY_LOW":           "Recovery bajo umbral",
    "RECOVERY_HIGH":          "Recovery sobre umbral",
    "PRESSURE_MEMBRANE_HIGH": "Presión de membrana alta",
    "PHASE_FAILURE":          "Falla de fase",
}

# Reglas por defecto — reproducen EXACTAMENTE el comportamiento actual sin
# reglas configuradas (ver defaultRules() en rules.cpp).
DEFAULT_RULES = {
    "process_permits": {
        "ro": {"op": "AND", "terms": [
            {"signal": "raw_water_available", "source": "input", "negate": False},
        ]},
        # pressure_ok NO participa de process_permits — sigue siendo
        # condición interna de la FSM de arranque (presionOK, control.cpp).
    },
    "independent_outputs": {
        "well_pump": {"op": "OR", "terms": [
            {"signal": "well_low_level", "source": "input", "negate": False},
        ]},
        # resto de LOGICAL_OUTPUTS: {"op":"OR","terms":[]}  (= false, "sin regla")
    },
    "fault_rules": [],   # vacío por defecto — MAX_RETRIES/FLOW_LOW/RECOVERY_*/
                          # PRESSURE_MEMBRANE_HIGH siguen siendo protecciones
                          # C++ fijas (SensorConfig *_enabled), no entradas de
                          # esta lista. Este array es para señales NUEVAS por
                          # instalación (ej. phase_failure).
}

_KNOWN_SIGNALS = set(LOGICAL_INPUTS) | set(DERIVED_SIGNALS)


def _default_rule(op: str = "OR") -> dict:
    return {"op": op, "terms": []}


def merge_rules(stored: Optional[dict]) -> dict:
    """Combina las reglas guardadas con los defaults (catálogo completo).

    process_permits/independent_outputs ausentes en `stored` se completan con
    una regla vacía (OR, sin términos = "sin efecto"), o con el default si
    corresponde. fault_rules ausente se completa con [] (sin fault rules).
    Claves desconocidas en `stored` se ignoran.
    """
    merged = copy.deepcopy(DEFAULT_RULES)
    stored = stored or {}

    permits = stored.get("process_permits") or {}
    for p in PROCESSES:
        if p in permits and isinstance(permits[p], dict):
            merged["process_permits"][p] = permits[p]
        elif p not in merged["process_permits"]:
            merged["process_permits"][p] = _default_rule("AND")

    outs = stored.get("independent_outputs") or {}
    for o in INDEPENDENT_OUTPUTS:
        if o in outs and isinstance(outs[o], dict):
            merged["independent_outputs"][o] = outs[o]
        elif o not in merged["independent_outputs"]:
            merged["independent_outputs"][o] = _default_rule("OR")

    if "fault_rules" in stored and isinstance(stored["fault_rules"], list):
        merged["fault_rules"] = stored["fault_rules"]

    return merged


def _validate_rule_config(entry) -> Optional[dict]:
    """Valida/normaliza una RuleConfig entrante. Devuelve None si es inválida."""
    if not isinstance(entry, dict):
        return None
    op = entry.get("op")
    if op not in ("AND", "OR"):
        return None
    terms_in = entry.get("terms")
    if not isinstance(terms_in, list) or len(terms_in) > RULE_MAX_TERMS:
        return None

    terms = []
    for t in terms_in:
        if not isinstance(t, dict):
            return None
        signal = t.get("signal")
        source = t.get("source", "input")
        if source not in ("input", "derived"):
            return None
        if source == "input" and signal not in LOGICAL_INPUTS:
            return None
        if source == "derived" and signal not in DERIVED_SIGNALS:
            return None
        terms.append({
            "signal": signal,
            "source": source,
            "negate": bool(t.get("negate")),
        })

    return {"op": op, "terms": terms}


def validate_rules(data: dict) -> dict:
    """Valida y normaliza un payload entrante (POST /api/rules).

    Entradas inválidas se descartan silenciosamente — el merge con
    DEFAULT_RULES en la siguiente lectura completa los huecos, y el firmware
    conserva el valor actual para ese slot (partial update). Nunca lanza
    excepción por datos inválidos.
    """
    out = {"process_permits": {}, "independent_outputs": {}, "fault_rules": []}

    permits = data.get("process_permits") or {}
    for p, entry in permits.items():
        if p not in PROCESSES:
            continue
        rule = _validate_rule_config(entry)
        if rule is not None:
            out["process_permits"][p] = rule

    outs = data.get("independent_outputs") or {}
    for o, entry in outs.items():
        if o not in INDEPENDENT_OUTPUTS:
            continue
        rule = _validate_rule_config(entry)
        if rule is not None:
            out["independent_outputs"][o] = rule

    faults = data.get("fault_rules")
    if isinstance(faults, list):
        for fr in faults[:FAULT_RULES_MAX]:
            if not isinstance(fr, dict):
                continue
            reason = fr.get("reason")
            if reason not in FAULT_REASONS:
                continue
            condition = _validate_rule_config(fr.get("condition"))
            if condition is None:
                continue
            delay_sec = fr.get("delay_sec", 0)
            if not isinstance(delay_sec, int) or delay_sec < 0:
                delay_sec = 0
            out["fault_rules"].append({
                "condition": condition,
                "reason": reason,
                "delay_sec": delay_sec,
            })

    return out
