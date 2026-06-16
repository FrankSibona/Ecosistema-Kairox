"""
Catálogo de señales lógicas — capa de abstracción Pin <-> Señal.

Debe mantenerse sincronizado con
firmware/Ro4.0-V1/src/io/io_catalog.h (LogicalInput/LogicalOutput) y
firmware/Ro4.0-V1/src/io/io_map.cpp (defaultIOMap): mismos nombres de señal
(claves usadas en JSON/NVS/MQTT) y mismo mapeo por defecto. Append-only —
no renombrar ni eliminar claves existentes.
"""

import copy
from typing import Optional

# Orden informativo (coincide con LogicalInput en io_catalog.h).
LOGICAL_INPUTS = [
    "demand",
    "raw_water_available",
    "feed_tank_high",
    "feed_tank_low",
    "permeate_tank_high",
    "permeate_tank_low",
    "final_tank_high",
    "final_tank_low",
    "pressure_ok",
    "softener_regenerating",
    "well_low_level",
    "dosing_ok",
    "permeate_tank_demand",
    "final_tank_demand",
    "phase_failure",
]

# Orden informativo (coincide con DerivedSignal en io_catalog.h). No vienen
# de io_map/GPIO — se calculan cada loop desde el estado de un proceso.
DERIVED_SIGNALS = [
    "ro_producing",
]

# Orden informativo (coincide con LogicalOutput en io_catalog.h).
LOGICAL_OUTPUTS = [
    "low_pressure_pump",
    "high_pressure_pump",
    "well_pump",
    "transfer_pump",
    "flush_valve",
    "inlet_valve",
    "dosing_pump",
]

# Etiquetas para la UI (Flask admin panel).
INPUT_LABELS = {
    "demand":                "Demanda de agua",
    "raw_water_available":   "Agua cruda disponible",
    "feed_tank_high":        "Tanque alimentación: nivel alto",
    "feed_tank_low":         "Tanque alimentación: nivel bajo",
    "permeate_tank_high":    "Tanque permeado: nivel alto",
    "permeate_tank_low":     "Tanque permeado: nivel bajo",
    "final_tank_high":       "Tanque final: nivel alto",
    "final_tank_low":        "Tanque final: nivel bajo",
    "pressure_ok":           "Presostato OK",
    "softener_regenerating": "Ablandador regenerando",
    "well_low_level":        "Pozo: nivel bajo",
    "dosing_ok":             "Dosificación OK",
    "permeate_tank_demand":  "Tanque permeado: demanda (arranque RO)",
    "final_tank_demand":     "Tanque final: demanda (bomba transferencia)",
    "phase_failure":         "Falla de fase (protección RO)",
}

# Etiquetas para señales derivadas (no vienen de io_map/GPIO).
DERIVED_LABELS = {
    "ro_producing": "RO produciendo",
}

OUTPUT_LABELS = {
    "low_pressure_pump":  "Bomba baja presión",
    "high_pressure_pump": "Bomba alta presión",
    "well_pump":          "Bomba de pozo",
    "transfer_pump":      "Bomba de transferencia",
    "flush_valve":        "Válvula flush",
    "inlet_valve":        "Válvula entrada",
    "dosing_pump":        "Bomba dosificadora",
}

