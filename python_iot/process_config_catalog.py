"""
Catálogo de parámetros de proceso (ProcessConfig) — temporización configurable de la FSM.
Debe mantenerse sincronizado con firmware/Ro4.0-V1/src/control/process_config.h.
Defaults = valores exactos de los #define originales en config.h.
"""
import copy
from typing import Optional

DEFAULT_PROCESS_CONFIG = {
    "pressure_stabilization_delay_sec": 10,
    "startup_timeout_sec":               5,
    "retry_interval_sec":               10,
    "max_retries":                       5,
    "flush_duration_sec":               60,
}

PROCESS_CONFIG_LABELS = {
    "pressure_stabilization_delay_sec": "Estabilización baja→alta presión [s]",
    "startup_timeout_sec":              "Timeout verificación de presión [s]",
    "retry_interval_sec":               "Espera entre reintentos [s]",
    "max_retries":                      "Reintentos máx. antes de FAULT",
    "flush_duration_sec":               "Duración ciclo de flush [s]",
}

PROCESS_CONFIG_LIMITS = {
    "pressure_stabilization_delay_sec": (0, 300),
    "startup_timeout_sec":              (1, 300),
    "retry_interval_sec":               (0, 600),
    "max_retries":                      (1,  20),
    "flush_duration_sec":               (1, 600),
}


def merge_process_config(stored: Optional[dict]) -> dict:
    merged = copy.deepcopy(DEFAULT_PROCESS_CONFIG)
    for k, v in (stored or {}).items():
        if k in merged:
            merged[k] = v
    return merged


def validate_process_config(data: dict) -> tuple:
    out = {}
    warnings = []
    for field, (lo, hi) in PROCESS_CONFIG_LIMITS.items():
        raw = (data or {}).get(field, DEFAULT_PROCESS_CONFIG[field])
        val = int(raw) if isinstance(raw, (int, float)) else DEFAULT_PROCESS_CONFIG[field]
        val = max(lo, min(hi, val))
        out[field] = val
    # startup_timeout_sec > pressure_stabilization_delay_sec significa que la
    # verificación de presión se intenta ANTES de habilitar la bomba de alta
    # presión (el if de startHigh() aún no se cumple). Válido si el presostato
    # mide presión de membrana (la bomba baja ya eleva la presión), indeseable
    # si mide presión de alimentación (la bomba alta aún no arrancó).
    if out["startup_timeout_sec"] > out["pressure_stabilization_delay_sec"]:
        warnings.append(
            "startup_timeout_sec > pressure_stabilization_delay_sec: la verificación de "
            "presión ocurre antes de habilitar la bomba de alta presión. Válido para "
            "presostato de membrana; invierta los valores si el presostato mide presión "
            "de alimentación."
        )
    return out, warnings
