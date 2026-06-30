"""
Catálogo de protección anti-congelamiento (AntifreezeConfig) — opcional, DHT22.
Debe mantenerse sincronizado con firmware/Ro4.0-V1/src/safety/antifreeze.h.
Defaults = enabled/sensor_enabled=0 (opt-in) — sin impacto en equipos sin sensor.
"""
import copy
from typing import Optional

DEFAULT_ANTIFREEZE_CONFIG = {
    "enabled":                    0,
    "sensor_enabled":             0,
    "sensor_gpio":               21,
    "temp_threshold_low_c":      0.0,
    "temp_threshold_high_c":     3.0,
    "flush_duration_sec":        300,
    "eval_interval_sec":        3600,
    "boot_inhibit_sec":          120,
    "min_valid_temp_c":        -40.0,
    "max_valid_temp_c":         60.0,
    "max_consecutive_failures":   5,
}

ANTIFREEZE_LABELS = {
    "enabled":                    "Protección anti-congelamiento",
    "sensor_enabled":             "Sensor DHT22 habilitado",
    "sensor_gpio":                "GPIO del sensor",
    "temp_threshold_low_c":       "Umbral de riesgo [°C] (activa)",
    "temp_threshold_high_c":      "Umbral de recuperación [°C] (desactiva)",
    "flush_duration_sec":         "Duración del ciclo [s]",
    "eval_interval_sec":          "Intervalo entre evaluaciones [s]",
    "boot_inhibit_sec":           "Inhibición post-arranque [s]",
    "min_valid_temp_c":           "Temperatura válida mínima [°C]",
    "max_valid_temp_c":           "Temperatura válida máxima [°C]",
    "max_consecutive_failures":   "Fallos consecutivos antes de sensor_fault",
}

ANTIFREEZE_LIMITS = {
    "enabled":                    (0,     1),
    "sensor_enabled":             (0,     1),
    "sensor_gpio":                (0,    39),
    "temp_threshold_low_c":       (-40.0, 60.0),
    "temp_threshold_high_c":      (-40.0, 60.0),
    "flush_duration_sec":         (10,  3600),
    "eval_interval_sec":          (60, 86400),
    "boot_inhibit_sec":           (0,   3600),
    "min_valid_temp_c":           (-40.0, 80.0),
    "max_valid_temp_c":           (-40.0, 80.0),
    "max_consecutive_failures":   (1,    20),
}


def merge_antifreeze_config(stored: Optional[dict]) -> dict:
    merged = copy.deepcopy(DEFAULT_ANTIFREEZE_CONFIG)
    for k, v in (stored or {}).items():
        if k in merged:
            merged[k] = v
    return merged


def validate_antifreeze_config(data: dict) -> tuple:
    out = {}
    warnings = []
    for field, (lo, hi) in ANTIFREEZE_LIMITS.items():
        raw = (data or {}).get(field, DEFAULT_ANTIFREEZE_CONFIG[field])
        is_float = isinstance(DEFAULT_ANTIFREEZE_CONFIG[field], float)
        try:
            val = float(raw) if is_float else int(raw)
        except (TypeError, ValueError):
            val = DEFAULT_ANTIFREEZE_CONFIG[field]
        val = max(lo, min(hi, val))
        out[field] = val

    if out["temp_threshold_high_c"] <= out["temp_threshold_low_c"]:
        warnings.append(
            "temp_threshold_high_c debe ser mayor que temp_threshold_low_c — "
            "ajustado al default para evitar histéresis inválida."
        )
        out["temp_threshold_low_c"]  = DEFAULT_ANTIFREEZE_CONFIG["temp_threshold_low_c"]
        out["temp_threshold_high_c"] = DEFAULT_ANTIFREEZE_CONFIG["temp_threshold_high_c"]

    if out["eval_interval_sec"] <= out["flush_duration_sec"]:
        warnings.append(
            "eval_interval_sec debe ser mayor que flush_duration_sec (evita ciclos "
            "superpuestos) — ajustado al default."
        )
        out["flush_duration_sec"] = DEFAULT_ANTIFREEZE_CONFIG["flush_duration_sec"]
        out["eval_interval_sec"]  = DEFAULT_ANTIFREEZE_CONFIG["eval_interval_sec"]

    if out["max_valid_temp_c"] <= out["min_valid_temp_c"]:
        warnings.append(
            "max_valid_temp_c debe ser mayor que min_valid_temp_c — ajustado al default."
        )
        out["min_valid_temp_c"] = DEFAULT_ANTIFREEZE_CONFIG["min_valid_temp_c"]
        out["max_valid_temp_c"] = DEFAULT_ANTIFREEZE_CONFIG["max_valid_temp_c"]

    return out, warnings
