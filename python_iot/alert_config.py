"""
alert_config.py — KAIROX alert rules configuration

Single source of truth for:
  - Operational thresholds (configurable via env vars)
  - Alert codes whitelist (only these codes create alerts/notifications)
  - Per-rule hysteresis: trigger_seconds / clear_seconds

trigger_seconds: condition must persist this long before the alert fires.
                 0 = fire immediately (safety-critical rules).
clear_seconds:   condition must be absent this long before the alert resolves.
"""

import os

# ── Operational thresholds ─────────────────────────────────────────────────────

THRESH_TDS_OUT_WARN           = float(os.getenv("THRESH_TDS_OUT_WARN",           "80.0"))   # ppm
THRESH_TDS_OUT_RESOLVE        = float(os.getenv("THRESH_TDS_OUT_RESOLVE",        "70.0"))   # ppm (hysteresis band)

THRESH_LOW_PRESSURE           = float(os.getenv("THRESH_LOW_PRESSURE",           "2.0"))    # bar (while PRODUCING)
THRESH_LOW_PRESSURE_RESOLVE   = float(os.getenv("THRESH_LOW_PRESSURE_RESOLVE",   "2.5"))    # bar

THRESH_HIGH_PRESSURE          = float(os.getenv("THRESH_HIGH_PRESSURE",          "9.0"))    # bar (absolute limit)
THRESH_HIGH_PRESSURE_RESOLVE  = float(os.getenv("THRESH_HIGH_PRESSURE_RESOLVE",  "8.5"))    # bar

THRESH_MIN_FLOW               = float(os.getenv("THRESH_MIN_FLOW",               "0.05"))   # L/min — minimum to be "flowing"
THRESH_NO_FLOW_SEC            = int(os.getenv("THRESH_NO_FLOW_SEC",             "30"))      # seconds of zero flow before NO_PERMEATE_FLOW

THRESH_LOW_EFFICIENCY         = float(os.getenv("THRESH_LOW_EFFICIENCY",         "0.85"))   # 0–1
THRESH_LOW_EFFICIENCY_RESOLVE = float(os.getenv("THRESH_LOW_EFFICIENCY_RESOLVE", "0.87"))   # 0–1

THRESH_OFFLINE_SEC            = int(os.getenv("THRESH_OFFLINE_SEC",             "90"))      # seconds without telemetry → DEVICE_OFFLINE
THRESH_OFFLINE_CHECK_SEC      = int(os.getenv("THRESH_OFFLINE_CHECK_SEC",       "30"))      # OfflineChecker loop interval

THRESH_REMINDER_SEC           = int(os.getenv("THRESH_REMINDER_SEC",            "3600"))    # re-notify interval while alert stays active

# Physically impossible ranges — used for SENSOR_INVALID detection
SENSOR_LIMITS = {
    "pressure_membrane_bar": (-0.1, 50.0),
    "pressure_brine_bar":    (-0.1, 50.0),
    "flow_perm_lpm":         (-0.1, 500.0),
    "flow_rechazo_lpm":      (-0.1, 500.0),
    "tds_in_ppm":            (-1.0, 5000.0),
    "tds_out_ppm":           (-1.0, 5000.0),
}

# ── Alert codes whitelist ──────────────────────────────────────────────────────
# Only codes in this set will create persistent alerts and trigger notifications.
# DiagnosticEngine may still produce other codes for health-display and Grafana —
# they just won't write to the alerts table.

ALERT_CODES = frozenset({
    "DEVICE_OFFLINE",
    "DEVICE_RECONNECTED",
    "HIGH_TDS_OUTPUT",
    "LOW_PRESSURE",
    "HIGH_PRESSURE",
    "NO_PERMEATE_FLOW",
    "LOW_EFFICIENCY",
    "SENSOR_INVALID",
})

# ── Per-rule hysteresis ────────────────────────────────────────────────────────

RULE_CONFIG = {
    "DEVICE_OFFLINE":   {"trigger_seconds": 0,   "clear_seconds": 30},
    "HIGH_TDS_OUTPUT":  {"trigger_seconds": 30,  "clear_seconds": 60},
    "LOW_PRESSURE":     {"trigger_seconds": 30,  "clear_seconds": 60},
    "HIGH_PRESSURE":    {"trigger_seconds": 0,   "clear_seconds": 30},
    "NO_PERMEATE_FLOW": {"trigger_seconds": 0,   "clear_seconds": 30},  # NoFlowTracker already debounces
    "LOW_EFFICIENCY":   {"trigger_seconds": 60,  "clear_seconds": 120},
    "SENSOR_INVALID":   {"trigger_seconds": 10,  "clear_seconds": 30},
}
_DEFAULT_RULE = {"trigger_seconds": 60, "clear_seconds": 120}


def get_rule(code: str) -> dict:
    return RULE_CONFIG.get(code, _DEFAULT_RULE)