# Mapeo por defecto — reproduce EXACTAMENTE el wiring hardcodeado actual
# (ver firmware/Ro4.0-V1/include/config.h PIN_D1..D6 / PIN_R1..R6 y
# firmware/Ro4.0-V1/src/io/io_map.cpp defaultIOMap()). gpio=None == sin pin
# asignado (IOMAP_GPIO_NONE/0xFF en firmware).
#
# debounce_ms: debounce simétrico aplicado en Sensors::getSignal() (firmware
# IOMAP_VERSION v3). default_value: valor lógico devuelto si gpio=None ("señal
# no configurada"). demand/raw_water_available/pressure_ok usan
# debounce_ms=2000 (reproduce el debounce hoy hardcodeado en control.cpp);
# raw_water_available/pressure_ok usan default_value=1 (sin sensor -> se
# asume OK). Resto: debounce_ms=0, default_value=0.
DEFAULT_IO_MAP = {
    "inputs": {
        "demand":                {"gpio": 27,   "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 2000},
        "raw_water_available":   {"gpio": 26,   "mode": "pullup",   "invert": 0, "default_value": 1, "debounce_ms": 2000},
        "feed_tank_high":        {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "feed_tank_low":         {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "permeate_tank_high":    {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "permeate_tank_low":     {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "final_tank_high":       {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "final_tank_low":        {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "pressure_ok":           {"gpio": 33,   "mode": "pulldown", "invert": 0, "default_value": 1, "debounce_ms": 2000},
        "softener_regenerating": {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "well_low_level":        {"gpio": 32,   "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "dosing_ok":             {"gpio": 25,   "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "permeate_tank_demand":  {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "final_tank_demand":     {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
        "phase_failure":         {"gpio": None, "mode": "pullup",   "invert": 0, "default_value": 0, "debounce_ms": 0},
    },
    "outputs": {
        "low_pressure_pump":  {"gpio": 4,    "invert": 0},
        "high_pressure_pump": {"gpio": 16,   "invert": 0},
        "well_pump":          {"gpio": 17,   "invert": 0},
        "transfer_pump":      {"gpio": None, "invert": 0},
        "flush_valve":        {"gpio": 19,   "invert": 0},
        "inlet_valve":        {"gpio": 2,    "invert": 0},
        "dosing_pump":        {"gpio": 18,   "invert": 0},
    },
}

# Features configurables por dispositivo (booleanos). Sin impacto en FSM en
# esta fase — preparados para futuras variantes hidráulicas (ver perfiles).
DEFAULT_FEATURES = {
    "feature_well_pump":          False,
    "feature_transfer_pump":      False,
    "feature_softener_interlock": False,
    "feature_dosing":             False,
    "feature_delta_pressure":     False,
}

FEATURE_LABELS = {
    "feature_well_pump":          "Bomba de pozo",
    "feature_transfer_pump":      "Bomba de transferencia",
    "feature_softener_interlock": "Interlock ablandador",
    "feature_dosing":             "Dosificación",
    "feature_delta_pressure":     "ΔP membrana/rechazo",
}


def merge_io_map(stored: Optional[dict]) -> dict:
    """Combina el io_map guardado con los defaults (catálogo completo).

    Señales ausentes en `stored` se completan con DEFAULT_IO_MAP. Claves
    desconocidas en `stored` se ignoran (catálogo es la fuente de verdad).
    """
    merged = copy.deepcopy(DEFAULT_IO_MAP)
    stored = stored or {}
    for section in ("inputs", "outputs"):
        for name, entry in (stored.get(section) or {}).items():
            if name in merged[section] and isinstance(entry, dict):
                merged[section][name].update(entry)
    return merged


def merge_features(stored: Optional[dict]) -> dict:
    """Combina features guardados con los defaults (catálogo completo)."""
    merged = dict(DEFAULT_FEATURES)
    for k, v in (stored or {}).items():
        if k in merged:
            merged[k] = bool(v)
    return merged


def validate_io_map(data: dict) -> dict:
    """Valida y normaliza un io_map entrante (payload de /api/iomap POST).

    Señales con nombre desconocido o valores fuera de rango se descartan
    silenciosamente — el merge con DEFAULT_IO_MAP en la siguiente lectura
    completa los huecos, y el firmware conserva el valor actual para esa
    señal (partial update). Nunca lanza excepción por datos inválidos.

    "default_value"/"debounce_ms" ausentes en `entry` (ej. la UI de Mapeo de
    E/S no expone estos campos) caen al default del catálogo para esa señal
    — NO a 0/false — para no desarmar silenciosamente el debounce/safe-default
    de demand/raw_water_available/pressure_ok en cada guardado desde la UI.
    """
    out = {"inputs": {}, "outputs": {}}
    for name, entry in (data.get("inputs") or {}).items():
        if name not in LOGICAL_INPUTS or not isinstance(entry, dict):
            continue
        gpio = entry.get("gpio")
        if gpio is not None and not (isinstance(gpio, int) and 0 <= gpio <= 39):
            continue
        mode = entry.get("mode")
        if mode not in ("pullup", "pulldown"):
            mode = "pullup"
        catalog_defaults = DEFAULT_IO_MAP["inputs"][name]
        debounce_ms = entry.get("debounce_ms", catalog_defaults["debounce_ms"])
        if not (isinstance(debounce_ms, int) and 0 <= debounce_ms <= 60000):
            debounce_ms = catalog_defaults["debounce_ms"]
        default_value = entry.get("default_value", catalog_defaults["default_value"])
        out["inputs"][name] = {
            "gpio": gpio,
            "mode": mode,
            "invert": 1 if entry.get("invert") else 0,
            "default_value": 1 if default_value else 0,
            "debounce_ms": debounce_ms,
        }
    for name, entry in (data.get("outputs") or {}).items():
        if name not in LOGICAL_OUTPUTS or not isinstance(entry, dict):
            continue
        gpio = entry.get("gpio")
        if gpio is not None and not (isinstance(gpio, int) and 0 <= gpio <= 39):
            continue
        out["outputs"][name] = {
            "gpio": gpio,
            "invert": 1 if entry.get("invert") else 0,
        }
    return out


def validate_features(data: dict) -> dict:
    """Normaliza un dict de features al catálogo conocido (bool por clave)."""
    return {k: bool((data or {}).get(k, False)) for k in DEFAULT_FEATURES}
