"""
Fyntek RO Backend - worker.py v3.3
====================================

NUEVO en v3.4 — Separación Telemetría vs KPIs:

  KPIEngine refactorizado en dos capas:

  read_physical()
    → Nueva capa de telemetría cruda (siempre activa)
    → Variables físicas medidas directamente del proceso:
       - caudal (permeado / rechazo)
       - presiones
       - volúmenes acumulados
    → Se calculan SIEMPRE, independientemente del estado operativo

  compute_kpis()
    → KPIs derivados (interpretación del proceso)
    → Solo se calculan cuando el sistema está en PRODUCING
    → Evita generar métricas sin contexto (ruido matemático)

IMPACTO FUNCIONAL:

  Telemetría física persistente
    → flow_permeate_lpm y pressure_membrane_bar ya NO desaparecen en IDLE
    → El dashboard refleja variables reales incluso fuera de producción
    → Mejora directa en credibilidad del sistema

  KPIs condicionados por operación
    → recovery, efficiency, cost_per_liter solo existen en producción
    → Se elimina ambigüedad en períodos sin flujo

  device_status más representativo
    → Siempre contiene la última lectura física válida
    → Independiente del estado del equipo

  Consistencia con el modelo físico
    → Se separa explícitamente:
        medición (realidad)
        vs
        interpretación (análisis)

NO_PERMEATE_FLOW (mejora implícita)
  → Sigue operando correctamente usando datos físicos directos
  → Detecta flujo nulo incluso fuera de métricas calculadas

ARQUITECTURA DE CAPAS (actualizada):

  Capa 0 → Telemetría física (SIEMPRE activa)
           → sensores, señales crudas, realidad del proceso

  Capa 1 → Eventos (instantáneos)
           → alertas inmediatas (ej: NO_PERMEATE_FLOW)

  Capa 2 → Diagnóstico (con histéresis)
           → problemas confirmados en el tiempo

  Capa 3 → Tendencias (informativas)
           → análisis de comportamiento (no alertan)

  Capa 4 → Negocio (cada 5 min)
           → impacto económico y operativo

CAMBIO CLAVE DE FILOSOFÍA:

  Antes:
    "El sistema muestra lo que el estado permite calcular"

  Ahora:
    "El sistema muestra lo que realmente está pasando,
     y calcula KPIs solo cuando tiene sentido hacerlo"

  → Esto elimina falsos vacíos de datos
  → y evita interpretaciones incorrectas del cliente

NUEVO en v3.3 — Métricas de negocio:

  BusinessMetricsEngine
    → Responde las 3 preguntas que le importan al cliente:
      1. ¿Estoy produciendo lo que debería?   → fulfillment_pct
      2. ¿Estoy desperdiciando agua/energía?  → waste_pct, waste_liters
      3. ¿Mi equipo se está degradando?       → degradation_pct, degradation_label

  RiskEngine
    → Riesgo operativo: LOW | MEDIUM | HIGH | CRITICAL
    → Basado en diagnóstico actual + tendencias, no en variables aisladas

  DegradationTracker
    → "Tu sistema perdió 8.3% de rendimiento en 7 días"
    → Compara eficiencia actual vs baseline o vs hace N días
    → Se calcula cada 5 minutos por dispositivo (no por mensaje)

  Staleness indicator
    → health_age_hours: cuántas horas tiene el último diagnóstico de salud
    → Evita vender certeza donde hay incertidumbre

ARQUITECTURA DE CAPAS:
  Capa 1 → Eventos (instantáneos)        → alerta inmediata
  Capa 2 → Diagnóstico (con histéresis)  → confirmado en 60s
  Capa 3 → Tendencias (informativas)     → nunca alertan
  Capa 4 → Negocio (cada 5 min)          → KPIs para cliente final
"""

import functools
import hmac
import json
import logging
import math
import os
import queue
import smtplib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import alert_config as acfg
import io_catalog
import rule_catalog
import process_config_catalog
import antifreeze_catalog
from collections import deque, defaultdict
from datetime import datetime, timezone, date, timedelta
from typing import Optional, Dict, List, Any

import paho.mqtt.client as mqtt
import psycopg2
import psycopg2.pool
import requests
from flask import Flask, request, jsonify, render_template_string

import ai_client as _ai

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/ro_backend.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ro_backend")

# ============================================================
# CONFIGURACIÓN
# ============================================================

DB_HOST = os.getenv("DB_HOST", "ro-postgres")
DB_NAME = os.getenv("DB_NAME", "iot_db")
DB_USER = os.getenv("DB_USER", "user")
DB_PASS = os.getenv("DB_PASS", "password")
DB_PORT = int(os.getenv("DB_PORT", "5432"))

MQTT_BROKER = os.getenv("MQTT_BROKER", "ro-mosquitto")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER   = os.getenv("MQTT_USER", "kairox")
MQTT_PASS   = os.getenv("MQTT_PASS", "admin0102")
MQTT_TOPIC  = "fyntek/#"

TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_ADMIN_CHAT = os.getenv("TELEGRAM_ADMIN_CHAT", "")

# ── Email (SMTP) ──────────────────────────────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO   = os.getenv("SMTP_TO",   "")   # comma-separated global alert recipients

# ── Command Engine ────────────────────────────────────────────────────────────
COMMAND_ALLOWED         = {"START", "STOP", "FLUSH", "RST"}
COMMAND_TIMEOUT_SEC     = int(os.getenv("COMMAND_TIMEOUT_SEC", "60"))

# ── AI Integration layer ──────────────────────────────────────────────────────
# AI_API_KEY: Bearer token for AI service — grants read context + issue commands.
# Empty string disables auth (internal/dev use only).
AI_API_KEY              = os.getenv("AI_API_KEY", "")

# ADMIN_API_KEY: Bearer token for human operators — grants AI Gate mode changes.
# The AI service MUST NOT have access to this key.
# This prevents the AI from modifying its own permission level.
# Empty string disables admin auth (dev mode — must be set in production).
ADMIN_API_KEY           = os.getenv("ADMIN_API_KEY", "")

# Minimum seconds between successive AI commands for the same device.
AI_COMMAND_COOLDOWN_SEC = int(os.getenv("AI_COMMAND_COOLDOWN_SEC", "10"))

# How many process telemetry rows to include in the context window.
AI_CONTEXT_WINDOW_ROWS  = int(os.getenv("AI_CONTEXT_WINDOW_ROWS", "10"))

# API version returned in every response.
API_VERSION             = "1"

# ── AI Decision Engine (backend-initiated calls to external AI) ───────────────
# Distinct from the existing AI Gate (/api/v1/*) which handles inbound AI calls.
# This engine proactively calls an external AI API for each device where
# ai_mode != 'OFF'. Empty AI_ENDPOINT_URL disables the engine entirely.
AI_ENDPOINT_URL      = os.getenv("AI_ENDPOINT_URL", "")
AI_POLL_INTERVAL_SEC = max(10,  int(os.getenv("AI_POLL_INTERVAL_SEC",  "60")))   # min 10s
AI_TIMEOUT_SEC       = max(1,   int(os.getenv("AI_TIMEOUT_SEC",        "10")))   # min 1s
AI_AUTO_COOLDOWN_SEC = max(0,   int(os.getenv("AI_AUTO_COOLDOWN_SEC",  "300")))  # 0 = no cooldown
# Telemetry window sent to AI on each poll cycle
AI_WINDOW_SECONDS    = max(10,  int(os.getenv("AI_WINDOW_SECONDS",     "60")))   # window depth
AI_SAMPLE_PERIOD_SEC = max(1,   int(os.getenv("AI_SAMPLE_PERIOD_SEC",  "1")))    # seconds between samples
AI_WINDOW_MAX_SAMPLES= max(1,   int(os.getenv("AI_WINDOW_MAX_SAMPLES", "120")))  # hard cap on samples
# Realtime mode — per-sample push to a separate endpoint
AI_REALTIME_ENDPOINT_URL    = os.getenv("AI_REALTIME_ENDPOINT_URL", "")
AI_REALTIME_TIMEOUT_SEC     = max(1, int(os.getenv("AI_REALTIME_TIMEOUT_SEC",     "2")))
AI_REALTIME_CONTEXT_SECONDS = max(0, int(os.getenv("AI_REALTIME_CONTEXT_SECONDS", "0")))
AI_REALTIME_API_TOKEN       = os.getenv("AI_REALTIME_API_TOKEN", "")
AI_API_TOKEN                = os.getenv("AI_API_TOKEN", "")

# ── Admin Panel ───────────────────────────────────────────────────────────────
# HTTP Basic Auth for /admin/* routes.
# Empty ADMIN_PANEL_USER disables auth (dev mode only — do NOT leave empty in prod).
ADMIN_PANEL_USER = os.getenv("ADMIN_PANEL_USER", "")
ADMIN_PANEL_PASS = os.getenv("ADMIN_PANEL_PASS", "")

# ── AI Control Gate ───────────────────────────────────────────────────────────
# Controls what the AI service is allowed to do.
#
#   OBSERVE_ONLY — read context allowed, commands blocked.
#                  Normal mode for monitoring without actuation.
#   AUTO_EXECUTE — read and commands allowed. Full AI autonomy.
#   LOCKDOWN     — read and commands both blocked. Emergency isolation.
#                  Human access via internal /api/* endpoints still works.
#
# Default is OBSERVE_ONLY — safe on every startup.
AI_GATE_MODES        = {"OBSERVE_ONLY", "AUTO_EXECUTE", "LOCKDOWN"}
AI_GATE_DEFAULT_MODE = os.getenv("AI_GATE_DEFAULT_MODE", "OBSERVE_ONLY")

THRESHOLDS = {
    # Alert thresholds — authoritative values come from alert_config (single source of truth)
    "pressure_max_bar":           acfg.THRESH_HIGH_PRESSURE,
    "pressure_low_bar":           acfg.THRESH_LOW_PRESSURE,
    "efficiency_warning":         acfg.THRESH_LOW_EFFICIENCY,
    "efficiency_critical":        float(os.getenv("THRESH_EFF_CRITICAL",   "0.70")),
    "recovery_min":               float(os.getenv("THRESH_RECOVERY_MIN",   "0.25")),
    "recovery_max":               float(os.getenv("THRESH_RECOVERY_MAX",   "0.85")),
    "flow_permeate_min_lpm":          acfg.THRESH_MIN_FLOW,
    "alert_cooldown_sec":         int(os.getenv("ALERT_COOLDOWN",          "300")),
    "trend_window":               int(os.getenv("TREND_WINDOW",            "30")),
    "pressure_trend_threshold":   float(os.getenv("TREND_PRESSURE",        "0.02")),
    "efficiency_trend_threshold": float(os.getenv("TREND_EFFICIENCY",      "-0.005")),
    "hysteresis_confirm_sec":     int(os.getenv("HYSTERESIS_CONFIRM",      "60")),
    "hysteresis_clear_sec":       int(os.getenv("HYSTERESIS_CLEAR",        "120")),
    "no_flow_timeout_sec":        acfg.THRESH_NO_FLOW_SEC,
    # Business metrics refresh
    "biz_refresh_sec":            int(os.getenv("BIZ_REFRESH",             "300")),
    "degradation_window_days":    int(os.getenv("DEGRADATION_DAYS",        "7")),
    "degradation_min_pct":        float(os.getenv("DEGRADATION_MIN_PCT",   "3.0")),
    # Alert system (reference alert_config)
    "tds_out_warn_ppm":           acfg.THRESH_TDS_OUT_WARN,
    "tds_out_resolve_ppm":        acfg.THRESH_TDS_OUT_RESOLVE,
    "recovery_resolve_pct":       float(os.getenv("THRESH_RECOVERY_RESOLVE", "0.28")),
    "alert_reminder_sec":         acfg.THRESH_REMINDER_SEC,
    "offline_check_interval_sec": acfg.THRESH_OFFLINE_CHECK_SEC,
}

BASELINE_FIELDS = [
    "efficiency_warn_low", "efficiency_crit_low",
    "recovery_warn_low",   "recovery_warn_high",
    "flow_permeate_warn_low",  "pressure_warn_high",
    "pressure_crit_high",  "delta_pressure_warn_high",
]

DIAG_SCORES = {
    "FAULT_NO_WATER":       95,
    "FAULT_SYSTEM":        100,
    "FAULT_START_PRESSURE": 100,
    "FAULT_LOW_FLOW":      100,
    "FAULT_RECOVERY_LOW":  100,
    "FAULT_RECOVERY_HIGH": 100,
    "NO_RAW_WATER":         90,
    "HIGH_PRESSURE":        90,
    "NO_PERMEATE_FLOW":     88,
    "CRITICAL_EFFICIENCY":  80,
    "MEMBRANE_DEGRADED":    75,
    "MEMBRANE_FOULING":     70,
    "MEMBRANE_SCALING":     65,
    "PROGRESSIVE_FOULING":  60,
    "LOW_RECOVERY":         45,
    "HIGH_TDS_OUTPUT":      55,
    "SENSOR_INVALID":       50,
    "LOW_EFFICIENCY":       40,
    "LOW_PERMEATE_FLOW":    35,
    "LOW_PRESSURE":         35,
    "DECLINING_EFFICIENCY": 30,
    "RESIDUAL_FLOW_STOPPED": 15,
    "MEMBRANE_HIGH_PRESSURE_ALARM": 70,
    "DELTA_P_ALARM":               60,
    "BRINE_HIGH_PRESSURE_ALARM":    50,
}

IMMEDIATE_ALERT_CODES = {
    "FAULT_NO_WATER", "FAULT_SYSTEM",
    "NO_RAW_WATER", "HIGH_PRESSURE",
    "CRITICAL_EFFICIENCY", "NO_PERMEATE_FLOW",
}

ACTIVE_STATES  = {"PRODUCING", "STARTING"}
PASSIVE_STATES = {"IDLE", "STOPPING", "FLUSHING"}

# Pesos de riesgo por diagnóstico
RISK_WEIGHTS = {
    "FAULT_SYSTEM":         100,
    "FAULT_START_PRESSURE": 100,
    "FAULT_LOW_FLOW":       100,
    "FAULT_RECOVERY_LOW":   100,
    "FAULT_RECOVERY_HIGH":  100,
    "FAULT_NO_WATER":       95,
    "NO_RAW_WATER":         90,
    "HIGH_PRESSURE":        85,
    "NO_PERMEATE_FLOW":     80,
    "CRITICAL_EFFICIENCY":  75,
    "MEMBRANE_DEGRADED":    60,
    "MEMBRANE_FOULING":     55,
    "MEMBRANE_SCALING":     50,
    "LOW_EFFICIENCY":       30,
    "LOW_RECOVERY":         25,
    "LOW_PERMEATE_FLOW":    20,
    "LOW_PRESSURE":         20,
    "FOULING_PROGRESSIVE":  35,  # tendencia
    "DECLINING_EFFICIENCY": 25,  # tendencia
    "RESIDUAL_FLOW_STOPPED": 15,
    "MEMBRANE_HIGH_PRESSURE_ALARM": 55,
    "DELTA_P_ALARM":               45,
    "BRINE_HIGH_PRESSURE_ALARM":    35,
}

def map_severity_to_health(severity: str) -> str:
    return {"OK": "HEALTHY", "WARNING": "WARNING", "CRITICAL": "CRITICAL"}.get(severity, "UNKNOWN")

# ============================================================
# DATABASE POOL
# ============================================================

class DatabasePool:
    def __init__(self):
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._consecutive_errors: int = 0
        self._last_error_time: Optional[str] = None

    def connect(self):
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            host=DB_HOST, port=DB_PORT,
            database=DB_NAME, user=DB_USER, password=DB_PASS,
        )
        log.info("✅ DB pool conectado")

    def _on_db_success(self):
        self._consecutive_errors = 0

    def _on_db_error(self, context: str):
        self._consecutive_errors += 1
        self._last_error_time = datetime.now(timezone.utc).isoformat()
        n = self._consecutive_errors
        if n == 3:
            log.critical(f"[DB] {n} errores consecutivos — posible caída de PostgreSQL")
        if n >= 10 and n % 10 == 0:
            log.critical(f"[DB] {n} errores consecutivos — telemetría se está PERDIENDO")
        if n >= 3:
            try:
                if mqtt_client and mqtt_client.is_connected():
                    import json as _j
                    mqtt_client.publish("fyntek/system/alerts", _j.dumps({
                        "type": "DB_UNAVAILABLE", "errors": n,
                        "since": self._last_error_time,
                    }))
            except Exception:
                pass

    @property
    def health(self) -> dict:
        return {
            "status": "ok" if self._consecutive_errors == 0 else "error",
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error_time,
        }

    def execute(self, sql: str, params: tuple = ()):
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            self._on_db_success()
        except Exception as e:
            conn.rollback()
            log.error(f"DB error: {e} | SQL: {sql[:80]}")
            self._on_db_error("execute")
        finally:
            self._pool.putconn(conn)

    def fetchall(self, sql: str, params: tuple = ()):
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = cur.fetchall()
            self._on_db_success()
            return result
        except Exception as e:
            log.error(f"DB fetch error: {e}")
            self._on_db_error("fetchall")
            return []
        finally:
            self._pool.putconn(conn)

    def insert_returning(self, sql: str, params: tuple = ()):
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
            self._on_db_success()
            return row[0] if row else None
        except Exception as e:
            conn.rollback()
            log.error(f"DB insert_returning error: {e}")
            self._on_db_error("insert_returning")
            return None
        finally:
            self._pool.putconn(conn)


db = DatabasePool()
_BACKEND_START_TIME = time.time()

# ============================================================
# UTILIDADES
# ============================================================

STATE_MAP = {
    "IDLE": 0, "STARTING": 1, "PRODUCING": 2,
    "FLUSHING": 3, "STOPPING": 4, "FAULT": 5,
}

def ts_to_utc(ts_unix: Any) -> Optional[datetime]:
    try:
        val = int(ts_unix)
        if val <= 0:
            return None
        return datetime.fromtimestamp(val, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

def validate_float(value: Any, vmin: float = -1000, vmax: float = 1000) -> Optional[float]:
    try:
        v = float(value)
        return v if vmin <= v <= vmax else None
    except (TypeError, ValueError):
        return None

def validate_bool(value: Any) -> Optional[bool]:
    try:
        return value if isinstance(value, bool) else bool(int(value))
    except (TypeError, ValueError):
        return None

# ============================================================
# BASELINE CACHE
# ============================================================

class BaselineCache:
    _FALLBACK = {
        "efficiency_warn_low":      "efficiency_warning",
        "efficiency_crit_low":      "efficiency_critical",
        "recovery_warn_low":        "recovery_min",
        "recovery_warn_high":       "recovery_max",
        "flow_permeate_warn_low":       "flow_permeate_min_lpm",
        "pressure_warn_high":       "pressure_max_bar",
        "pressure_crit_high":       "pressure_max_bar",
        "delta_pressure_warn_high": "pressure_max_bar",
    }
    _cache: Dict[str, Dict] = {}

    @classmethod
    def get(cls, device_id: str) -> Dict:
        cached = cls._cache.get(device_id, {})
        if cached.get("_ts", 0) > time.time() - 600:
            return cached["data"]

        cols = []
        for field in BASELINE_FIELDS:
            cols += [f"{field}_learned", f"{field}_manual", f"{field}_source"]

        rows = db.fetchall(
            f"SELECT {', '.join(cols)} FROM device_baseline WHERE device_id = %s",
            (device_id,)
        )
        resolved = {}
        if rows:
            row = rows[0]
            for i, field in enumerate(BASELINE_FIELDS):
                learned = row[i * 3]
                manual  = row[i * 3 + 1]
                source  = row[i * 3 + 2] or "learned"
                if source == "manual" and manual is not None:
                    resolved[field] = manual
                elif learned is not None:
                    resolved[field] = learned
                else:
                    resolved[field] = THRESHOLDS[cls._FALLBACK[field]]
        else:
            for field in BASELINE_FIELDS:
                resolved[field] = THRESHOLDS[cls._FALLBACK[field]]

        cls._cache[device_id] = {"data": resolved, "_ts": time.time()}
        return resolved

    @classmethod
    def get_efficiency_baseline(cls, device_id: str) -> Optional[float]:
        """Retorna la eficiencia media aprendida, o None si no hay baseline."""
        rows = db.fetchall(
            "SELECT efficiency_mean FROM device_baseline WHERE device_id = %s",
            (device_id,)
        )
        return rows[0][0] if rows and rows[0][0] else None

    @classmethod
    def invalidate(cls, device_id: str):
        cls._cache.pop(device_id, None)

# ============================================================
# KPI ENGINE
# ============================================================

class KPIEngine:
    _last_quality:  Dict[str, Dict] = {}
    _device_config: Dict[str, Dict] = {}

    @classmethod
    def update_quality_cache(cls, device_id: str, quality_data: Dict):
        cls._last_quality[device_id] = quality_data

    @classmethod
    def _get_config(cls, device_id: str) -> Dict:
        cached = cls._device_config.get(device_id, {})
        if cached.get("_ts", 0) > time.time() - 300:
            return cached
        rows = db.fetchall(
            "SELECT pump_power_kw, cost_kwh, cost_water_m3, daily_target_liters, "
            "pressure_membrane_high_limit, pressure_brine_high_limit, "
            "pressure_brine_alarm_enabled, delta_p_alarm_enabled, delta_p_alarm_limit "
            "FROM device_config WHERE device_id = %s",
            (device_id,)
        )
        config = {
            "pump_power_kw":       rows[0][0] if rows else 0.75,
            "cost_kwh":            rows[0][1] if rows else 0.12,
            "cost_water_m3":       rows[0][2] if rows else 0.80,
            "daily_target_liters": rows[0][3] if rows else 0.0,
            "pressure_membrane_high_limit": rows[0][4] if rows else 12.0,
            "pressure_brine_high_limit":    rows[0][5] if rows else 8.0,
            "pressure_brine_alarm_enabled": rows[0][6] if rows else False,
            "delta_p_alarm_enabled":        rows[0][7] if rows else False,
            "delta_p_alarm_limit":          rows[0][8] if rows else 5.0,
            "_ts":                 time.time(),
        }
        cls._device_config[device_id] = config
        return config

    @classmethod
    def invalidate_config(cls, device_id: str):
        cls._device_config.pop(device_id, None)

    @classmethod
    def read_physical(cls, process: Dict) -> Dict:
        """
        Extrae variables físicas medidas.
        Se llama SIEMPRE, independientemente del estado operativo.
        Estas son telemetría, no interpretación.
        """
        return {
            "flow_permeate_lpm":        validate_float(process.get("flow_permeate_lpm"),          0, 100),
            "flow_reject_lpm":     validate_float(process.get("flow_reject_lpm"),        0, 100),
            "pressure_membrane_bar": validate_float(process.get("pressure_membrane_bar"),  0, 50),
            "pressure_brine_bar":   validate_float(process.get("pressure_brine_bar"),      0, 50),
            "pressure_membrane_voltage": validate_float(process.get("pressure_membrane_voltage"), 0, 15),
            "pressure_brine_voltage":    validate_float(process.get("pressure_brine_voltage"),    0, 15),
            "delta_p_bar":          validate_float(process.get("delta_p_bar"),            -50, 50),
            "volume_permeate_l":        validate_float(process.get("volume_permeate_l"),           0, 1e7),
            "volume_reject_l":     validate_float(process.get("volume_reject_l"),        0, 1e7),
        }

    @classmethod
    def compute_kpis(cls, device_id: str, physical: Dict, state: str) -> Optional[Dict]:
        """
        Calcula KPIs derivados.
        Solo tiene sentido cuando el equipo está produciendo.
        Fuera de PRODUCING los ratios son ruido matemático.
        """
        if state not in ACTIVE_STATES:
            return None

        flow_p  = physical.get("flow_permeate_lpm")
        flow_r  = physical.get("flow_reject_lpm")
        p_mem   = physical.get("pressure_membrane_bar")
        p_brine = physical.get("pressure_brine_bar")

        if flow_p is None or flow_r is None:
            return None

        total_flow      = flow_p + flow_r
        recovery        = (flow_p / total_flow) if total_flow > 0.01 else None
        rejection_ratio = (flow_r / flow_p)     if flow_p > 0.01    else None
        delta_p         = (p_mem - p_brine)      if p_mem and p_brine else None

        quality    = cls._last_quality.get(device_id, {})
        tds_in     = validate_float(quality.get("tds_in_ppm"),  0, 5000)
        tds_out    = validate_float(quality.get("tds_out_ppm"), 0, 5000)
        efficiency = None
        if tds_in and tds_in > 10.0:  # minimum 10 ppm for meaningful rejection ratio
            efficiency = 1.0 - (tds_out / tds_in) if tds_out is not None else None

        config         = cls._get_config(device_id)
        cost_per_liter = None
        if flow_p and flow_p > 0.01 and total_flow > 0.01:
            cost_energy    = config["pump_power_kw"] * (config["cost_kwh"] / 60)
            cost_water     = (total_flow / 1000) * config["cost_water_m3"] * (1000 / 60)
            cost_per_liter = (cost_energy + cost_water) / flow_p

        return {
            "recovery":           recovery,
            "efficiency":         efficiency,
            "rejection_ratio":    rejection_ratio,
            "delta_pressure_bar": delta_p,
            "flow_permeate_lpm":      flow_p,
            "flow_reject_lpm":   flow_r,
            "tds_in_ppm":         tds_in,
            "tds_out_ppm":        tds_out,
            "cost_per_liter":     cost_per_liter,
        }

# ============================================================
# TREND ANALYZER
# ============================================================

class TrendAnalyzer:
    def __init__(self, window: int = 30):
        self.window = window
        self._buffers: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: {
                "pressure":   deque(maxlen=window),
                "efficiency": deque(maxlen=window),
                "flow_perm":  deque(maxlen=window),
            }
        )

    def add_metrics(self, device_id: str, metrics: Dict):
        buf = self._buffers[device_id]
        if metrics.get("delta_pressure_bar") is not None:
            buf["pressure"].append(metrics["delta_pressure_bar"])
        if metrics.get("efficiency") is not None:
            buf["efficiency"].append(metrics["efficiency"])
        if metrics.get("flow_permeate_lpm") is not None:
            buf["flow_perm"].append(metrics["flow_permeate_lpm"])

    def slope(self, device_id: str, variable: str) -> Optional[float]:
        values = list(self._buffers[device_id][variable])
        n = len(values)
        if n < 5:
            return None
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0.0

    def get_trends(self, device_id: str) -> Dict:
        return {
            "pressure_slope":   self.slope(device_id, "pressure"),
            "efficiency_slope": self.slope(device_id, "efficiency"),
            "flow_slope":       self.slope(device_id, "flow_perm"),
        }


trend_analyzer = TrendAnalyzer(window=THRESHOLDS["trend_window"])

# ============================================================
# RISK ENGINE
# ============================================================

class RiskEngine:
    """
    Calcula el riesgo operativo como un score numérico [0-100]
    y lo traduce a: LOW | MEDIUM | HIGH | CRITICAL

    El riesgo combina:
      - Diagnóstico actual (peso por código)
      - Tendencias activas (peso por tipo)
      - Confianza del diagnóstico (amplifica o atenúa)
    """

    def compute(
        self,
        final_root:  Any,   # DiagnosticResult
        trend_diags: List[Dict],
        confidence:  float,
    ) -> Dict:
        score = 0.0

        # Peso base del diagnóstico principal
        diag_weight = RISK_WEIGHTS.get(final_root.code, 0)
        # La confianza amplifica: 100% confianza → peso completo
        score += diag_weight * confidence

        # Bonus por tendencias activas
        for trend in trend_diags:
            trend_weight = RISK_WEIGHTS.get(trend["code"], 0)
            score += trend_weight * 0.5  # las tendencias pesan la mitad

        score = min(100.0, score)

        if score >= 80:
            level = "CRITICAL"
        elif score >= 50:
            level = "HIGH"
        elif score >= 25:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {"level": level, "score": round(score, 1)}


risk_engine = RiskEngine()

# ============================================================
# DEGRADATION TRACKER
# ============================================================

class DegradationTracker:
    """
    Calcula la tendencia de degradación comparando la eficiencia
    actual contra la eficiencia de hace N días.

    Se ejecuta con rate-limiting (máx cada biz_refresh_sec por dispositivo)
    para no hacer queries pesadas a DB cada segundo.

    Retorna:
      degradation_pct:   porcentaje de pérdida (negativo = degradó)
      degradation_days:  ventana de días analizada
      degradation_label: texto legible para el cliente
    """

    def __init__(self):
        self._last_computed: Dict[str, float] = {}
        self._cache:         Dict[str, Dict]  = {}

    def should_compute(self, device_id: str) -> bool:
        refresh = THRESHOLDS["biz_refresh_sec"]
        return (time.time() - self._last_computed.get(device_id, 0)) >= refresh

    def compute(self, device_id: str, current_efficiency: Optional[float]) -> Dict:
        """
        Compara eficiencia actual con:
          1. Baseline aprendido (preferido)
          2. Promedio de hace N días en DB (fallback)
        """
        empty = {"degradation_pct": None, "degradation_days": None, "degradation_label": None}

        if current_efficiency is None:
            return empty

        days   = THRESHOLDS["degradation_window_days"]
        min_pct = THRESHOLDS["degradation_min_pct"]

        # Intentar comparar contra baseline aprendido
        baseline_eff = BaselineCache.get_efficiency_baseline(device_id)

        if baseline_eff and baseline_eff > 0:
            degradation_pct = ((current_efficiency - baseline_eff) / baseline_eff) * 100
            reference       = "baseline"
            reference_eff   = baseline_eff
        else:
            # Fallback: promedio de eficiencia de hace N días
            rows = db.fetchall(
                """
                SELECT AVG(efficiency)
                FROM metrics
                WHERE device_id = %s
                  AND time BETWEEN
                      (NOW() - INTERVAL '%s days' - INTERVAL '1 day')
                  AND (NOW() - INTERVAL '%s days')
                  AND efficiency IS NOT NULL
                """,
                (device_id, days, days - 1)
            )
            if not rows or rows[0][0] is None:
                return empty

            reference_eff   = rows[0][0]
            degradation_pct = ((current_efficiency - reference_eff) / reference_eff) * 100
            reference       = f"hace {days} días"

        self._last_computed[device_id] = time.time()

        # Solo reportar si la degradación supera el mínimo relevante
        if abs(degradation_pct) < min_pct:
            result = {
                "degradation_pct":   round(degradation_pct, 1),
                "degradation_days":  days,
                "degradation_label": f"Rendimiento estable (ref: {reference})",
            }
        elif degradation_pct < 0:
            result = {
                "degradation_pct":   round(degradation_pct, 1),
                "degradation_days":  days,
                "degradation_label": (
                    f"Pérdida de {abs(degradation_pct):.1f}% de rendimiento "
                    f"vs {reference}"
                ),
            }
        else:
            result = {
                "degradation_pct":   round(degradation_pct, 1),
                "degradation_days":  days,
                "degradation_label": (
                    f"Mejora de {degradation_pct:.1f}% vs {reference}"
                ),
            }

        self._cache[device_id] = result
        return result

    def get_cached(self, device_id: str) -> Dict:
        """Retorna el último resultado calculado sin ir a DB."""
        return self._cache.get(device_id, {
            "degradation_pct":   None,
            "degradation_days":  None,
            "degradation_label": None,
        })


degradation_tracker = DegradationTracker()

# ============================================================
# BUSINESS METRICS ENGINE
# ============================================================

class BusinessMetricsEngine:
    """
    Calcula los KPIs que responden las 3 preguntas del cliente:

      1. ¿Estoy produciendo lo que debería?
         → liters_today, target_liters, fulfillment_pct

      2. ¿Estoy desperdiciando agua/energía?
         → waste_liters_today, waste_pct

      3. ¿Mi equipo se está degradando?
         → degradation_pct, degradation_label

    Se ejecuta con rate limiting (biz_refresh_sec) para no
    sobrecargar la DB con queries cada segundo.

    Los resultados se guardan en device_status (tiempo real)
    y en business_metrics (histórico diario).
    """

    def __init__(self):
        self._last_run: Dict[str, float] = {}

    def should_run(self, device_id: str) -> bool:
        refresh = THRESHOLDS["biz_refresh_sec"]
        return (time.time() - self._last_run.get(device_id, 0)) >= refresh

    def compute(
        self,
        device_id:   str,
        timestamp:   datetime,
        metrics:     Optional[Dict],
        final_root:  Any,   # DiagnosticResult
        trend_diags: List[Dict],
    ) -> Dict:
        """Calcula y persiste todas las métricas de negocio."""

        config     = KPIEngine._get_config(device_id)
        tz_name    = 'America/Argentina/Buenos_Aires'

        # ── 1. PRODUCCIÓN DEL DÍA ────────────────────────────
        rows = db.fetchall(
            f"""
            SELECT
                MAX(volume_permeate_l)    - MIN(volume_permeate_l)    AS liters_produced,
                MAX(volume_reject_l) - MIN(volume_reject_l) AS liters_rejected
            FROM telemetry_process
            WHERE device_id = %s
              AND DATE(time AT TIME ZONE '{tz_name}') = CURRENT_DATE AT TIME ZONE '{tz_name}'
              AND volume_permeate_l IS NOT NULL
            """,
            (device_id,)
        )

        liters_today       = rows[0][0] if rows and rows[0][0] else 0.0
        waste_liters_today = rows[0][1] if rows and rows[0][1] else 0.0
        total_today        = (liters_today or 0) + (waste_liters_today or 0)

        # Cumplimiento vs objetivo
        target         = config.get("daily_target_liters", 0) or 0
        fulfillment_pct = None
        if target > 0:
            fulfillment_pct = round(min(100.0, (liters_today / target) * 100), 1)

        # % de agua desperdiciada
        waste_pct = None
        if total_today > 0:
            waste_pct = round((waste_liters_today / total_today) * 100, 1)

        # ── 2. RIESGO ─────────────────────────────────────────
        risk = risk_engine.compute(final_root, trend_diags, final_root.confidence)

        # ── 3. DEGRADACIÓN ────────────────────────────────────
        current_eff = metrics.get("efficiency") if metrics else None
        if degradation_tracker.should_compute(device_id):
            deg = degradation_tracker.compute(device_id, current_eff)
        else:
            deg = degradation_tracker.get_cached(device_id)

        # ── 4. FRESCURA DEL DIAGNÓSTICO ──────────────────────
        rows_h = db.fetchall(
            "SELECT health_updated_at FROM device_status WHERE device_id = %s",
            (device_id,)
        )
        health_age_hours = None
        if rows_h and rows_h[0][0]:
            delta = datetime.now(tz=timezone.utc) - rows_h[0][0]
            health_age_hours = round(delta.total_seconds() / 3600, 1)

        self._last_run[device_id] = time.time()

        result = {
            "liters_today":       round(liters_today, 1),
            "target_liters":      target,
            "fulfillment_pct":    fulfillment_pct,
            "waste_liters_today": round(waste_liters_today, 1),
            "waste_pct":          waste_pct,
            "risk_level":         risk["level"],
            "risk_score":         risk["score"],
            "degradation_pct":    deg.get("degradation_pct"),
            "degradation_days":   deg.get("degradation_days"),
            "degradation_label":  deg.get("degradation_label"),
            "health_age_hours":   health_age_hours,
        }

        # Guardar en business_metrics (historial diario, UPSERT)
        self._persist_daily(device_id, timestamp, result, metrics)

        return result

    def _persist_daily(
        self,
        device_id: str,
        timestamp: datetime,
        biz:       Dict,
        metrics:   Optional[Dict],
    ):
        """Persiste o actualiza el registro del día en business_metrics."""
        avg_eff = avg_rec = None
        if metrics:
            avg_eff = metrics.get("efficiency")
            avg_rec = metrics.get("recovery")

        db.execute(
            """
            INSERT INTO business_metrics
              (day, device_id, liters_produced, liters_rejected,
               daily_target_liters, fulfillment_pct,
               waste_pct, avg_efficiency, avg_recovery,
               risk_level, calculated_at)
            VALUES (
              CURRENT_DATE AT TIME ZONE 'America/Argentina/Buenos_Aires',
              %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (day, device_id) DO UPDATE SET
              liters_produced      = EXCLUDED.liters_produced,
              liters_rejected      = EXCLUDED.liters_rejected,
              daily_target_liters  = EXCLUDED.daily_target_liters,
              fulfillment_pct      = EXCLUDED.fulfillment_pct,
              waste_pct            = EXCLUDED.waste_pct,
              avg_efficiency       = EXCLUDED.avg_efficiency,
              avg_recovery         = EXCLUDED.avg_recovery,
              risk_level           = EXCLUDED.risk_level,
              calculated_at        = NOW()
            """,
            (
                device_id,
                biz["liters_today"],       biz["waste_liters_today"],
                biz["target_liters"],      biz["fulfillment_pct"],
                biz["waste_pct"],          avg_eff,
                avg_rec,                   biz["risk_level"],
            )
        )


biz_engine = BusinessMetricsEngine()

# ============================================================
# DIAGNOSTIC RESULT
# ============================================================

class DiagnosticResult:
    def __init__(
        self,
        severity:   str,
        code:       str,
        message:    str,
        action:     str,
        evidence:   Dict,
        symptoms:   Dict  = None,
        score:      int   = 0,
        confidence: float = 1.0,
        is_event:   bool  = False,
    ):
        self.severity   = severity
        self.code       = code
        self.message    = message
        self.action     = action
        self.evidence   = evidence
        self.symptoms   = symptoms or {}
        self.score      = score
        self.confidence = confidence
        self.is_event   = is_event

    def __repr__(self):
        return f"[{self.severity}|score={self.score}|conf={self.confidence:.0%}] {self.code}"

    def to_dict(self):
        return {
            "severity":   self.severity,  "code":       self.code,
            "message":    self.message,   "action":     self.action,
            "evidence":   self.evidence,  "symptoms":   self.symptoms,
            "score":      self.score,     "confidence": round(self.confidence, 3),
            "is_event":   self.is_event,
        }


DIAG_OK = DiagnosticResult(
    severity="OK", code="NORMAL",
    message="Operando normalmente",
    action="Sin acción requerida.",
    evidence={}, score=0, confidence=1.0,
)

# ============================================================
# NO-FLOW TRACKER
# ============================================================

class NoFlowTracker:
    def __init__(self):
        self._no_flow_since: Dict[str, Optional[float]] = {}

    def update(self, device_id: str, state: str, flow_perm: Optional[float]) -> bool:
        if state not in ACTIVE_STATES:
            self._no_flow_since[device_id] = None
            return False

        has_flow = flow_perm is not None and flow_perm > 0.05
        if has_flow:
            self._no_flow_since[device_id] = None
            return False

        now = time.time()
        if self._no_flow_since.get(device_id) is None:
            self._no_flow_since[device_id] = now
            return False

        return (now - self._no_flow_since[device_id]) >= THRESHOLDS["no_flow_timeout_sec"]

    def get_duration(self, device_id: str) -> float:
        since = self._no_flow_since.get(device_id)
        return (time.time() - since) if since else 0.0


no_flow_tracker = NoFlowTracker()

# ============================================================
# HYSTERESIS MANAGER
# ============================================================

class HysteresisManager:
    """
    Per-code time-based hysteresis for slow diagnostic conditions.

    trigger_seconds: how long a condition must persist before it is "confirmed"
    clear_seconds:   how long a condition must be absent before it is cleared

    Both values come from alert_config.get_rule(code) — configurable per alert code.
    """

    def __init__(self):
        self._state: Dict[str, Dict[str, Dict]] = defaultdict(dict)

    def update(self, device_id: str, active_codes: List[str]) -> List[str]:
        now   = time.time()
        state = self._state[device_id]

        for code in active_codes:
            rule = acfg.get_rule(code)
            trigger = rule["trigger_seconds"]

            if code not in state:
                state[code] = {
                    "first_seen": now, "last_seen": now,
                    "confirmed": False, "cleared_since": None,
                }
            else:
                state[code]["last_seen"]     = now
                state[code]["cleared_since"] = None

            if not state[code]["confirmed"]:
                if now - state[code]["first_seen"] >= trigger:
                    state[code]["confirmed"] = True
                    log.info(f"[{device_id}] Diagnóstico confirmado: {code} (trigger={trigger}s)")

        to_delete = []
        for code, info in state.items():
            if code not in active_codes:
                clear = acfg.get_rule(code)["clear_seconds"]
                if info["cleared_since"] is None:
                    info["cleared_since"] = now
                elif now - info["cleared_since"] >= clear:
                    to_delete.append(code)
                    log.info(f"[{device_id}] Diagnóstico limpiado: {code} (clear={clear}s)")
        for code in to_delete:
            del state[code]

        return [code for code, info in state.items() if info["confirmed"]]

    def is_new_confirmation(self, device_id: str, code: str) -> bool:
        info = self._state[device_id].get(code)
        if not info or not info["confirmed"]:
            return False
        trigger = acfg.get_rule(code)["trigger_seconds"]
        return (time.time() - info["first_seen"]) < trigger + 5


hysteresis = HysteresisManager()

# ============================================================
# DIAGNOSTIC ENGINE
# ============================================================

class DiagnosticEngine:

    def run(self, device_id, process, metrics, state, inputs, trends) -> Dict:
        thresh    = BaselineCache.get(device_id)
        all_diags: List[DiagnosticResult] = []

        all_diags.extend(self._eval_events(state, inputs, process, device_id))
        if metrics:
            all_diags.extend(self._eval_operational(metrics, process, thresh, state))
        all_diags.extend(self._eval_contextual(state, process, device_id))
        sensor_diag = self._check_sensor_invalid(process, metrics)
        if sensor_diag:
            all_diags.append(sensor_diag)

        trend_diags = []
        if metrics and trends:
            trend_diags = self._eval_trends(metrics, trends)

        if not all_diags:
            return {"root_cause": DIAG_OK, "all_diags": [], "trend_diags": trend_diags}

        all_diags.sort(key=lambda d: d.score, reverse=True)
        root = all_diags[0]
        root.confidence = self._calc_confidence(root, all_diags, metrics or {})
        for other in all_diags[1:]:
            root.symptoms.update(other.evidence)

        return {"root_cause": root, "all_diags": all_diags, "trend_diags": trend_diags}

    def _calc_confidence(self, diag, all_diags, metrics) -> float:
        base            = 0.6
        evidence_bonus  = min(0.3, len(diag.evidence) * 0.1)
        coherence_bonus = 0.1 if len(all_diags) == 1 else (
            0.1 if (diag.score - all_diags[1].score) < 20 else 0.0
        )
        return min(1.0, base + evidence_bonus + coherence_bonus)

    def _eval_events(self, state, inputs, process, device_id) -> List[DiagnosticResult]:
        results = []

        if state == "FAULT":
            crudo  = (inputs or {}).get("raw_water_ok", True)
            retry  = tracker.get_retry_count(device_id)
            reason = tracker.get_fault_reason(device_id)

            if not crudo:
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_NO_WATER",
                    "FALLA: Sin agua de crudo. El sistema no puede arrancar.",
                    "Verificar suministro de agua cruda, flotante del tanque y válvula de entrada.",
                    {"state": state, "raw_water_ok": False, "retry": retry},
                    score=DIAG_SCORES["FAULT_NO_WATER"], is_event=True,
                ))
            elif reason == "MAX_RETRIES":
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_START_PRESSURE",
                    f"FALLA AL ARRANCAR: presión de membrana no alcanzada tras {retry} reintentos.",
                    "Verificar bomba de alta presión, válvulas de entrada/pressure switch y posibles obstrucciones.",
                    {"state": state, "retry": retry, "fault_reason": reason},
                    score=DIAG_SCORES["FAULT_START_PRESSURE"], is_event=True,
                ))
            elif reason == "FLOW_LOW":
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_LOW_FLOW",
                    "FALLA EN PRODUCCIÓN: caudal de permeado por debajo del mínimo.",
                    "Verificar bomba de alta presión, posible obstrucción/fuga en membrana y sensor de caudal.",
                    {"state": state, "retry": retry, "fault_reason": reason},
                    score=DIAG_SCORES["FAULT_LOW_FLOW"], is_event=True,
                ))
            elif reason == "RECOVERY_LOW":
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_RECOVERY_LOW",
                    "FALLA EN PRODUCCIÓN: recovery por debajo del mínimo.",
                    "Verificar fugas o derivación (bypass) en la membrana y caudal de rechazo.",
                    {"state": state, "retry": retry, "fault_reason": reason},
                    score=DIAG_SCORES["FAULT_RECOVERY_LOW"], is_event=True,
                ))
            elif reason == "RECOVERY_HIGH":
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_RECOVERY_HIGH",
                    "FALLA EN PRODUCCIÓN: recovery por encima del máximo.",
                    "Verificar válvula de rechazo (posiblemente muy cerrada) y obstrucciones en la línea de rechazo.",
                    {"state": state, "retry": retry, "fault_reason": reason},
                    score=DIAG_SCORES["FAULT_RECOVERY_HIGH"], is_event=True,
                ))
            else:
                results.append(DiagnosticResult(
                    "CRITICAL", "FAULT_SYSTEM",
                    f"FALLA DEL SISTEMA: retries agotados ({retry}).",
                    "Resetear equipo. Si persiste, inspeccionar bomba y pressure_switch.",
                    {"state": state, "retry": retry},
                    score=DIAG_SCORES["FAULT_SYSTEM"], is_event=True,
                ))

        elif inputs and not inputs.get("raw_water_ok", True) and state not in ("IDLE", "FLUSHING"):
            results.append(DiagnosticResult(
                "CRITICAL", "NO_RAW_WATER",
                "Sin agua de crudo mientras el equipo intenta operar.",
                "Verificar tanque de agua cruda y señal del flotante.",
                {"raw_water_ok": False, "state": state},
                score=DIAG_SCORES["NO_RAW_WATER"], is_event=True,
            ))

        flow_p = validate_float(process.get("flow_permeate_lpm"), 0, 100)
        if no_flow_tracker.update(device_id, state, flow_p):
            duration = no_flow_tracker.get_duration(device_id)
            p_mem    = validate_float(process.get("pressure_membrane_bar"), 0, 50)
            results.append(DiagnosticResult(
                "CRITICAL", "NO_PERMEATE_FLOW",
                f"Equipo en producción sin caudal de permeado por {duration:.0f}s.",
                "Verificar bomba de alta presión, válvula de permeado y membrana. "
                "Revisar si hay aire atrapado en el sistema.",
                {
                    "flow_permeate_lpm":    flow_p or 0,
                    "state":            state,
                    "duration_sec":     round(duration, 0),
                    "pressure_membrane_bar": p_mem,
                    "confidence_note":  "Verificar sensor de caudal antes de actuar",
                },
                score=DIAG_SCORES["NO_PERMEATE_FLOW"], is_event=True,
            ))

        return results

    def _eval_operational(self, metrics, process, thresh, state) -> List[DiagnosticResult]:
        results = []
        p_mem   = validate_float(process.get("pressure_membrane_bar"), 0, 50)
        flow_p  = metrics.get("flow_permeate_lpm")
        eff     = metrics.get("efficiency")
        rec     = metrics.get("recovery")
        delta_p = metrics.get("delta_pressure_bar")

        # Diagnósticos basados en eficiencia/recovery/TDS solo son significativos
        # con producción real y en régimen (PRODUCING). Con flujo ~0 (residual al
        # detenerse, o rampa de arranque en STARTING) estos valores son ruido.
        producing_steady = (
            state == "PRODUCING" and
            flow_p is not None and flow_p > THRESHOLDS["flow_permeate_min_lpm"]
        )

        if p_mem is not None and p_mem > thresh["pressure_crit_high"]:
            results.append(DiagnosticResult(
                "CRITICAL", "HIGH_PRESSURE",
                f"Presión crítica: {p_mem:.1f} bar (límite {thresh['pressure_crit_high']:.1f} bar).",
                "Detener equipo. Verificar válvula de rechazo y estado de membrana.",
                {"pressure_membrane_bar": p_mem},
                score=DIAG_SCORES["HIGH_PRESSURE"], is_event=True,
            ))

        if producing_steady and eff is not None and eff < thresh["efficiency_crit_low"]:
            results.append(DiagnosticResult(
                "CRITICAL", "CRITICAL_EFFICIENCY",
                f"Eficiencia crítica: {eff*100:.1f}% (límite {thresh['efficiency_crit_low']*100:.0f}%).",
                "Inspeccionar membrana. Posible rotura o fouling severo.",
                {"efficiency": eff},
                score=DIAG_SCORES["CRITICAL_EFFICIENCY"], is_event=True,
            ))

        if (producing_steady and
                p_mem is not None and p_mem > thresh["pressure_warn_high"] * 0.85 and
                flow_p is not None and flow_p < thresh["flow_permeate_warn_low"]):
            extra = {"delta_pressure_bar": delta_p} if delta_p and delta_p > 1.5 else {}
            results.append(DiagnosticResult(
                "WARNING", "MEMBRANE_FOULING",
                f"Ensuciamiento de membrana: presión alta ({p_mem:.1f} bar) con bajo caudal ({flow_p:.2f} L/min).",
                "Ejecutar ciclo de limpieza CIP. Si persiste, reemplazar membrana.",
                {"pressure_membrane_bar": p_mem, "flow_permeate_lpm": flow_p, **extra},
                score=DIAG_SCORES["MEMBRANE_FOULING"],
            ))

        if (producing_steady and
                eff is not None and eff < thresh["efficiency_warn_low"] and
                rec is not None and rec > thresh["recovery_warn_high"]):
            results.append(DiagnosticResult(
                "WARNING", "MEMBRANE_SCALING",
                f"Incrustación (scaling): eficiencia {eff*100:.1f}% con recovery alto {rec*100:.1f}%.",
                "Revisar dosis de antiscalante. Reducir recovery. Programar limpieza ácida.",
                {"efficiency": eff, "recovery": rec},
                score=DIAG_SCORES["MEMBRANE_SCALING"],
            ))

        if (producing_steady and
                eff is not None and eff < thresh["efficiency_warn_low"] and
                p_mem is not None and
                THRESHOLDS["pressure_low_bar"] < p_mem < thresh["pressure_warn_high"] * 0.8):
            results.append(DiagnosticResult(
                "WARNING", "MEMBRANE_DEGRADED",
                f"Degradación de membrana: eficiencia {eff*100:.1f}% con presión normal ({p_mem:.1f} bar).",
                "Evaluar reemplazo de membrana. Verificar cloro residual en agua de entrada.",
                {"efficiency": eff, "pressure_membrane_bar": p_mem},
                score=DIAG_SCORES["MEMBRANE_DEGRADED"],
            ))

        if producing_steady and rec is not None and rec < thresh["recovery_warn_low"]:
            results.append(DiagnosticResult(
                "WARNING", "LOW_RECOVERY",
                f"Recovery bajo: {rec*100:.1f}%.",
                "Ajustar válvula de rechazo. Verificar configuración de caudales.",
                {"recovery": rec},
                score=DIAG_SCORES["LOW_RECOVERY"],
            ))

        if producing_steady and eff is not None and eff < thresh["efficiency_warn_low"]:
            results.append(DiagnosticResult(
                "WARNING", "LOW_EFFICIENCY",
                f"Eficiencia baja: {eff*100:.1f}%.",
                "Revisión general: calidad de agua cruda, estado de membrana y pretratamiento.",
                {"efficiency": eff},
                score=DIAG_SCORES["LOW_EFFICIENCY"],
            ))

        if flow_p is not None and flow_p < thresh["flow_permeate_warn_low"]:
            results.append(DiagnosticResult(
                "WARNING", "LOW_PERMEATE_FLOW",
                f"Caudal de permeado bajo: {flow_p:.2f} L/min.",
                "Verificar presión de entrada y estado de membrana.",
                {"flow_permeate_lpm": flow_p},
                score=DIAG_SCORES["LOW_PERMEATE_FLOW"],
            ))

        if p_mem is not None and p_mem < THRESHOLDS["pressure_low_bar"]:
            results.append(DiagnosticResult(
                "WARNING", "LOW_PRESSURE",
                f"Presión de membrana baja: {p_mem:.1f} bar.",
                "Verificar bomba de alta presión y válvulas.",
                {"pressure_membrane_bar": p_mem},
                score=DIAG_SCORES["LOW_PRESSURE"],
            ))

        tds_out = metrics.get("tds_out_ppm")
        thr_tds = THRESHOLDS["tds_out_warn_ppm"]
        if producing_steady and tds_out is not None and tds_out > thr_tds:
            results.append(DiagnosticResult(
                "WARNING", "HIGH_TDS_OUTPUT",
                f"TDS salida elevado: {tds_out:.0f} ppm (umbral {thr_tds:.0f} ppm).",
                "Verificar integridad de la membrana. Posible rotura o bypass.",
                {"tds_out_ppm": tds_out, "threshold_ppm": thr_tds},
                score=DIAG_SCORES["HIGH_TDS_OUTPUT"],
            ))

        return results

    def _eval_contextual(self, state, process, device_id=None) -> List[DiagnosticResult]:
        """Diagnósticos que dependen del estado FSM y datos físicos crudos,
        independientes de KPIs derivados (metrics)."""
        results = []
        flow_reject = validate_float(process.get("flow_reject_lpm"), -0.1, 500.0)

        if (state in ("IDLE", "STOPPING") and flow_reject is not None and
                flow_reject > acfg.THRESH_RESIDUAL_FLOW_LPM):
            results.append(DiagnosticResult(
                "WARNING", "RESIDUAL_FLOW_STOPPED",
                f"Flujo de rechazo residual con equipo detenido: {flow_reject:.2f} L/min.",
                "Verificar fuga hidráulica o válvula de rechazo sin cerrar.",
                {"flow_reject_lpm": flow_reject, "state": state},
                score=DIAG_SCORES["RESIDUAL_FLOW_STOPPED"],
            ))

        # ── Alarmas de presión (diagnósticas, no detienen el equipo) ──────────
        # Evaluadas solo en STARTING/PRODUCING — fuera de ese rango las lecturas
        # de presión no son representativas (equipo despresurizado/transitorio).
        if state in ACTIVE_STATES and device_id:
            cfg     = KPIEngine._get_config(device_id)
            p_mem   = validate_float(process.get("pressure_membrane_bar"), 0, 50)
            p_brine = validate_float(process.get("pressure_brine_bar"),    0, 50)
            delta_p = validate_float(process.get("delta_p_bar"),         -50, 50)

            if p_mem is not None and p_mem > cfg["pressure_membrane_high_limit"]:
                results.append(DiagnosticResult(
                    "WARNING", "MEMBRANE_HIGH_PRESSURE_ALARM",
                    f"Presión de membrana elevada: {p_mem:.2f} bar > "
                    f"{cfg['pressure_membrane_high_limit']:.2f} bar (umbral).",
                    "Verificar válvula de rechazo, posible obstrucción o ensuciamiento de membrana.",
                    {"pressure_membrane_bar": p_mem,
                     "pressure_membrane_high_limit": cfg["pressure_membrane_high_limit"],
                     "state": state},
                    score=DIAG_SCORES["MEMBRANE_HIGH_PRESSURE_ALARM"],
                ))

            if (cfg["pressure_brine_alarm_enabled"] and p_brine is not None and
                    p_brine > cfg["pressure_brine_high_limit"]):
                results.append(DiagnosticResult(
                    "WARNING", "BRINE_HIGH_PRESSURE_ALARM",
                    f"Presión de rechazo elevada: {p_brine:.2f} bar > "
                    f"{cfg['pressure_brine_high_limit']:.2f} bar (umbral).",
                    "Verificar válvula de rechazo y obstrucciones en la línea de rechazo.",
                    {"pressure_brine_bar": p_brine,
                     "pressure_brine_high_limit": cfg["pressure_brine_high_limit"],
                     "state": state},
                    score=DIAG_SCORES["BRINE_HIGH_PRESSURE_ALARM"],
                ))

            if (cfg["delta_p_alarm_enabled"] and delta_p is not None and
                    delta_p > cfg["delta_p_alarm_limit"]):
                results.append(DiagnosticResult(
                    "WARNING", "DELTA_P_ALARM",
                    f"Delta de presión membrana-rechazo elevado: {delta_p:.2f} bar > "
                    f"{cfg['delta_p_alarm_limit']:.2f} bar (umbral).",
                    "Posible indicio de fouling/scaling de membrana — programar limpieza CIP.",
                    {"delta_p_bar": delta_p,
                     "delta_p_alarm_limit": cfg["delta_p_alarm_limit"],
                     "state": state},
                    score=DIAG_SCORES["DELTA_P_ALARM"],
                ))

        return results

    def _check_sensor_invalid(self, process: Dict, metrics: Optional[Dict]) -> Optional["DiagnosticResult"]:
        """Detect NaN/Inf or physically impossible sensor readings."""
        bad = []
        checks = {
            "pressure_membrane_bar": process.get("pressure_membrane_bar"),
            "pressure_brine_bar":    process.get("pressure_brine_bar"),
            "flow_permeate_lpm":         process.get("flow_permeate_lpm"),
            "flow_reject_lpm":      process.get("flow_reject_lpm"),
        }
        if metrics:
            checks["tds_in_ppm"]  = metrics.get("tds_in_ppm")
            checks["tds_out_ppm"] = metrics.get("tds_out_ppm")

        for key, val in checks.items():
            if val is None:
                continue
            lo, hi = acfg.SENSOR_LIMITS.get(key, (-1e9, 1e9))
            if math.isnan(val) or math.isinf(val):
                bad.append(f"{key}=NaN/Inf")
            elif val < lo or val > hi:
                bad.append(f"{key}={val:.2g} fuera de rango [{lo},{hi}]")

        if not bad:
            return None
        return DiagnosticResult(
            "WARNING", "SENSOR_INVALID",
            f"Lectura de sensor inválida: {', '.join(bad[:3])}.",
            "Verificar calibración y conexión de sensores.",
            {"invalid": bad},
            score=50,
        )

    def _eval_trends(self, metrics, trends) -> List[Dict]:
        trend_list = []
        p_slope   = trends.get("pressure_slope")
        eff_slope = trends.get("efficiency_slope")
        delta_p   = metrics.get("delta_pressure_bar")
        eff       = metrics.get("efficiency")

        if (p_slope is not None and
                p_slope > THRESHOLDS["pressure_trend_threshold"] and
                delta_p is not None and delta_p > 0.5):
            trend_list.append({
                "code":      "FOULING_PROGRESSIVE",
                "message":   f"Presión diferencial subiendo (+{p_slope:.4f} bar/muestra).",
                "direction": "up",
                "variable":  "delta_pressure_bar",
                "slope":     p_slope,
            })

        if (eff_slope is not None and
                eff_slope < THRESHOLDS["efficiency_trend_threshold"] and
                eff is not None):
            trend_list.append({
                "code":      "DECLINING_EFFICIENCY",
                "message":   f"Eficiencia en tendencia descendente ({eff_slope*100:.3f}%/muestra).",
                "direction": "down",
                "variable":  "efficiency",
                "slope":     eff_slope,
            })

        return trend_list


diagnostic_engine = DiagnosticEngine()

# ============================================================
# STATE TRACKER
# ============================================================

class DeviceStateTracker:
    def __init__(self):
        self._state:        Dict[str, str]          = defaultdict(lambda: "UNKNOWN")
        self._inputs:       Dict[str, Dict]          = {}
        self._outputs:      Dict[str, Dict]          = {}
        self._process:      Dict[str, Dict]          = {}
        self._fault_reason: Dict[str, Optional[str]] = {}
        self._retry_count:  Dict[str, int]           = {}

    def update_state(self, device_id, state, retry_count=0, fault_reason=None):
        self._state[device_id]        = state
        self._retry_count[device_id]  = retry_count
        self._fault_reason[device_id] = fault_reason if state == "FAULT" else None

    def update_inputs(self, device_id, data):    self._inputs[device_id]  = data
    def update_outputs(self, device_id, data):   self._outputs[device_id] = data
    def update_process(self, device_id, data):   self._process[device_id] = data

    def get_state(self, device_id):        return self._state.get(device_id, "UNKNOWN")
    def get_inputs(self, device_id):       return self._inputs.get(device_id)
    def get_outputs(self, device_id):      return self._outputs.get(device_id)
    def get_process(self, device_id):      return self._process.get(device_id)
    def get_fault_reason(self, device_id): return self._fault_reason.get(device_id)
    def get_retry_count(self, device_id):  return self._retry_count.get(device_id, 0)


tracker = DeviceStateTracker()

# ============================================================
# TELEGRAM WORKER
# ============================================================

class TelegramWorker:
    """Async Telegram sender — HTTP calls never run on the MQTT thread."""

    def __init__(self, maxsize: int = 50):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name="telegram-worker")
        t.start()
        log.info("✅ TelegramWorker iniciado")

    def send(self, token: str, chat_id: str, text: str, device_id: str, code: str):
        try:
            self._q.put_nowait({"token": token, "chat_id": chat_id, "text": text,
                                "device_id": device_id, "code": code})
        except queue.Full:
            log.warning(f"[ALERT] TELEGRAM QUEUE FULL — descartando {device_id} {code}")

    def _loop(self):
        while True:
            item = self._q.get()
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{item['token']}/sendMessage",
                    json={"chat_id": item["chat_id"], "text": item["text"]},
                    timeout=10,
                )
                if resp.ok:
                    log.info(f"[ALERT] TELEGRAM SENT — {item['device_id']} {item['code']}")
                else:
                    log.error(
                        f"[ALERT] TELEGRAM FAILED — {item['device_id']} {item['code']} "
                        f"HTTP {resp.status_code}: {resp.text[:80]}"
                    )
            except Exception as e:
                log.error(f"[ALERT] TELEGRAM FAILED — {item['device_id']} {item['code']} {e}")
            finally:
                self._q.task_done()


telegram_worker = TelegramWorker()


# ============================================================
# EMAIL WORKER
# ============================================================

class EmailWorker:
    """Async SMTP sender — runs on a dedicated daemon thread."""

    def __init__(self, maxsize: int = 50):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name="email-worker")
        t.start()
        log.info("✅ EmailWorker iniciado")

    def send(self, to_addrs: List[str], subject: str, body: str,
             device_id: str, code: str):
        try:
            self._q.put_nowait({
                "to": to_addrs, "subject": subject, "body": body,
                "device_id": device_id, "code": code,
            })
        except queue.Full:
            log.warning(f"[ALERT] EMAIL QUEUE FULL — descartando {device_id} {code}")

    def _loop(self):
        while True:
            item = self._q.get()
            try:
                self._send(item)
            except Exception as e:
                log.error(f"[ALERT] EMAIL FAILED — {item['device_id']} {item['code']} {e}")
            finally:
                self._q.task_done()

    def _send(self, item: dict):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = item["subject"]
        msg["From"]    = SMTP_FROM or SMTP_USER
        msg["To"]      = ", ".join(item["to"])
        msg.attach(MIMEText(item["body"], "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.ehlo()
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(msg["From"], item["to"], msg.as_string())

        log.info(f"[ALERT] EMAIL SENT — {item['device_id']} {item['code']} → {item['to']}")


email_worker = EmailWorker()


# ============================================================
# NOTIFIER
# ============================================================

class Notifier:
    """
    Channel-agnostic notification dispatcher.

    AlertManager calls notifier.send() without knowing Telegram or SMTP details.
    Supported channels: Telegram (per-device chat_id or global admin), Email (global SMTP_TO).
    """

    def __init__(self):
        self._chat_cache: Dict[str, Optional[str]] = {}
        self._name_cache: Dict[str, str] = {}

    def get_name(self, device_id: str) -> str:
        if device_id not in self._name_cache:
            rows = db.fetchall(
                "SELECT display_name FROM devices WHERE device_id = %s", (device_id,)
            )
            self._name_cache[device_id] = (rows[0][0] or device_id) if rows else device_id
        return self._name_cache[device_id]

    def _get_chat(self, device_id: str) -> Optional[str]:
        if device_id not in self._chat_cache:
            rows = db.fetchall(
                "SELECT telegram_chat_id FROM devices WHERE device_id = %s", (device_id,)
            )
            self._chat_cache[device_id] = (
                rows[0][0] if rows and rows[0][0] else TELEGRAM_ADMIN_CHAT
            )
        return self._chat_cache[device_id]

    def invalidate(self, device_id: str):
        self._chat_cache.pop(device_id, None)
        self._name_cache.pop(device_id, None)

    def send(self, device_id: str, code: str, severity: str, message: str):
        icon = {"CRITICAL": "🔴", "WARNING": "⚠", "INFO": "ℹ"}.get(severity, "•")
        name = self.get_name(device_id)
        body = f"{icon} {name}\n{code}\n\n{message}"

        # Telegram
        token   = TELEGRAM_TOKEN
        chat_id = self._get_chat(device_id)
        if token and chat_id:
            telegram_worker.send(token, chat_id, body, device_id, code)
        else:
            log.debug(f"[ALERT] Telegram no configurado para {device_id}")

        # Email
        if SMTP_HOST and SMTP_TO:
            recipients = [a.strip() for a in SMTP_TO.split(",") if a.strip()]
            if recipients:
                subject = f"[KAIROX] {code} — {name}"
                email_worker.send(recipients, subject, body, device_id, code)


notifier = Notifier()


# ============================================================
# ALERT MANAGER
# ============================================================

class AlertManager:
    """
    Persistent alert system backed by the alerts table.

    Deduplication: a partial unique index on (device_id, code) WHERE active=TRUE
    ensures only one active alert per (device_id, code) pair.

    Notification cooldown: immediate on first occurrence, then every
    THRESHOLDS["alert_reminder_sec"] while the condition persists.
    In-memory _last_notified survives within a process lifetime; the DB
    last_notified_at column survives restarts (used for display, not enforcement).
    """

    # Value-based resolve thresholds: condition(metrics) -> True means alert should clear
    RESOLVE_HYSTERESIS: Dict[str, Any] = {}  # populated after THRESHOLDS is defined

    def __init__(self):
        self._last_notified: Dict[str, float] = {}   # key: "device_id:code"
        self._active_codes:  Dict[str, set]   = defaultdict(set)  # device_id -> {code, ...}

    def invalidate_cache(self, device_id: str):
        notifier.invalidate(device_id)

    # ── Core operations ───────────────────────────────────────────────────────

    def fire(self, device_id: str, code: str, severity: str, message: str) -> bool:
        """
        Upsert an active alert. Returns True if this created a new alert row.
        Uses RETURNING (xmax = 0): True = INSERT (new), False = UPDATE (already active).
        insert_returning() commits the transaction; fetchall() does not.
        """
        result = db.insert_returning(
            """
            INSERT INTO alerts (device_id, code, severity, message, active)
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT (device_id, code) WHERE active = TRUE
            DO UPDATE SET
                severity   = EXCLUDED.severity,
                message    = EXCLUDED.message,
                updated_at = NOW()
            RETURNING (xmax = 0)
            """,
            (device_id, code, severity, message)
        )
        is_new = bool(result)
        self._active_codes[device_id].add(code)
        if is_new:
            log.info(f"[ALERT] NEW — {device_id} {code} [{severity}] {message}")
        return is_new

    def resolve(self, device_id: str, code: str):
        """Mark a single active alert as resolved."""
        db.execute(
            "UPDATE alerts SET active=FALSE, resolved_at=NOW(), updated_at=NOW() "
            "WHERE device_id=%s AND code=%s AND active=TRUE",
            (device_id, code)
        )
        self._active_codes[device_id].discard(code)
        log.info(f"[ALERT] RESOLVED — {device_id} {code}")

    def resolve_diagnostic_alerts(self, device_id: str):
        """Resolve all active WARNING/CRITICAL alerts for a device. Skips DEVICE_OFFLINE."""
        db.execute(
            "UPDATE alerts SET active=FALSE, resolved_at=NOW(), updated_at=NOW() "
            "WHERE device_id=%s AND active=TRUE "
            "AND severity IN ('WARNING','CRITICAL') AND code != 'DEVICE_OFFLINE'",
            (device_id,)
        )
        # Clear in-memory tracking for all non-OFFLINE codes
        codes = self._active_codes.get(device_id, set())
        removed = {c for c in codes if c != "DEVICE_OFFLINE"}
        for c in removed:
            log.info(f"[ALERT] RESOLVED — {device_id} {c}")
        self._active_codes[device_id] = codes - removed

    def resolve_all_active(self, device_id: str):
        """Resolve ALL active alerts for a device (incluido DEVICE_OFFLINE).
        Llamado en RST: el operador reconoce el estado y el equipo está online."""
        db.execute(
            "UPDATE alerts SET active=FALSE, resolved_at=NOW(), updated_at=NOW() "
            "WHERE device_id=%s AND active=TRUE",
            (device_id,)
        )
        codes = set(self._active_codes.get(device_id, set()))
        for c in codes:
            log.info(f"[ALERT] RESOLVED — {device_id} {c}")
        self._active_codes[device_id] = set()

    def fire_event(self, device_id: str, code: str, message: str, cooldown_sec: int = 300):
        """
        Persist a one-shot INFO event and notify once.
        INFO alerts are stored inactive (historical log, no persistent active state).
        """
        if code not in acfg.ALERT_CODES:
            return
        key = f"{device_id}:{code}"
        now = time.time()
        if (now - self._last_notified.get(key, 0)) < cooldown_sec:
            return
        db.execute(
            "INSERT INTO alerts (device_id, code, severity, message, active, notification_count) "
            "VALUES (%s, %s, 'INFO', %s, FALSE, 1)",
            (device_id, code, message)
        )
        notifier.send(device_id, code, "INFO", message)
        self._last_notified[key] = now

    def check_reconnection(self, device_id: str):
        """Called on any incoming message. Resolves DEVICE_OFFLINE and fires DEVICE_RECONNECTED."""
        rows = db.fetchall(
            "SELECT 1 FROM alerts WHERE device_id=%s AND code='DEVICE_OFFLINE' AND active=TRUE LIMIT 1",
            (device_id,)
        )
        if rows:
            self.resolve(device_id, "DEVICE_OFFLINE")
            name = notifier.get_name(device_id)
            self.fire_event(device_id, "DEVICE_RECONNECTED",
                            f"{name} reconectado.", cooldown_sec=300)
            log.info(f"[ALERT] {device_id} reconectado — DEVICE_OFFLINE resuelto")

    def fire_and_notify(self, device_id: str, code: str, severity: str, message: str):
        """Fire an alert and notify if new or reminder cooldown has elapsed.
        Only codes in acfg.ALERT_CODES produce persistent alerts and notifications."""
        if code not in acfg.ALERT_CODES:
            return
        key = f"{device_id}:{code}"
        now = time.time()
        is_new = self.fire(device_id, code, severity, message)
        if is_new or (now - self._last_notified.get(key, 0)) >= acfg.THRESH_REMINDER_SEC:
            notifier.send(device_id, code, severity, message)
            self._last_notified[key] = now
            db.execute(
                "UPDATE alerts SET last_notified_at=NOW(), "
                "notification_count = notification_count + 1 "
                "WHERE device_id=%s AND code=%s AND active=TRUE",
                (device_id, code)
            )

    # ── Diagnostic pipeline hook ──────────────────────────────────────────────

    def process(self, device_id: str, root: DiagnosticResult, is_new: bool, biz: Dict,
                metrics: Optional[Dict] = None):
        """Called from _run_analytics after each diagnostic cycle."""
        m = metrics or {}

        if root.severity == "OK":
            self.resolve_diagnostic_alerts(device_id)
            return

        # Value-based hysteresis: if the alert is already active, check whether
        # the condition has cleared its resolve threshold before re-firing.
        active = self._active_codes.get(device_id, set())
        if root.code in active:
            resolver = self.RESOLVE_HYSTERESIS.get(root.code)
            if resolver and resolver(m):
                self.resolve(device_id, root.code)
                return  # condition below resolve threshold — don't re-fire

        self.fire_and_notify(device_id, root.code, root.severity, root.message)


alert_manager = AlertManager()

# Value-based resolve thresholds — hysteresis band between activate and resolve
AlertManager.RESOLVE_HYSTERESIS = {
    "HIGH_TDS_OUTPUT": lambda m: (
        m.get("tds_out_ppm") is not None
        and m["tds_out_ppm"] < acfg.THRESH_TDS_OUT_RESOLVE
    ),
    "LOW_PRESSURE": lambda m: (
        m.get("pressure_membrane_bar") is not None
        and m["pressure_membrane_bar"] > acfg.THRESH_LOW_PRESSURE_RESOLVE
    ),
    "HIGH_PRESSURE": lambda m: (
        m.get("pressure_membrane_bar") is not None
        and m["pressure_membrane_bar"] < acfg.THRESH_HIGH_PRESSURE_RESOLVE
    ),
    "LOW_EFFICIENCY": lambda m: (
        m.get("efficiency") is not None
        and m["efficiency"] > acfg.THRESH_LOW_EFFICIENCY_RESOLVE
    ),
}


# ============================================================
# OFFLINE CHECKER
# ============================================================

class OfflineChecker:
    """Background thread: fires DEVICE_OFFLINE alerts for devices silent > THRESH_OFFLINE_SEC."""

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name="offline-check")
        t.start()
        log.info(f"✅ OfflineChecker iniciado (threshold={acfg.THRESH_OFFLINE_SEC}s)")

    def _loop(self):
        while True:
            try:
                self._check()
            except Exception as e:
                log.error(f"[OfflineChecker] {e}")
            time.sleep(acfg.THRESH_OFFLINE_CHECK_SEC)

    def _check(self):
        now = datetime.now(timezone.utc)
        rows = db.fetchall(
            """
            SELECT d.device_id, COALESCE(d.display_name, d.device_id), ds.last_seen
            FROM devices d
            JOIN device_status ds ON d.device_id = ds.device_id
            WHERE ds.last_seen < NOW() - MAKE_INTERVAL(secs => %s)
            """,
            (acfg.THRESH_OFFLINE_SEC,)
        )
        for device_id, name, last_seen in rows:
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            secs = int((now - last_seen).total_seconds())
            msg  = f"{name} sin conexión ({secs}s sin telemetría)."
            alert_manager.fire_and_notify(device_id, "DEVICE_OFFLINE", "CRITICAL", msg)


offline_checker = OfflineChecker()

# ============================================================
# LEARN ENGINE
# ============================================================

class LearnEngine:
    def __init__(self):
        self._active: Dict[str, Dict] = {}

    def start(self, device_id: str, duration_minutes: int = 30) -> Optional[int]:
        if device_id in self._active:
            self.cancel(device_id)
        session_id = db.insert_returning(
            "INSERT INTO learning_sessions (device_id, started_at, duration_min) "
            "VALUES (%s, NOW(), %s) RETURNING id",
            (device_id, duration_minutes)
        )
        if not session_id:
            return None
        self._active[device_id] = {
            "session_id":   session_id,
            "started_at":   time.time(),
            "duration_sec": duration_minutes * 60,
            "samples":      [],
        }
        log.info(f"[{device_id}] Learn iniciado — {duration_minutes} min")
        return session_id

    def is_active(self, device_id: str) -> bool:
        return device_id in self._active

    def cancel(self, device_id: str):
        session = self._active.pop(device_id, None)
        if session:
            db.execute(
                "UPDATE learning_sessions SET status='CANCELLED', finished_at=NOW() WHERE id=%s",
                (session["session_id"],)
            )

    def add_sample(self, device_id: str, metrics: Dict):
        if device_id not in self._active:
            return
        session = self._active[device_id]
        session["samples"].append({k: v for k, v in metrics.items() if v is not None})
        if len(session["samples"]) % 50 == 0:
            db.execute(
                "UPDATE learning_sessions SET samples=%s WHERE id=%s",
                (len(session["samples"]), session["session_id"])
            )
        if time.time() - session["started_at"] >= session["duration_sec"]:
            self._finish(device_id)

    def _finish(self, device_id: str):
        session = self._active.pop(device_id)
        samples = session["samples"]
        n       = len(samples)

        if n < 10:
            log.warning(f"[{device_id}] Learn cancelado: pocas muestras ({n})")
            db.execute(
                "UPDATE learning_sessions SET status='CANCELLED', finished_at=NOW(), samples=%s WHERE id=%s",
                (n, session["session_id"])
            )
            return

        def stats(key):
            vals = [s[key] for s in samples if key in s and s[key] is not None]
            if len(vals) < 5:
                return None, None
            mean = sum(vals) / len(vals)
            std  = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            return mean, std

        eff_m, eff_s = stats("efficiency")
        rec_m, rec_s = stats("recovery")
        fp_m,  fp_s  = stats("flow_permeate_lpm")
        dp_m,  dp_s  = stats("delta_pressure_bar")

        def w_high(m, s, mult=2): return (m + mult * s) if m is not None and s else None
        def w_low(m, s, mult=2):  return max(0, m - mult * s) if m is not None and s else None

        db.execute(
            "INSERT INTO device_baseline (device_id, learned_at, session_id) VALUES (%s, NOW(), %s) "
            "ON CONFLICT (device_id) DO NOTHING",
            (device_id, session["session_id"])
        )
        db.execute(
            """
            UPDATE device_baseline SET
                learned_at = NOW(), session_id = %s,
                efficiency_mean = %s, efficiency_std = %s,
                recovery_mean = %s, recovery_std = %s,
                flow_permeate_mean = %s, flow_permeate_std = %s,
                delta_pressure_mean = %s, delta_pressure_std = %s,
                efficiency_warn_low_learned = %s,
                efficiency_crit_low_learned = %s,
                recovery_warn_low_learned = %s,
                recovery_warn_high_learned = %s,
                flow_permeate_warn_low_learned = %s,
                pressure_warn_high_learned = %s,
                pressure_crit_high_learned = %s,
                delta_pressure_warn_high_learned = %s
            WHERE device_id = %s
            """,
            (
                session["session_id"],
                eff_m, eff_s, rec_m, rec_s, fp_m, fp_s, dp_m, dp_s,
                w_low(eff_m, eff_s, 2),  w_low(eff_m, eff_s, 3),
                w_low(rec_m, rec_s, 2),  w_high(rec_m, rec_s, 2),
                w_low(fp_m, fp_s, 2),
                w_high(dp_m, dp_s, 2),   w_high(dp_m, dp_s, 3),
                w_high(dp_m, dp_s, 2),
                device_id,
            )
        )
        db.execute(
            "UPDATE learning_sessions SET status='DONE', finished_at=NOW(), samples=%s WHERE id=%s",
            (n, session["session_id"])
        )
        BaselineCache.invalidate(device_id)
        log.info(f"[{device_id}] Baseline learned actualizado ({n} muestras)")


learn_engine = LearnEngine()

# ============================================================
# MESSAGE PROCESSOR
# ============================================================

class MessageProcessor:

    def dispatch(self, topic: str, payload: str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            log.warning(f"JSON inválido en {topic}: {e}")
            return

        parts = topic.split("/")
        if len(parts) < 3:
            return

        device_id = data.get("device_id", "unknown")

        # ACK from firmware — handled without timestamp validation.
        # topic format: fyntek/{device_id}/cmd/ack
        if len(parts) >= 4 and parts[2] == "cmd" and parts[3] == "ack":
            if command_engine:
                command_engine.handle_ack(device_id, data)
            return

        timestamp = ts_to_utc(data.get("ts"))
        if timestamp is None:
            log.warning(f"[{device_id}] timestamp inválido, descartando")
            return

        handlers = {
            "process":   self._handle_process,
            "quality":   self._handle_quality,
            "state":     self._handle_state,
            "inputs":    self._handle_inputs,
            "outputs":   self._handle_outputs,
            "heartbeat": self._handle_heartbeat,
        }
        handler = handlers.get(parts[2])
        if handler:
            handler(device_id, timestamp, data)

    def _auto_register(self, device_id: str, fw_version: str = ""):
        # No pisar fw_version con "" — /heartbeat no incluye este campo, solo
        # /process y /quality lo reportan. Conserva el último valor no vacío.
        db.execute(
            "INSERT INTO devices (device_id, fw_version, registered_at) VALUES (%s,%s,NOW()) "
            "ON CONFLICT (device_id) DO UPDATE SET fw_version = "
            "CASE WHEN EXCLUDED.fw_version <> '' THEN EXCLUDED.fw_version ELSE devices.fw_version END",
            (device_id, fw_version)
        )

    # PHASE1_COMPAT: translate old firmware field names → new names.
    # Remove after all devices have been reflashed with firmware v2.0+.
    _PROCESS_ALIASES = {
        "flow_perm_lpm":   "flow_permeate_lpm",
        "flow_rechazo_lpm": "flow_reject_lpm",
        "volume_perm_l":   "volume_permeate_l",
        "volume_rechazo_l": "volume_reject_l",
    }
    _INPUTS_ALIASES = {
        "crudo_ok":      "raw_water_ok",
        "presostato":    "pressure_switch",
        "reserva1":      "feed_tank_level_low",
        "flotante_pozo": "feed_tank_level_low",
        "reserva2":      "spare2",
    }

    # v2 column order mirrors LogicalInput / LogicalOutput enum in io_catalog.h.
    # Append-only — never reorder (must match enum index order).
    _INPUT_V2_COLUMNS = (
        "demand", "raw_water_available", "feed_tank_high", "feed_tank_low",
        "permeate_tank_high", "permeate_tank_low", "final_tank_high", "final_tank_low",
        "pressure_ok", "softener_regenerating", "well_low_level", "dosing_ok",
        "permeate_tank_demand", "final_tank_demand", "phase_failure",
    )
    _OUTPUT_V2_COLUMNS = (
        "low_pressure_pump", "high_pressure_pump", "well_pump", "transfer_pump",
        "flush_valve", "inlet_valve", "dosing_pump",
    )

    @staticmethod
    def _is_fw_v2(fw_version: str) -> bool:
        """True for firmware >= 2.0.0 (semantic /inputs and /outputs)."""
        try:
            return int(fw_version.split(".")[0]) >= 2
        except (ValueError, IndexError, AttributeError):
            return False

    def _handle_process(self, device_id, timestamp, data):
        data = {self._PROCESS_ALIASES.get(k, k): v for k, v in data.items()}
        db.execute(
            "INSERT INTO telemetry_process "
            "(time,device_id,flow_permeate_lpm,flow_reject_lpm,pressure_membrane_bar,"
            "pressure_brine_bar,pressure_membrane_voltage,pressure_brine_voltage,delta_p_bar,"
            "volume_permeate_l,volume_reject_l,fw_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                timestamp, device_id,
                validate_float(data.get("flow_permeate_lpm"),          0, 100),
                validate_float(data.get("flow_reject_lpm"),        0, 100),
                validate_float(data.get("pressure_membrane_bar"),   0, 50),
                validate_float(data.get("pressure_brine_bar"),      0, 50),
                validate_float(data.get("pressure_membrane_voltage"), 0, 15),
                validate_float(data.get("pressure_brine_voltage"),    0, 15),
                validate_float(data.get("delta_p_bar"),             -50, 50),
                validate_float(data.get("volume_permeate_l"),           0, 1e7),
                validate_float(data.get("volume_reject_l"),        0, 1e7),
                data.get("fw_version", ""),
            ),
        )
        self._auto_register(device_id, data.get("fw_version", ""))
        tracker.update_process(device_id, data)
        alert_manager.check_reconnection(device_id)
        realtime_engine.dispatch(device_id, timestamp, data)
        self._run_analytics(device_id, timestamp, data)

    def _handle_quality(self, device_id, timestamp, data):
        tds_in_v   = validate_float(data.get("tds_in_voltage"),  0, 5)
        tds_out_v  = validate_float(data.get("tds_out_voltage"), 0, 5)
        tds_in_ppm = validate_float(data.get("tds_in_ppm"),  0, 5000)
        tds_out_ppm= validate_float(data.get("tds_out_ppm"), 0, 5000)
        db.execute(
            "INSERT INTO telemetry_quality "
            "(time,device_id,tds_in_voltage,tds_out_voltage,tds_in_ppm,tds_out_ppm,fw_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (timestamp, device_id,
             tds_in_v, tds_out_v, tds_in_ppm, tds_out_ppm,
             data.get("fw_version", "")),
        )
        self._auto_register(device_id, data.get("fw_version", ""))
        KPIEngine.update_quality_cache(device_id, {
            "tds_in_voltage":  tds_in_v,
            "tds_out_voltage": tds_out_v,
            "tds_in_ppm":      tds_in_ppm,
            "tds_out_ppm":     tds_out_ppm,
        })

    def _handle_state(self, device_id, timestamp, data):
        state = data.get("state", "UNKNOWN")
        fault_reason = data.get("fault_reason") or None
        db.execute(
            "INSERT INTO telemetry_state (time,device_id,state,state_numeric,running,retry_count,fault_reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (timestamp, device_id, state, STATE_MAP.get(state, -1),
             validate_bool(data.get("running")), data.get("retry", 0), fault_reason),
        )
        # Sync fault_reason to device_status so Grafana can query it directly.
        # Clear it when state leaves FAULT (fault_reason="" or state != FAULT).
        effective_reason = fault_reason if state == "FAULT" else None
        db.execute(
            "INSERT INTO device_status (device_id, last_seen, online, state, fault_reason) "
            "VALUES (%s, %s, TRUE, %s, %s) "
            "ON CONFLICT (device_id) DO UPDATE "
            "SET state = EXCLUDED.state, fault_reason = EXCLUDED.fault_reason",
            (device_id, timestamp, state, effective_reason),
        )
        tracker.update_state(
            device_id, state,
            retry_count=data.get("retry", 0),
            fault_reason=fault_reason,
        )
        log.info(f"[{device_id}] Estado → {state}" + (f" ({fault_reason})" if fault_reason else ""))
        if state == "FAULT":
            self._run_analytics(device_id, timestamp, tracker.get_process(device_id) or {})

    def _handle_inputs(self, device_id, timestamp, data):
        if self._is_fw_v2(data.get("fw_version", "")):
            signals = {k: validate_bool(v)
                       for k, v in data.items()
                       if k not in ("device_id", "ts", "fw_version")}
            cols = self._INPUT_V2_COLUMNS
            vals = tuple(signals.get(c) for c in cols)
            db.execute(
                f"INSERT INTO telemetry_inputs (time, device_id, {', '.join(cols)}) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
                (timestamp, device_id) + vals,
            )
            tracker.update_inputs(device_id, signals)
            return
        # v1: legacy hardcoded pin names
        data = {self._INPUTS_ALIASES.get(k, k): v for k, v in data.items()}
        inputs = {k: validate_bool(data.get(k))
                  for k in ("demand","raw_water_ok","dose_ok","pressure_switch","feed_tank_level_low","spare2")}
        db.execute(
            "INSERT INTO telemetry_inputs "
            "(time,device_id,demand,raw_water_ok,dose_ok,pressure_switch,feed_tank_level_low,spare2) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (timestamp, device_id, *inputs.values()),
        )
        tracker.update_inputs(device_id, inputs)

    def _handle_outputs(self, device_id, timestamp, data):
        if self._is_fw_v2(data.get("fw_version", "")):
            signals = {k: validate_bool(v)
                       for k, v in data.items()
                       if k not in ("device_id", "ts", "fw_version")}
            cols = self._OUTPUT_V2_COLUMNS
            vals = tuple(signals.get(c) for c in cols)
            db.execute(
                f"INSERT INTO telemetry_outputs (time, device_id, {', '.join(cols)}) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
                (timestamp, device_id) + vals,
            )
            tracker.update_outputs(device_id, signals)
            return
        # v1: legacy hardcoded relay names
        outputs = {
            "pump_low":    validate_bool(data.get("pump_low")),
            "pump_high":   validate_bool(data.get("pump_high")),
            "pump_inlet":  validate_bool(data.get("pump_inlet")),
            "pump_dose":   validate_bool(data.get("pump_dose")),
            "valve_flush": validate_bool(data.get("valve_flush")),
            "valve_inlet": validate_bool(data.get("valve_inlet")),
        }
        db.execute(
            "INSERT INTO telemetry_outputs "
            "(time,device_id,pump_low,pump_high,pump_inlet,pump_dose,valve_flush,valve_inlet) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (timestamp, device_id, *outputs.values()),
        )
        tracker.update_outputs(device_id, outputs)

    # Valid FSM states the firmware can report. Used to guard heartbeat reconciliation.
    _FSM_STATES = {"IDLE", "STARTING", "PRODUCING", "FLUSHING", "STOPPING", "FAULT"}

    def _handle_heartbeat(self, device_id, timestamp, data):
        self._auto_register(device_id, data.get("fw_version", ""))

        # Heartbeat carries current FSM state for backend state reconciliation after
        # restart. /state remains the primary channel for FSM transition events.
        # If the field is absent (older firmware), this is a no-op.
        reported_state = data.get("state", "").upper()
        hb_fault_reason = data.get("fault_reason") or None
        effective_reason = hb_fault_reason if reported_state == "FAULT" else None
        if reported_state in self._FSM_STATES:
            db.execute(
                "INSERT INTO device_status (device_id,last_seen,online,state,fault_reason) "
                "VALUES (%s,%s,TRUE,%s,%s) "
                "ON CONFLICT (device_id) DO UPDATE "
                "SET last_seen=EXCLUDED.last_seen, online=TRUE, state=EXCLUDED.state, "
                # When FAULT: keep existing reason if new payload has none (old firmware).
                # When non-FAULT: clear to NULL so the panel shows "Sin falla".
                "fault_reason=CASE WHEN EXCLUDED.state='FAULT' "
                "THEN COALESCE(EXCLUDED.fault_reason, device_status.fault_reason) "
                "ELSE NULL END",
                (device_id, timestamp, reported_state, effective_reason),
            )
        else:
            db.execute(
                "INSERT INTO device_status (device_id,last_seen,online) VALUES (%s,%s,TRUE) "
                "ON CONFLICT (device_id) DO UPDATE "
                "SET last_seen=EXCLUDED.last_seen, online=TRUE",
                (device_id, timestamp),
            )
        alert_manager.check_reconnection(device_id)

    # ---- ANALYTICS PIPELINE ────────────────────────────────

    def _run_analytics(self, device_id: str, timestamp: datetime, process_data: Dict):
        state  = tracker.get_state(device_id)
        inputs = tracker.get_inputs(device_id)
        physical = KPIEngine.read_physical(process_data)
        metrics  = KPIEngine.compute_kpis(device_id, physical, state)

        if metrics:
            trend_analyzer.add_metrics(device_id, metrics)
            if learn_engine.is_active(device_id):
                learn_engine.add_sample(device_id, metrics)
            db.execute(
                "INSERT INTO metrics "
                "(time,device_id,recovery,efficiency,rejection_ratio,delta_pressure_bar,"
                "flow_permeate_lpm,flow_reject_lpm,tds_in_ppm,tds_out_ppm,cost_per_liter) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (timestamp, device_id,
                 metrics["recovery"],        metrics["efficiency"],
                 metrics["rejection_ratio"], metrics["delta_pressure_bar"],
                 metrics["flow_permeate_lpm"],   metrics["flow_reject_lpm"],
                 metrics["tds_in_ppm"],      metrics["tds_out_ppm"],
                 metrics["cost_per_liter"]),
            )

        trends = trend_analyzer.get_trends(device_id) if metrics else None
        result = diagnostic_engine.run(device_id, process_data, metrics, state, inputs, trends)

        root        = result["root_cause"]
        all_diags   = result["all_diags"]
        trend_diags = result["trend_diags"]

        # Histéresis
        event_diags    = [d for d in all_diags if d.is_event]
        slow_diags     = [d for d in all_diags if not d.is_event]
        confirmed_slow = hysteresis.update(device_id, [d.code for d in slow_diags])
        confirmed_slow_diags = [d for d in slow_diags if d.code in confirmed_slow]

        final_root = DIAG_OK
        is_new     = False

        if event_diags:
            final_root = event_diags[0]
            is_new     = True
        elif confirmed_slow and confirmed_slow_diags:
            confirmed_diags = sorted(
                confirmed_slow_diags,
                key=lambda d: d.score, reverse=True
            )
            final_root = confirmed_diags[0]
            final_root.confidence = diagnostic_engine._calc_confidence(
                final_root, confirmed_diags, metrics or {}
            )
            for other in confirmed_diags[1:]:
                final_root.symptoms.update(other.evidence)
            is_new = hysteresis.is_new_confirmation(device_id, final_root.code)

        # Hasta 2 diagnósticos secundarios (por score) para el panel "Factores detectados"
        secondary = sorted(
            (d for d in (event_diags + confirmed_slow_diags) if d.code != final_root.code),
            key=lambda d: d.score, reverse=True
        )
        secondary_diag_codes = [d.code for d in secondary[:2]]

        # Persistir diagnóstico
        if final_root.severity != "OK":
            db.execute(
                "INSERT INTO diagnostics "
                "(time,device_id,severity,code,message,action,details) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (timestamp, device_id, final_root.severity, final_root.code,
                 final_root.message, final_root.action, json.dumps(final_root.to_dict())),
            )
            log.info(f"[{device_id}] {final_root}")

        # ── Métricas de negocio (con rate limiting) ──────────
        biz: Dict = {}
        if biz_engine.should_run(device_id):
            biz = biz_engine.compute(device_id, timestamp, metrics, final_root, trend_diags)

        # ── Actualizar device_status ──────────────────────────
        new_health        = map_severity_to_health(final_root.severity)
        update_health     = state in ACTIVE_STATES or final_root.severity != "OK"

        if update_health:
            db.execute(
                """
                INSERT INTO device_status
                (device_id, last_seen, online, state,
                last_severity, last_diag_code, last_diag_message, last_action,
                flow_permeate_lpm, pressure_membrane_bar, pressure_brine_bar,
                pressure_membrane_voltage, pressure_brine_voltage, delta_p_bar,
                recovery, efficiency,
                health_status, health_code, health_message,
                health_action, health_updated_at, secondary_diag_codes,
                biz_liters_today, biz_target_liters, biz_fulfillment_pct,
                biz_waste_liters_today, biz_waste_pct,
                biz_risk_level, biz_risk_score,
                biz_degradation_pct, biz_degradation_days, biz_degradation_label,
                biz_health_age_hours)
                VALUES (%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id) DO UPDATE SET
                last_seen          = EXCLUDED.last_seen,
                online             = TRUE,
                state              = CASE WHEN EXCLUDED.state = 'UNKNOWN' THEN device_status.state ELSE EXCLUDED.state END,
                last_severity      = EXCLUDED.last_severity,
                last_diag_code     = EXCLUDED.last_diag_code,
                last_diag_message  = EXCLUDED.last_diag_message,
                last_action        = EXCLUDED.last_action,
                flow_permeate_lpm      = EXCLUDED.flow_permeate_lpm,
                pressure_membrane_bar  = EXCLUDED.pressure_membrane_bar,
                pressure_brine_bar     = EXCLUDED.pressure_brine_bar,
                pressure_membrane_voltage = EXCLUDED.pressure_membrane_voltage,
                pressure_brine_voltage    = EXCLUDED.pressure_brine_voltage,
                delta_p_bar            = EXCLUDED.delta_p_bar,
                recovery           = EXCLUDED.recovery,
                efficiency         = EXCLUDED.efficiency,
                health_status      = EXCLUDED.health_status,
                health_code        = EXCLUDED.health_code,
                health_message     = EXCLUDED.health_message,
                health_action      = EXCLUDED.health_action,
                health_updated_at  = EXCLUDED.health_updated_at,
                secondary_diag_codes = EXCLUDED.secondary_diag_codes,
                biz_liters_today       = COALESCE(EXCLUDED.biz_liters_today, device_status.biz_liters_today),
                biz_target_liters      = COALESCE(EXCLUDED.biz_target_liters, device_status.biz_target_liters),
                biz_fulfillment_pct    = COALESCE(EXCLUDED.biz_fulfillment_pct, device_status.biz_fulfillment_pct),
                biz_waste_liters_today = COALESCE(EXCLUDED.biz_waste_liters_today, device_status.biz_waste_liters_today),
                biz_waste_pct          = COALESCE(EXCLUDED.biz_waste_pct, device_status.biz_waste_pct),
                biz_risk_level         = COALESCE(EXCLUDED.biz_risk_level, device_status.biz_risk_level),
                biz_risk_score         = COALESCE(EXCLUDED.biz_risk_score, device_status.biz_risk_score),
                biz_degradation_pct    = COALESCE(EXCLUDED.biz_degradation_pct, device_status.biz_degradation_pct),
                biz_degradation_days   = COALESCE(EXCLUDED.biz_degradation_days, device_status.biz_degradation_days),
                biz_degradation_label  = COALESCE(EXCLUDED.biz_degradation_label, device_status.biz_degradation_label),
                biz_health_age_hours   = EXCLUDED.biz_health_age_hours
                """,
                (
                    device_id,
                    timestamp,
                    state,
                    final_root.severity,
                    final_root.code,
                    final_root.message,
                    final_root.action,

                    # 🔹 SOLO variables físicas válidas
                    physical.get("flow_permeate_lpm"),
                    physical.get("pressure_membrane_bar"),
                    physical.get("pressure_brine_bar"),
                    physical.get("pressure_membrane_voltage"),
                    physical.get("pressure_brine_voltage"),
                    physical.get("delta_p_bar"),

                    metrics["recovery"]   if metrics else None,
                    metrics["efficiency"] if metrics else None,

                    new_health,
                    final_root.code,
                    final_root.message,
                    final_root.action,
                    timestamp,
                    json.dumps(secondary_diag_codes),

                    biz.get("liters_today"),
                    biz.get("target_liters"),
                    biz.get("fulfillment_pct"),
                    biz.get("waste_liters_today"),
                    biz.get("waste_pct"),
                    biz.get("risk_level"),
                    biz.get("risk_score"),
                    biz.get("degradation_pct"),
                    biz.get("degradation_days"),
                    biz.get("degradation_label"),
                    biz.get("health_age_hours"),
                ),
            )      
        else:
            # Estado pasivo + OK: solo actualizamos campos operativos, no health
            db.execute(
                """
                INSERT INTO device_status
                (device_id, last_seen, online, state,
                last_severity, last_diag_code, last_diag_message, last_action,
                flow_permeate_lpm, pressure_membrane_bar, pressure_brine_bar,
                pressure_membrane_voltage, pressure_brine_voltage, delta_p_bar,
                recovery, efficiency,
                biz_health_age_hours)
                VALUES (%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id) DO UPDATE SET
                last_seen         = EXCLUDED.last_seen,
                online            = TRUE,
                state             = CASE WHEN EXCLUDED.state = 'UNKNOWN' THEN device_status.state ELSE EXCLUDED.state END,
                last_severity     = EXCLUDED.last_severity,
                last_diag_code    = EXCLUDED.last_diag_code,
                last_diag_message = EXCLUDED.last_diag_message,
                last_action       = EXCLUDED.last_action,
                flow_permeate_lpm     = EXCLUDED.flow_permeate_lpm,
                pressure_membrane_bar = EXCLUDED.pressure_membrane_bar,
                pressure_brine_bar    = EXCLUDED.pressure_brine_bar,
                pressure_membrane_voltage = EXCLUDED.pressure_membrane_voltage,
                pressure_brine_voltage    = EXCLUDED.pressure_brine_voltage,
                delta_p_bar           = EXCLUDED.delta_p_bar,
                recovery          = EXCLUDED.recovery,
                efficiency        = EXCLUDED.efficiency,
                biz_health_age_hours = EXCLUDED.biz_health_age_hours
                """,
                (
                    device_id,
                    timestamp,
                    state,
                    final_root.severity,
                    final_root.code,
                    final_root.message,
                    final_root.action,

                    # 🔹 SOLO físicas
                    physical.get("flow_permeate_lpm"),
                    physical.get("pressure_membrane_bar"),
                    physical.get("pressure_brine_bar"),
                    physical.get("pressure_membrane_voltage"),
                    physical.get("pressure_brine_voltage"),
                    physical.get("delta_p_bar"),

                    metrics["recovery"]   if metrics else None,
                    metrics["efficiency"] if metrics else None,

                    biz.get("health_age_hours"),
                ),
            )

        alert_manager.process(device_id, final_root, is_new, biz, metrics)


processor = MessageProcessor()

# ============================================================
# COMMAND ENGINE
# ============================================================

class CommandEngine:
    """
    Issues commands to firmware via MQTT and persists their lifecycle in DB.

    Lifecycle (MVP):  SENT → EXECUTED | REJECTED | TIMEOUT

    Thread ownership:
      issue()         → Flask thread  (HTTP request handler)
      handle_ack()    → MQTT thread   (paho on_message callback via dispatch())
      _timeout_loop() → daemon thread (started by start())

    Concurrency safety:
      All persistent state is DB-backed. No shared in-memory mutation.
      handle_ack() and _timeout_loop() both use WHERE status='SENT' in their
      UPDATEs — PostgreSQL row-level locking ensures only one wins; the loser
      matches 0 rows with no side effects.

    Idempotence:
      Duplicate ACKs (same command_id, status already set) match 0 rows.
      Late ACKs arriving after TIMEOUT match 0 rows — TIMEOUT is preserved.

    Note: db._pool access breaks encapsulation of DatabasePool. Acceptable for
    MVP; a future db.get_connection() method would be the clean path.
    """

    def __init__(self, mqtt_client):
        self._mqtt = mqtt_client

    # ── Issue ── Flask thread ──────────────────────────────────────────────────

    def issue(self, device_id: str, cmd: str, issued_by: str = "api") -> dict:
        """
        Creates a command in DB and publishes it to MQTT.
        Returns {"command_id": ..., "status": "SENT"} on success.
        Returns {"error": ...} on all failures — callers must check.

        Uses the DB connection directly (not db.execute()) so that
        psycopg2.IntegrityError from uq_commands_one_active_per_device is
        caught explicitly, preventing a ghost command_id on concurrent inserts.
        """
        if cmd not in COMMAND_ALLOWED:
            return {"error": "unknown_command",
                    "detail": f"allowed: {sorted(COMMAND_ALLOWED)}"}

        # Optimistic read — real enforcement is the unique partial index.
        existing = db.fetchall(
            "SELECT command_id FROM device_commands "
            "WHERE device_id = %s AND status IN ('SENT','RECEIVED','ACCEPTED')",
            (device_id,)
        )
        if existing:
            return {"error": "command_pending", "command_id": existing[0][0]}

        command_id  = str(uuid.uuid4())
        deadline_dt = datetime.now(timezone.utc) + timedelta(seconds=COMMAND_TIMEOUT_SEC)

        conn = db._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO device_commands "
                    "(command_id, device_id, cmd, status, issued_by, "
                    " issued_at, deadline_at, updated_at) "
                    "VALUES (%s, %s, %s, 'SENT', %s, NOW(), %s, NOW())",
                    (command_id, device_id, cmd, issued_by, deadline_dt)
                )
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            return {"error": "command_pending"}
        except Exception as e:
            conn.rollback()
            log.error(f"[CMD] INSERT error: {e}")
            return {"error": "db_error"}
        finally:
            db._pool.putconn(conn)

        payload = json.dumps({
            "command_id":  command_id,
            "cmd":         cmd,
            "issued_at":   int(time.time()),
            "deadline_at": int(deadline_dt.timestamp()),
        })
        self._mqtt.publish(f"fyntek/{device_id}/cmd", payload)
        log.info(f"[CMD] SENT {command_id[:8]}… device={device_id} cmd={cmd}")

        return {"command_id": command_id, "status": "SENT"}

    # ── Handle ACK ── MQTT thread ──────────────────────────────────────────────

    def handle_ack(self, device_id: str, data: dict):
        """
        Processes EXECUTED or REJECTED ACK received from firmware.

        WHERE status='SENT' makes every path idempotent:
          - Duplicate ACK:           0 rows updated, no side effects.
          - Late ACK after TIMEOUT:  0 rows updated, TIMEOUT is preserved.
        """
        command_id = data.get("command_id")
        ack        = data.get("ack")
        reason     = data.get("reason") or ""

        if not command_id or ack not in ("EXECUTED", "REJECTED"):
            log.warning(f"[CMD] ACK inválido device={device_id} data={data}")
            return

        if ack == "EXECUTED":
            db.execute(
                "UPDATE device_commands "
                "SET status='EXECUTED', executed_at=NOW(), "
                "    last_ack_at=NOW(), updated_at=NOW() "
                "WHERE command_id=%s AND device_id=%s AND status='SENT'",
                (command_id, device_id)
            )
            log.info(f"[CMD] EXECUTED {command_id[:8]}… device={device_id}")
        else:
            db.execute(
                "UPDATE device_commands "
                "SET status='REJECTED', rejected_at=NOW(), reject_reason=%s, "
                "    last_ack_at=NOW(), updated_at=NOW() "
                "WHERE command_id=%s AND device_id=%s AND status='SENT'",
                (reason, command_id, device_id)
            )
            log.info(f"[CMD] REJECTED {command_id[:8]}… device={device_id} "
                     f"reason={reason!r}")

    # ── Timeout loop ── daemon thread ──────────────────────────────────────────

    def start(self):
        t = threading.Thread(
            target=self._timeout_loop, daemon=True, name="cmd-timeout"
        )
        t.start()
        log.info("✅ CommandEngine timeout loop iniciado")

    def _timeout_loop(self):
        """
        Every 30 s: marks SENT commands past deadline_at as TIMEOUT.
        WHERE status='SENT' prevents overwriting EXECUTED or REJECTED even
        when racing with a concurrent handle_ack() call.
        """
        while True:
            time.sleep(30)
            try:
                rows = db.fetchall(
                    "SELECT command_id, device_id, cmd FROM device_commands "
                    "WHERE status = 'SENT' AND deadline_at < NOW()"
                )
                for r in rows:
                    db.execute(
                        "UPDATE device_commands "
                        "SET status='TIMEOUT', timeout_at=NOW(), updated_at=NOW() "
                        "WHERE command_id=%s AND status='SENT'",
                        (r[0],)
                    )
                    log.info(
                        f"[CMD] TIMEOUT {r[0][:8]}… device={r[1]} cmd={r[2]}"
                    )
            except Exception as e:
                log.error(f"[CMD] timeout_loop error: {e}", exc_info=True)


# Set in main() after MQTT client connects.
# Flask routes return 503 while this is None.
command_engine: "CommandEngine" = None
mqtt_client = None  # paho client — set in main(); used to publish retained config

# Per-device timestamp of last AI-issued command.
# IN-MEMORY: lost on backend restart. The DB unique partial index
# (uq_commands_one_active_per_device) is the authoritative enforcement.
# Cooldown is a UX-level safeguard, not a security boundary.
_ai_cooldown: Dict[str, float] = {}

# AI Control Gate state.
# IN-MEMORY: resets to AI_GATE_DEFAULT_MODE ("OBSERVE_ONLY") on every
# backend restart. This is intentional — safe-by-default on recovery.
# If AUTO_EXECUTE is needed after a restart, an operator must explicitly
# re-enable it via POST /api/v1/ai/mode with ADMIN_API_KEY.
# Do NOT add DB persistence here without an explicit approval workflow.
_ai_gate: Dict[str, Any] = {
    "mode":       AI_GATE_DEFAULT_MODE,
    "updated_at": None,
    "updated_by": "system:startup",
}

# ============================================================
# AI DECISION ENGINE
# ============================================================

class AIDecisionEngine:
    """
    Proactively calls an external AI API for devices with ai_mode != 'OFF'.

    Modes per device (devices.ai_mode):
      OFF    — device skipped, no AI call
      VIEWER — context sent, decision logged, command NOT executed
      AUTO   — context sent, command executed if policy allows it

    The AI only suggests. is_ai_command_allowed() in ai_client decides
    whether AUTO execution proceeds (cooldown, FSM state, anti-oscillation).
    All execution goes through CommandEngine — never direct MQTT.

    Thread: single daemon thread started by start().
    _last_auto is in-memory only (acceptable for MVP).
    """

    def __init__(self) -> None:
        self._started  = False
        # Per-device: (last_cmd_str, last_executed_at datetime) — in-memory
        self._last_auto: Dict[str, tuple] = {}

    def start(self) -> None:
        if self._started:
            log.warning("[AI-ENGINE] start() called more than once — ignored")
            return
        if not AI_ENDPOINT_URL:
            log.warning("[AI-ENGINE] AI_ENDPOINT_URL not set — engine disabled")
            return
        self._started = True
        log.info(
            f"[AI-ENGINE] Starting — endpoint={AI_ENDPOINT_URL} "
            f"poll={AI_POLL_INTERVAL_SEC}s timeout={AI_TIMEOUT_SEC}s "
            f"auto_cooldown={AI_AUTO_COOLDOWN_SEC}s"
        )
        t = threading.Thread(target=self._loop, daemon=True, name="ai-decision")
        t.start()
        log.info("✅ AIDecisionEngine iniciado")

    def _loop(self) -> None:
        # Run first cycle immediately on start, then sleep between subsequent cycles.
        while True:
            try:
                self._run_cycle()
            except Exception as e:
                log.error(f"[AI-ENGINE] Cycle error: {e}", exc_info=True)
            time.sleep(AI_POLL_INTERVAL_SEC)

    def _run_cycle(self) -> None:
        rows = db.fetchall(
            "SELECT device_id, ai_mode FROM devices "
            "WHERE ai_mode != 'OFF' AND COALESCE(enabled, TRUE) = TRUE"
        )
        for device_id, ai_mode in rows:
            try:
                self._process_device(device_id, ai_mode)
            except Exception as e:
                log.error(f"[AI-ENGINE] Error processing {device_id}: {e}")

    def _process_device(self, device_id: str, ai_mode: str) -> None:
        ctx    = _ai.build_context(
            device_id, db,
            window_seconds=AI_WINDOW_SECONDS,
            sample_period_sec=AI_SAMPLE_PERIOD_SEC,
            max_samples=AI_WINDOW_MAX_SAMPLES,
        )
        req_id   = ctx["request_id"]
        n_samples = len(ctx.get("telemetry_window", {}).get("samples", []))

        log.info(
            f"[AI-ENGINE] req={req_id[:8]}… device={device_id} mode={ai_mode} "
            f"state={ctx['fsm_state']} connectivity={ctx['connectivity']} samples={n_samples}"
        )

        decision, error = _ai.get_ai_decision(ctx, AI_ENDPOINT_URL, AI_TIMEOUT_SEC, api_token=AI_API_TOKEN)

        if error:
            log.warning(f"[AI-ENGINE] req={req_id[:8]}… API error: {error}")
            self._save_decision(device_id, ai_mode, None, False, None, "FAILED", error)
            return

        dec_type   = decision["decision"]
        confidence = decision["confidence"]
        reason     = decision.get("reason", "")
        suggested  = decision.get("suggested_cmd")

        log.info(
            f"[AI-ENGINE] req={req_id[:8]}… decision={dec_type} "
            f"confidence={confidence:.2f} suggested_cmd={suggested} reason={reason!r}"
        )

        executed    = False
        exec_status = None
        exec_result = None

        if ai_mode == "VIEWER":
            exec_status = "REJECTED"
            exec_result = "viewer_mode: suggestions logged only"
            log.info(f"[AI-ENGINE] VIEWER — logged suggestion: {suggested}")

        elif ai_mode == "AUTO" and dec_type == "EXECUTE" and suggested:
            last_cmd, last_at = self._last_auto.get(device_id, (None, None))
            allowed, deny_reason = _ai.is_ai_command_allowed(
                cmd=suggested,
                fsm_state=ctx["fsm_state"],
                last_auto_cmd=last_cmd,
                last_auto_at=last_at,
                auto_cooldown_sec=AI_AUTO_COOLDOWN_SEC,
            )
            if not allowed:
                exec_status = "REJECTED"
                exec_result = deny_reason
                log.info(f"[AI-ENGINE] AUTO blocked by policy: {deny_reason}")
            else:
                result = command_engine.issue(device_id, suggested, issued_by="ai_auto")
                if "error" not in result:
                    executed    = True
                    exec_status = "SUCCESS"
                    exec_result = result["command_id"]
                    self._last_auto[device_id] = (suggested, datetime.now(timezone.utc))
                    log.info(
                        f"[AI-ENGINE] AUTO executed {suggested} — "
                        f"device={device_id} cmd_id={result['command_id'][:8]}…"
                    )
                else:
                    exec_status = "FAILED"
                    exec_result = result["error"]
                    log.warning(
                        f"[AI-ENGINE] AUTO failed {suggested} — "
                        f"device={device_id}: {result['error']}"
                    )

        self._save_decision(
            device_id, ai_mode, decision,
            executed,
            datetime.now(timezone.utc) if executed else None,
            exec_status, exec_result,
        )

    def _save_decision(
        self,
        device_id:   str,
        ai_mode:     str,
        decision:    Optional[dict],
        executed:    bool,
        executed_at: Optional[datetime],
        exec_status: Optional[str],
        exec_result: Optional[str],
    ) -> None:
        db.execute(
            "INSERT INTO ai_decisions "
            "(device_id, ai_mode, decision, confidence, reason, "
            " suggested_cmd, executed, executed_at, exec_status, exec_result) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                device_id,
                ai_mode,
                decision["decision"]            if decision else "ERROR",
                decision.get("confidence")      if decision else None,
                decision.get("reason")          if decision else exec_result,
                decision.get("suggested_cmd")   if decision else None,
                executed,
                executed_at,
                exec_status,
                exec_result,
            ),
        )


ai_decision_engine: "AIDecisionEngine" = None


# ── Realtime AI Engine ────────────────────────────────────────────────────────

class RealtimeAIEngine:
    """
    Per-sample AI calls fired from the MQTT process handler (1 Hz).

    Backpressure: if the previous call for a device has not returned, the new
    sample is dropped and logged. No queue — stale data is never sent.

    Context window: controlled by AI_REALTIME_CONTEXT_SECONDS (default 0).
      0  → sample + metadata only; 1 DB query (device_status).
      >0 → includes context_window; 2 DB queries (device_status + window).
    """

    _MODE_CACHE_TTL_SEC = 60

    def __init__(self):
        self._in_flight: set              = set()
        self._lock                        = threading.Lock()
        self._executor                    = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="ai-rt"
        )
        self._mode_cache: Dict[str, tuple] = {}   # device_id → (ai_mode, expires_at)

    def dispatch(self, device_id: str, timestamp: "datetime", process_data: dict) -> None:
        """Called from _handle_process() in the MQTT callback thread. Must return fast."""
        if not AI_REALTIME_ENDPOINT_URL:
            return

        ai_mode = self._get_ai_mode(device_id)
        if ai_mode == "OFF":
            return

        with self._lock:
            if device_id in self._in_flight:
                log.debug(f"[AI-RT] {device_id}: previous call in-flight — sample dropped")
                return
            self._in_flight.add(device_id)

        self._executor.submit(self._call, device_id, timestamp, process_data, ai_mode)

    def _get_ai_mode(self, device_id: str) -> str:
        cached = self._mode_cache.get(device_id)
        if cached and cached[1] > time.time():
            return cached[0]
        rows = db.fetchall(
            "SELECT ai_mode FROM devices WHERE device_id = %s", (device_id,)
        )
        mode = (rows[0][0] or "OFF") if rows else "OFF"
        self._mode_cache[device_id] = (mode, time.time() + self._MODE_CACHE_TTL_SEC)
        return mode

    def _call(self, device_id: str, timestamp: "datetime", process_data: dict, ai_mode: str) -> None:
        try:
            quality  = KPIEngine._last_quality.get(device_id, {})
            ctx = _ai.build_realtime_context(
                device_id     = device_id,
                process_data  = process_data,
                quality_cache = quality,
                fsm_state     = tracker.get_state(device_id),
                fault_reason  = tracker.get_fault_reason(device_id),
                retry_count   = tracker.get_retry_count(device_id),
                inputs        = tracker.get_inputs(device_id),
                outputs       = tracker.get_outputs(device_id),
                db            = db,
                context_seconds = AI_REALTIME_CONTEXT_SECONDS,
            )

            req_id = ctx["request_id"]
            log.info(
                f"[AI-RT] req={req_id[:8]}… device={device_id} mode={ai_mode} "
                f"state={ctx['fsm_state']} context_sec={AI_REALTIME_CONTEXT_SECONDS}"
            )

            decision, error = _ai.get_ai_decision(ctx, AI_REALTIME_ENDPOINT_URL, AI_REALTIME_TIMEOUT_SEC, api_token=AI_REALTIME_API_TOKEN)

            if error:
                log.warning(f"[AI-RT] req={req_id[:8]}… error: {error}")
                self._save(device_id, ai_mode, None, False, None, "FAILED", error)
                return

            dec_type  = decision["decision"]
            suggested = decision.get("suggested_cmd")
            log.info(
                f"[AI-RT] req={req_id[:8]}… decision={dec_type} "
                f"suggested_cmd={suggested} reason={decision.get('reason', '')!r}"
            )

            executed    = False
            exec_status = None
            exec_result = None

            if ai_mode == "VIEWER":
                exec_status = "REJECTED"
                exec_result = "viewer_mode"

            elif ai_mode == "AUTO" and dec_type == "EXECUTE" and suggested:
                last_cmd, last_at = ai_decision_engine._last_auto.get(device_id, (None, None))
                allowed, deny_reason = _ai.is_ai_command_allowed(
                    cmd=suggested,
                    fsm_state=ctx["fsm_state"],
                    last_auto_cmd=last_cmd,
                    last_auto_at=last_at,
                    auto_cooldown_sec=AI_AUTO_COOLDOWN_SEC,
                )
                if not allowed:
                    exec_status = "REJECTED"
                    exec_result = deny_reason
                    log.info(f"[AI-RT] AUTO blocked: {deny_reason}")
                else:
                    result = command_engine.issue(device_id, suggested, issued_by="ai_realtime")
                    if "error" not in result:
                        executed    = True
                        exec_status = "SUCCESS"
                        exec_result = result["command_id"]
                        ai_decision_engine._last_auto[device_id] = (
                            suggested, datetime.now(timezone.utc)
                        )
                        log.info(f"[AI-RT] AUTO executed {suggested} — device={device_id}")
                    else:
                        exec_status = "FAILED"
                        exec_result = result["error"]

            self._save(device_id, ai_mode, decision, executed,
                       datetime.now(timezone.utc) if executed else None,
                       exec_status, exec_result)

        except Exception as e:
            log.error(f"[AI-RT] {device_id}: unhandled error: {e}", exc_info=True)
        finally:
            with self._lock:
                self._in_flight.discard(device_id)

    def _save(self, device_id, ai_mode, decision, executed, executed_at, exec_status, exec_result):
        db.execute(
            "INSERT INTO ai_decisions "
            "(device_id, ai_mode, decision, confidence, reason, "
            " suggested_cmd, executed, executed_at, exec_status, exec_result) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                device_id, ai_mode,
                decision["decision"]          if decision else "ERROR",
                decision.get("confidence")    if decision else None,
                decision.get("reason")        if decision else exec_result,
                decision.get("suggested_cmd") if decision else None,
                executed, executed_at, exec_status, exec_result,
            ),
        )


realtime_engine: "RealtimeAIEngine" = None

# ============================================================
# MQTT
# ============================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(MQTT_TOPIC)
        log.info(f"✅ MQTT conectado → {MQTT_TOPIC}")
    else:
        log.error(f"❌ MQTT error (rc={rc})")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning(f"⚡ MQTT desconectado (rc={rc}). Reconectando...")

def on_message(client, userdata, msg):
    try:
        processor.dispatch(msg.topic, msg.payload.decode("utf-8"))
    except Exception as e:
        log.error(f"Error en {msg.topic}: {e}", exc_info=True)

# ============================================================
# API HTTP
# ============================================================

api = Flask(__name__)


@api.route("/api/health", methods=["GET"])
def health_check():
    mqtt_ok = mqtt_client.is_connected() if mqtt_client else False
    db_info = db.health
    overall = "ok" if db_info["status"] == "ok" and mqtt_ok else "degraded"
    return jsonify({
        "status": overall,
        "db": db_info,
        "mqtt": {"connected": mqtt_ok},
        "uptime_sec": int(time.time() - _BACKEND_START_TIME),
    })


# ── Auth ──────────────────────────────────────────────────────────────────────

def _check_bearer(key: str) -> bool:
    """Extract and validate a Bearer token against the given key. Timing-safe."""
    if not key:
        return True                                    # auth disabled
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], key)


def require_api_key(f):
    """
    Decorator for AI-facing routes (context + commands).
    Validates against AI_API_KEY.
    Auth disabled when AI_API_KEY is empty (internal/dev mode).

    Does NOT grant access to admin operations (gate mode changes).
    """
    @functools.wraps(f)
    def _wrapper(*args, **kwargs):
        if not _check_bearer(AI_API_KEY):
            return jsonify({
                "error":  "unauthorized",
                "detail": "Valid AI Bearer token required",
            }), 401
        return f(*args, **kwargs)
    return _wrapper


def require_basic_auth(f):
    """
    HTTP Basic Auth decorator for /admin/* routes.
    Uses ADMIN_PANEL_USER / ADMIN_PANEL_PASS env vars.
    Auth disabled when ADMIN_PANEL_USER is empty (dev mode).
    """
    @functools.wraps(f)
    def _wrapper(*args, **kwargs):
        if not ADMIN_PANEL_USER:
            return f(*args, **kwargs)
        auth = request.authorization
        valid = (
            auth is not None
            and hmac.compare_digest(auth.username or "", ADMIN_PANEL_USER)
            and hmac.compare_digest(auth.password or "", ADMIN_PANEL_PASS)
        )
        if not valid:
            return ("Authentication required", 401,
                    {"WWW-Authenticate": 'Basic realm="KAIROX Admin"'})
        return f(*args, **kwargs)
    return _wrapper


def require_admin_key(f):
    """
    Decorator for admin-only routes (AI Gate mode changes).
    Validates against ADMIN_API_KEY.
    Auth disabled when ADMIN_API_KEY is empty (dev mode only).

    The AI service must NOT hold this key — it must never be able
    to modify its own permission level.
    """
    @functools.wraps(f)
    def _wrapper(*args, **kwargs):
        if not _check_bearer(ADMIN_API_KEY):
            return jsonify({
                "error":  "unauthorized",
                "detail": "Valid admin Bearer token required",
            }), 401
        return f(*args, **kwargs)
    return _wrapper


@api.route("/api/config/<device_id>", methods=["GET"])
def get_config(device_id):
    rows = db.fetchall(
        "SELECT pump_power_kw,cost_kwh,cost_water_m3,target_recovery,"
        "target_efficiency,daily_target_liters,friendly_name,location,"
        "flow_factor_1,flow_factor_2,tds_temperature,"
        "min_flow_lpm,max_flow_lpm,flow_fault_delay_sec,"
        "min_recovery_pct,max_recovery_pct,recovery_fault_delay_sec,"
        "tds1_cal_slope,tds1_cal_offset,tds2_cal_slope,tds2_cal_offset,"
        "pressure_membrane_enabled,pressure_membrane_min_voltage,pressure_membrane_max_voltage,"
        "pressure_membrane_min_bar,pressure_membrane_max_bar,"
        "pressure_membrane_limits_enabled,pressure_membrane_high_limit,pressure_fault_delay_sec,"
        "pressure_brine_enabled,pressure_brine_min_voltage,pressure_brine_max_voltage,"
        "pressure_brine_min_bar,pressure_brine_max_bar,"
        "pressure_brine_high_limit,pressure_brine_alarm_enabled,"
        "delta_p_alarm_enabled,delta_p_alarm_limit,"
        "flow_protection_enabled,recovery_protection_enabled "
        "FROM device_config WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    r = rows[0]
    return jsonify({
        "pump_power_kw":            r[0],  "cost_kwh":              r[1],
        "cost_water_m3":            r[2],  "target_recovery":       r[3],
        "target_efficiency":        r[4],  "daily_target_liters":   r[5],
        "friendly_name":            r[6],  "location":              r[7],
        "flow_factor_1":            r[8],  "flow_factor_2":         r[9],
        "tds_temperature":          r[10],
        "min_flow_lpm":             r[11], "max_flow_lpm":          r[12],
        "flow_fault_delay_sec":     r[13],
        "min_recovery_pct":         r[14], "max_recovery_pct":      r[15],
        "recovery_fault_delay_sec": r[16],
        "tds1_cal_slope":           r[17], "tds1_cal_offset":       r[18],
        "tds2_cal_slope":           r[19], "tds2_cal_offset":       r[20],
        "pressure_membrane_enabled":        r[21],
        "pressure_membrane_min_voltage":    r[22],
        "pressure_membrane_max_voltage":    r[23],
        "pressure_membrane_min_bar":        r[24],
        "pressure_membrane_max_bar":        r[25],
        "pressure_membrane_limits_enabled": r[26],
        "pressure_membrane_high_limit":     r[27],
        "pressure_fault_delay_sec":         r[28],
        "pressure_brine_enabled":           r[29],
        "pressure_brine_min_voltage":       r[30],
        "pressure_brine_max_voltage":       r[31],
        "pressure_brine_min_bar":           r[32],
        "pressure_brine_max_bar":           r[33],
        "pressure_brine_high_limit":        r[34],
        "pressure_brine_alarm_enabled":     r[35],
        "delta_p_alarm_enabled":            r[36],
        "delta_p_alarm_limit":              r[37],
        "flow_protection_enabled":          r[38] if r[38] is not None else True,
        "recovery_protection_enabled":      r[39] if r[39] is not None else True,
    })

@api.route("/api/config/<device_id>", methods=["POST"])
def set_config(device_id):
    data  = request.get_json(silent=True) or {}
    ff1   = float(data.get("flow_factor_1",   450.0))
    ff2   = float(data.get("flow_factor_2",   450.0))
    tds_t = float(data.get("tds_temperature",  25.0))
    min_flow      = float(data.get("min_flow_lpm",              0.2))
    max_flow      = float(data.get("max_flow_lpm",             20.0))
    flow_delay    = int(  data.get("flow_fault_delay_sec",       30))
    min_rec       = float(data.get("min_recovery_pct",          10.0))
    max_rec       = float(data.get("max_recovery_pct",          85.0))
    rec_delay     = int(  data.get("recovery_fault_delay_sec",   60))
    tds1_cal_slope  = float(data.get("tds1_cal_slope",  0.0))
    tds1_cal_offset = float(data.get("tds1_cal_offset", 0.0))
    tds2_cal_slope  = float(data.get("tds2_cal_slope",  0.0))
    tds2_cal_offset = float(data.get("tds2_cal_offset", 0.0))
    # ── Calibración de presión (voltaje→bar), por canal ──────────────────────
    pm_en      = bool(data.get("pressure_membrane_enabled",        False))
    pm_minv    = float(data.get("pressure_membrane_min_voltage",   0.5))
    pm_maxv    = float(data.get("pressure_membrane_max_voltage",   4.5))
    pm_minb    = float(data.get("pressure_membrane_min_bar",       0.0))
    pm_maxb    = float(data.get("pressure_membrane_max_bar",      14.0))
    pm_lim     = bool(data.get("pressure_membrane_limits_enabled", False))
    pm_hi      = float(data.get("pressure_membrane_high_limit",   12.0))
    p_fdly     = int(  data.get("pressure_fault_delay_sec",         3))
    pb_en      = bool(data.get("pressure_brine_enabled",            False))
    pb_minv    = float(data.get("pressure_brine_min_voltage",      0.5))
    pb_maxv    = float(data.get("pressure_brine_max_voltage",      4.5))
    pb_minb    = float(data.get("pressure_brine_min_bar",          0.0))
    pb_maxb    = float(data.get("pressure_brine_max_bar",         14.0))
    # ── Alarmas diagnósticas backend-only (NO se publican a firmware) ─────────
    pb_hi_alarm     = float(data.get("pressure_brine_high_limit",  8.0))
    pb_alarm_en     = bool(data.get("pressure_brine_alarm_enabled", False))
    dp_alarm_en     = bool(data.get("delta_p_alarm_enabled",        False))
    dp_alarm_limit  = float(data.get("delta_p_alarm_limit",         5.0))
    # ── Flags de habilitación de protecciones activas (CFG_VERSION 2) ─────────
    flow_prot_en    = bool(data.get("flow_protection_enabled",     True))
    rec_prot_en     = bool(data.get("recovery_protection_enabled", True))
    db.execute(
        "INSERT INTO device_config "
        "(device_id,pump_power_kw,cost_kwh,cost_water_m3,"
        "target_recovery,target_efficiency,daily_target_liters,"
        "flow_factor_1,flow_factor_2,tds_temperature,"
        "min_flow_lpm,max_flow_lpm,flow_fault_delay_sec,"
        "min_recovery_pct,max_recovery_pct,recovery_fault_delay_sec,"
        "tds1_cal_slope,tds1_cal_offset,tds2_cal_slope,tds2_cal_offset,"
        "pressure_membrane_enabled,pressure_membrane_min_voltage,pressure_membrane_max_voltage,"
        "pressure_membrane_min_bar,pressure_membrane_max_bar,"
        "pressure_membrane_limits_enabled,pressure_membrane_high_limit,pressure_fault_delay_sec,"
        "pressure_brine_enabled,pressure_brine_min_voltage,pressure_brine_max_voltage,"
        "pressure_brine_min_bar,pressure_brine_max_bar,"
        "pressure_brine_high_limit,pressure_brine_alarm_enabled,"
        "delta_p_alarm_enabled,delta_p_alarm_limit,"
        "flow_protection_enabled,recovery_protection_enabled,"
        "friendly_name,location,updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
        "ON CONFLICT (device_id) DO UPDATE SET "
        "pump_power_kw=EXCLUDED.pump_power_kw, cost_kwh=EXCLUDED.cost_kwh, "
        "cost_water_m3=EXCLUDED.cost_water_m3, target_recovery=EXCLUDED.target_recovery, "
        "target_efficiency=EXCLUDED.target_efficiency, "
        "daily_target_liters=EXCLUDED.daily_target_liters, "
        "flow_factor_1=EXCLUDED.flow_factor_1, flow_factor_2=EXCLUDED.flow_factor_2, "
        "tds_temperature=EXCLUDED.tds_temperature, "
        "min_flow_lpm=EXCLUDED.min_flow_lpm, max_flow_lpm=EXCLUDED.max_flow_lpm, "
        "flow_fault_delay_sec=EXCLUDED.flow_fault_delay_sec, "
        "min_recovery_pct=EXCLUDED.min_recovery_pct, max_recovery_pct=EXCLUDED.max_recovery_pct, "
        "recovery_fault_delay_sec=EXCLUDED.recovery_fault_delay_sec, "
        "tds1_cal_slope=EXCLUDED.tds1_cal_slope, tds1_cal_offset=EXCLUDED.tds1_cal_offset, "
        "tds2_cal_slope=EXCLUDED.tds2_cal_slope, tds2_cal_offset=EXCLUDED.tds2_cal_offset, "
        "pressure_membrane_enabled=EXCLUDED.pressure_membrane_enabled, "
        "pressure_membrane_min_voltage=EXCLUDED.pressure_membrane_min_voltage, "
        "pressure_membrane_max_voltage=EXCLUDED.pressure_membrane_max_voltage, "
        "pressure_membrane_min_bar=EXCLUDED.pressure_membrane_min_bar, "
        "pressure_membrane_max_bar=EXCLUDED.pressure_membrane_max_bar, "
        "pressure_membrane_limits_enabled=EXCLUDED.pressure_membrane_limits_enabled, "
        "pressure_membrane_high_limit=EXCLUDED.pressure_membrane_high_limit, "
        "pressure_fault_delay_sec=EXCLUDED.pressure_fault_delay_sec, "
        "pressure_brine_enabled=EXCLUDED.pressure_brine_enabled, "
        "pressure_brine_min_voltage=EXCLUDED.pressure_brine_min_voltage, "
        "pressure_brine_max_voltage=EXCLUDED.pressure_brine_max_voltage, "
        "pressure_brine_min_bar=EXCLUDED.pressure_brine_min_bar, "
        "pressure_brine_max_bar=EXCLUDED.pressure_brine_max_bar, "
        "pressure_brine_high_limit=EXCLUDED.pressure_brine_high_limit, "
        "pressure_brine_alarm_enabled=EXCLUDED.pressure_brine_alarm_enabled, "
        "delta_p_alarm_enabled=EXCLUDED.delta_p_alarm_enabled, "
        "delta_p_alarm_limit=EXCLUDED.delta_p_alarm_limit, "
        "flow_protection_enabled=EXCLUDED.flow_protection_enabled, "
        "recovery_protection_enabled=EXCLUDED.recovery_protection_enabled, "
        "friendly_name=EXCLUDED.friendly_name, location=EXCLUDED.location, updated_at=NOW()",
        (device_id,
         data.get("pump_power_kw", 0.75),     data.get("cost_kwh", 0.12),
         data.get("cost_water_m3", 0.80),     data.get("target_recovery", 0.65),
         data.get("target_efficiency", 0.92), data.get("daily_target_liters", 0),
         ff1, ff2, tds_t,
         min_flow, max_flow, flow_delay,
         min_rec, max_rec, rec_delay,
         tds1_cal_slope, tds1_cal_offset, tds2_cal_slope, tds2_cal_offset,
         pm_en, pm_minv, pm_maxv, pm_minb, pm_maxb, pm_lim, pm_hi, p_fdly,
         pb_en, pb_minv, pb_maxv, pb_minb, pb_maxb,
         pb_hi_alarm, pb_alarm_en, dp_alarm_en, dp_alarm_limit,
         flow_prot_en, rec_prot_en,
         data.get("friendly_name", ""),       data.get("location", "")),
    )
    KPIEngine.invalidate_config(device_id)
    _publish_device_config(device_id, ff1, ff2, tds_t,
                           min_flow, max_flow, flow_delay,
                           min_rec, max_rec, rec_delay,
                           tds1_cal_slope, tds1_cal_offset,
                           tds2_cal_slope, tds2_cal_offset,
                           pm_en, pm_minv, pm_maxv, pm_minb, pm_maxb, pm_lim, pm_hi, p_fdly,
                           pb_en, pb_minv, pb_maxv, pb_minb, pb_maxb,
                           flow_prot_en, rec_prot_en)
    alert_manager.fire_event(
        device_id, "CONFIG_UPDATED",
        f"Config actualizada: ff1={ff1} ff2={ff2} tds_t={tds_t} "
        f"min_flow={min_flow} max_flow={max_flow} flow_delay={flow_delay}s "
        f"min_rec={min_rec}% max_rec={max_rec}% rec_delay={rec_delay}s "
        f"tds1_cal={tds1_cal_slope}/{tds1_cal_offset} tds2_cal={tds2_cal_slope}/{tds2_cal_offset} "
        f"pm_en={pm_en} pm_lim={pm_lim} pm_hi={pm_hi} pb_en={pb_en}",
        cooldown_sec=60,
    )
    return jsonify({"status": "ok"})


def _publish_device_config(
    device_id: str,
    ff1: float, ff2: float, tds_t: float,
    min_flow: float = 0.2, max_flow: float = 20.0, flow_delay: int = 30,
    min_rec: float = 10.0, max_rec: float = 85.0, rec_delay: int = 60,
    tds1_cal_slope: float = 0.0, tds1_cal_offset: float = 0.0,
    tds2_cal_slope: float = 0.0, tds2_cal_offset: float = 0.0,
    pm_en: bool = False, pm_minv: float = 0.5, pm_maxv: float = 4.5,
    pm_minb: float = 0.0, pm_maxb: float = 14.0,
    pm_lim: bool = False, pm_hi: float = 12.0, p_fdly: int = 3,
    pb_en: bool = False, pb_minv: float = 0.5, pb_maxv: float = 4.5,
    pb_minb: float = 0.0, pb_maxb: float = 14.0,
    flow_prot_en: bool = True, rec_prot_en: bool = True,
):
    """Publish retained sensor config + process protections to the device via MQTT.

    Retained so the device receives it immediately on reconnect.
    updated_at is the canonical version field — firmware applies only if newer.
    Missing fields in payload fall back to firmware current config (safe partial update).

    tds{1,2}_cal_slope == 0.0 → firmware sin calibración cargada, usa el
    polinomio DFRobot de fallback (voltageToPpm). > 0.0 activa calibración
    lineal por canal (ppm = slope * mV + offset).
    """
    if not mqtt_client:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({
        "flow_factor_1":            ff1,
        "flow_factor_2":            ff2,
        "tds_temperature":          tds_t,
        "min_flow_lpm":             min_flow,
        "max_flow_lpm":             max_flow,
        "flow_fault_delay_sec":     flow_delay,
        "min_recovery_pct":         min_rec,
        "max_recovery_pct":         max_rec,
        "recovery_fault_delay_sec": rec_delay,
        "tds1_cal_slope":           tds1_cal_slope,
        "tds1_cal_offset":          tds1_cal_offset,
        "tds2_cal_slope":           tds2_cal_slope,
        "tds2_cal_offset":          tds2_cal_offset,
        "pressure_membrane_enabled":        pm_en,
        "pressure_membrane_min_voltage":    pm_minv,
        "pressure_membrane_max_voltage":    pm_maxv,
        "pressure_membrane_min_bar":        pm_minb,
        "pressure_membrane_max_bar":        pm_maxb,
        "pressure_membrane_limits_enabled": pm_lim,
        "pressure_membrane_high_limit":     pm_hi,
        "pressure_fault_delay_sec":         p_fdly,
        "pressure_brine_enabled":           pb_en,
        "pressure_brine_min_voltage":       pb_minv,
        "pressure_brine_max_voltage":       pb_maxv,
        "pressure_brine_min_bar":           pb_minb,
        "pressure_brine_max_bar":           pb_maxb,
        "flow_protection_enabled":          flow_prot_en,
        "recovery_protection_enabled":      rec_prot_en,
        "updated_at":               int(_time.time()),
    })
    mqtt_client.publish(f"fyntek/{device_id}/config", payload, retain=True)
    log.info(f"[{device_id}] Config MQTT publicada: ff1={ff1} ff2={ff2} tds_t={tds_t} "
             f"min_flow={min_flow} max_flow={max_flow} flow_delay={flow_delay}s "
             f"min_rec={min_rec}% max_rec={max_rec}% rec_delay={rec_delay}s "
             f"tds1_cal={tds1_cal_slope}/{tds1_cal_offset} tds2_cal={tds2_cal_slope}/{tds2_cal_offset} "
             f"pm_en={pm_en} pm_cal=({pm_minv}-{pm_maxv}V→{pm_minb}-{pm_maxb}bar) "
             f"pm_lim={pm_lim} pm_hi={pm_hi}bar p_fdly={p_fdly}s "
             f"pb_en={pb_en} pb_cal=({pb_minv}-{pb_maxv}V→{pb_minb}-{pb_maxb}bar)")


# ============================================================
# IO MAP — capa de abstracción Pin <-> Señal lógica (ver io_catalog.py)
# ============================================================

@api.route("/api/iomap/<device_id>", methods=["GET"])
def get_iomap(device_id):
    rows = db.fetchall(
        "SELECT io_map, features FROM devices WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    stored_io_map, stored_features = rows[0]
    return jsonify({
        "io_map":   io_catalog.merge_io_map(stored_io_map),
        "features": io_catalog.merge_features(stored_features),
        "catalog": {
            "inputs":        io_catalog.LOGICAL_INPUTS,
            "outputs":       io_catalog.LOGICAL_OUTPUTS,
            "features":      list(io_catalog.DEFAULT_FEATURES.keys()),
            "input_labels":  io_catalog.INPUT_LABELS,
            "output_labels": io_catalog.OUTPUT_LABELS,
            "feature_labels": io_catalog.FEATURE_LABELS,
        },
    })


@api.route("/api/iomap/<device_id>", methods=["POST"])
def set_iomap(device_id):
    rows = db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    io_map   = io_catalog.validate_io_map(data.get("io_map") or {})
    features = io_catalog.validate_features(data.get("features") or {})

    db.execute(
        "UPDATE devices SET io_map=%s, features=%s WHERE device_id=%s",
        (json.dumps(io_map), json.dumps(features), device_id),
    )
    _publish_device_iomap(device_id, io_catalog.merge_io_map(io_map))
    alert_manager.fire_event(
        device_id, "IOMAP_UPDATED",
        f"Mapeo de E/S actualizado: {len(io_map['inputs'])} entradas, "
        f"{len(io_map['outputs'])} salidas configuradas",
        cooldown_sec=60,
    )
    return jsonify({"status": "ok"})


def _publish_device_iomap(device_id: str, io_map: dict):
    """Publish retained io_map (Pin<->Señal lógica, catálogo completo) to the
    device via MQTT.

    Retained so the device receives it immediately on reconnect.
    updated_at es el version field — el firmware aplica solo si es mayor al
    actual (mismo patrón que _publish_device_config). Claves de señal
    desconocidas para el firmware son ignoradas; señales ausentes en el
    payload conservan su valor actual en el dispositivo (partial update).
    """
    if not mqtt_client:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({
        "inputs":     io_map["inputs"],
        "outputs":    io_map["outputs"],
        "updated_at": int(_time.time()),
    })
    mqtt_client.publish(f"fyntek/{device_id}/iomap", payload, retain=True)
    log.info(f"[{device_id}] IO map MQTT publicado (retained)")


# ============================================================
# RULES — motor de reglas process_permits[]/independent_outputs[]/fault_rules[]
# (ver rule_catalog.py)
# ============================================================

@api.route("/api/rules/<device_id>", methods=["GET"])
def get_rules(device_id):
    rows = db.fetchall(
        "SELECT rules FROM devices WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    stored_rules, = rows[0]
    return jsonify({
        "rules": rule_catalog.merge_rules(stored_rules),
        "catalog": {
            "processes":           rule_catalog.PROCESSES,
            "independent_outputs": rule_catalog.INDEPENDENT_OUTPUTS,
            "inputs":              io_catalog.LOGICAL_INPUTS,
            "derived_signals":     rule_catalog.DERIVED_SIGNALS,
            "fault_reasons":       rule_catalog.FAULT_REASONS,
            "process_labels":      rule_catalog.PROCESS_LABELS,
            "output_labels":       io_catalog.OUTPUT_LABELS,
            "input_labels":        io_catalog.INPUT_LABELS,
            "derived_labels":      rule_catalog.DERIVED_LABELS,
            "fault_reason_labels": rule_catalog.FAULT_REASON_LABELS,
        },
    })


@api.route("/api/rules/<device_id>", methods=["POST"])
def set_rules(device_id):
    rows = db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    rules = rule_catalog.validate_rules(data.get("rules") or {})

    db.execute(
        "UPDATE devices SET rules=%s WHERE device_id=%s",
        (json.dumps(rules), device_id),
    )
    _publish_device_rules(device_id, rule_catalog.merge_rules(rules))
    alert_manager.fire_event(
        device_id, "RULES_UPDATED",
        f"Motor de reglas actualizado: {len(rules['process_permits'])} process_permits, "
        f"{len(rules['independent_outputs'])} independent_outputs, "
        f"{len(rules['fault_rules'])} fault_rules",
        cooldown_sec=60,
    )
    return jsonify({"status": "ok"})


def _publish_device_rules(device_id: str, rules: dict):
    """Publish retained rules (process_permits/independent_outputs/fault_rules,
    catálogo completo) to the device via MQTT.

    Retained so the device receives it immediately on reconnect.
    updated_at es el version field — el firmware aplica solo si es mayor al
    actual (mismo patrón que _publish_device_iomap). Claves desconocidas para
    el firmware son ignoradas; slots ausentes en el payload conservan su valor
    actual en el dispositivo (partial update).
    """
    if not mqtt_client:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({
        "process_permits":      rules["process_permits"],
        "independent_outputs":  rules["independent_outputs"],
        "fault_rules":          rules["fault_rules"],
        "updated_at":           int(_time.time()),
    })
    mqtt_client.publish(f"fyntek/{device_id}/rules", payload, retain=True)
    log.info(f"[{device_id}] Rules MQTT publicado (retained)")


# ============================================================
# PROCESS CONFIG — parámetros de temporización FSM (ver process_config_catalog.py)
# ============================================================

@api.route("/api/process_config/<device_id>", methods=["GET"])
def get_process_config(device_id):
    rows = db.fetchall(
        "SELECT process_config FROM devices WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    stored, = rows[0]
    cfg, warnings = process_config_catalog.validate_process_config(
        process_config_catalog.merge_process_config(stored)
    )
    return jsonify({
        "process_config": cfg,
        "warnings":       warnings,
        "catalog": {
            "labels": process_config_catalog.PROCESS_CONFIG_LABELS,
            "limits": {k: {"min": v[0], "max": v[1]}
                       for k, v in process_config_catalog.PROCESS_CONFIG_LIMITS.items()},
        },
    })


@api.route("/api/process_config/<device_id>", methods=["POST"])
def set_process_config(device_id):
    rows = db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    cfg, warnings = process_config_catalog.validate_process_config(
        data.get("process_config") or data
    )

    db.execute(
        "UPDATE devices SET process_config=%s WHERE device_id=%s",
        (json.dumps(cfg), device_id),
    )
    _publish_device_process_config(device_id, cfg)
    return jsonify({"status": "ok", "warnings": warnings})


def _publish_device_process_config(device_id: str, cfg: dict):
    if not mqtt_client:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({**cfg, "updated_at": int(_time.time())})
    mqtt_client.publish(f"fyntek/{device_id}/process_config", payload, retain=True)
    log.info(f"[{device_id}] ProcessConfig MQTT publicado (retained)")


# ============================================================
# ANTIFREEZE CONFIG — protección anti-congelamiento opcional (DHT22)
# (ver antifreeze_catalog.py / firmware src/safety/antifreeze.h)
# ============================================================

@api.route("/api/antifreeze_config/<device_id>", methods=["GET"])
def get_antifreeze_config(device_id):
    rows = db.fetchall(
        "SELECT antifreeze_config FROM devices WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    stored, = rows[0]
    cfg, warnings = antifreeze_catalog.validate_antifreeze_config(
        antifreeze_catalog.merge_antifreeze_config(stored)
    )
    return jsonify({
        "antifreeze_config": cfg,
        "warnings":          warnings,
        "catalog": {
            "labels": antifreeze_catalog.ANTIFREEZE_LABELS,
            "limits": {k: {"min": v[0], "max": v[1]}
                       for k, v in antifreeze_catalog.ANTIFREEZE_LIMITS.items()},
        },
    })


@api.route("/api/antifreeze_config/<device_id>", methods=["POST"])
def set_antifreeze_config(device_id):
    rows = db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    cfg, warnings = antifreeze_catalog.validate_antifreeze_config(
        data.get("antifreeze_config") or data
    )

    db.execute(
        "UPDATE devices SET antifreeze_config=%s WHERE device_id=%s",
        (json.dumps(cfg), device_id),
    )
    _publish_device_antifreeze_config(device_id, cfg)
    return jsonify({"status": "ok", "warnings": warnings})


def _publish_device_antifreeze_config(device_id: str, cfg: dict):
    if not mqtt_client:
        return
    import json as _json
    import time as _time
    payload = _json.dumps({**cfg, "updated_at": int(_time.time())})
    mqtt_client.publish(f"fyntek/{device_id}/antifreeze_config", payload, retain=True)
    log.info(f"[{device_id}] AntifreezeConfig MQTT publicado (retained)")


# ============================================================
# WIFI RESET — forzar apertura de portal WiFiManager vía MQTT
# ============================================================

@api.route("/api/wifi/reset/<device_id>", methods=["POST"])
def wifi_reset(device_id):
    if not mqtt_client or not mqtt_client.is_connected():
        return jsonify({"error": "mqtt_not_connected"}), 503
    mqtt_client.publish(f"fyntek/{device_id}/wifi/reset", "{}")
    log.info(f"[{device_id}] WiFi reset solicitado")
    return jsonify({"status": "sent", "device_id": device_id})


# ============================================================
# PROFILE — perfil completo de instalación (io_map + features + rules)
# en una sola operación. Reusa validate_*/merge_*/_publish_device_* — no
# agrega columnas ni tópicos MQTT nuevos.
# ============================================================

@api.route("/api/profile/<device_id>", methods=["GET"])
def get_profile(device_id):
    """Perfil completo actual (io_map + features + rules), mismo formato
    aceptado por POST /api/profile/<device_id> — útil para exportar/backup
    antes de importar un perfil nuevo (ver docs/chamico_lab_config.json)."""
    rows = db.fetchall(
        "SELECT io_map, features, rules FROM devices WHERE device_id=%s",
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    stored_io_map, stored_features, stored_rules = rows[0]
    return jsonify({
        "io_map":   io_catalog.merge_io_map(stored_io_map),
        "features": io_catalog.merge_features(stored_features),
        "rules":    rule_catalog.merge_rules(stored_rules),
    })


@api.route("/api/profile/<device_id>", methods=["POST"])
def import_profile(device_id):
    """Importa un perfil completo (io_map + features + rules) en una sola
    operación — evita 2 POST manuales (/api/iomap + /api/rules). Mismo
    formato que docs/chamico_lab_config.json."""
    rows = db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": "device not found"}), 404

    data = request.get_json(silent=True) or {}
    io_map   = io_catalog.validate_io_map(data.get("io_map") or {})
    features = io_catalog.validate_features(data.get("features") or {})
    rules    = rule_catalog.validate_rules(data.get("rules") or {})

    db.execute(
        "UPDATE devices SET io_map=%s, features=%s, rules=%s WHERE device_id=%s",
        (json.dumps(io_map), json.dumps(features), json.dumps(rules), device_id),
    )
    _publish_device_iomap(device_id, io_catalog.merge_io_map(io_map))
    _publish_device_rules(device_id, rule_catalog.merge_rules(rules))
    alert_manager.fire_event(
        device_id, "PROFILE_IMPORTED",
        f"Perfil de instalación importado: {len(io_map['inputs'])} entradas, "
        f"{len(io_map['outputs'])} salidas, "
        f"{len(rules['process_permits'])} process_permits, "
        f"{len(rules['independent_outputs'])} independent_outputs, "
        f"{len(rules['fault_rules'])} fault_rules",
        cooldown_sec=60,
    )
    return jsonify({"status": "ok"})


@api.route("/api/baseline/<device_id>", methods=["GET"])
def get_baseline(device_id):
    cols = []
    for field in BASELINE_FIELDS:
        cols += [f"{field}_learned", f"{field}_manual", f"{field}_source"]
    cols += ["learned_at", "session_id",
             "efficiency_mean", "efficiency_std",
             "recovery_mean", "recovery_std",
             "flow_permeate_mean", "flow_permeate_std",
             "delta_pressure_mean", "delta_pressure_std"]

    rows = db.fetchall(
        f"SELECT {', '.join(cols)} FROM device_baseline WHERE device_id = %s",
        (device_id,)
    )

    thresholds = []
    stats_data = {}

    if rows:
        row = rows[0]
        for i, field in enumerate(BASELINE_FIELDS):
            learned = row[i * 3]
            manual  = row[i * 3 + 1]
            source  = row[i * 3 + 2] or "learned"
            active  = (manual if source == "manual" and manual is not None else
                       learned if learned is not None else
                       THRESHOLDS[BaselineCache._FALLBACK[field]])
            thresholds.append({
                "field": field, "learned": learned, "manual": manual,
                "source": source, "active_value": active,
                "fallback_used": learned is None and manual is None,
            })
        offset = len(BASELINE_FIELDS) * 3
        stats_data = {
            "learned_at":          str(row[offset]),     "session_id":      row[offset+1],
            "efficiency_mean":     row[offset+2],         "efficiency_std":  row[offset+3],
            "recovery_mean":       row[offset+4],         "recovery_std":    row[offset+5],
            "flow_permeate_mean":      row[offset+6],         "flow_permeate_std":   row[offset+7],
            "delta_pressure_mean": row[offset+8],         "delta_pressure_std": row[offset+9],
        }
    else:
        for field in BASELINE_FIELDS:
            thresholds.append({
                "field": field, "learned": None, "manual": None, "source": "learned",
                "active_value": THRESHOLDS[BaselineCache._FALLBACK[field]], "fallback_used": True,
            })

    return jsonify({"device_id": device_id, "has_baseline": bool(rows),
                    "thresholds": thresholds, "stats": stats_data})

@api.route("/api/baseline/<device_id>", methods=["POST"])
def set_baseline(device_id):
    data = request.json or {}
    rows = db.fetchall("SELECT device_id FROM devices WHERE device_id=%s", (device_id,))
    if not rows:
        return jsonify({"error": f"Device '{device_id}' not found"}), 404

    db.execute(
        "INSERT INTO device_baseline (device_id, learned_at) VALUES (%s, NOW()) "
        "ON CONFLICT (device_id) DO NOTHING",
        (device_id,)
    )

    updated_values  = {}
    updated_sources = {}

    for field in BASELINE_FIELDS:
        if field in data and data[field] is not None:
            val = float(data[field])
            db.execute(
                f"UPDATE device_baseline SET {field}_manual=%s, {field}_source='manual' "
                f"WHERE device_id=%s",
                (val, device_id)
            )
            updated_values[field]  = val
            updated_sources[field] = "manual"

        source_key = f"{field}_source"
        if source_key in data:
            source = data[source_key]
            if source not in ("learned", "manual"):
                return jsonify({"error": "source debe ser 'learned' o 'manual'"}), 400
            db.execute(
                f"UPDATE device_baseline SET {field}_source=%s WHERE device_id=%s",
                (source, device_id)
            )
            updated_sources[field] = source

    if not updated_values and not updated_sources:
        return jsonify({"error": "No se recibió ningún campo válido.",
                        "allowed_fields": BASELINE_FIELDS}), 400

    BaselineCache.invalidate(device_id)
    return jsonify({
        "status": "ok", "device_id": device_id,
        "updated_values": updated_values, "updated_sources": updated_sources,
        "note": "Activo en el próximo ciclo de diagnóstico.",
    })

@api.route("/api/learn/start/<device_id>", methods=["POST"])
def start_learn(device_id):
    duration   = (request.json or {}).get("duration_minutes", 30)
    session_id = learn_engine.start(device_id, duration)
    return jsonify({"status": "started", "session_id": session_id,
                    "duration_minutes": duration})

@api.route("/api/learn/status/<device_id>", methods=["GET"])
def learn_status(device_id):
    if learn_engine.is_active(device_id):
        s = learn_engine._active[device_id]
        elapsed = int(time.time() - s["started_at"])
        return jsonify({
            "status":        "RUNNING",
            "samples":       len(s["samples"]),
            "elapsed_sec":   elapsed,
            "remaining_sec": max(0, s["duration_sec"] - elapsed),
            "progress_pct":  round(elapsed / s["duration_sec"] * 100, 1),
        })
    rows = db.fetchall(
        "SELECT status,samples,finished_at FROM learning_sessions "
        "WHERE device_id=%s ORDER BY started_at DESC LIMIT 1",
        (device_id,)
    )
    if rows:
        return jsonify({"status": rows[0][0], "samples": rows[0][1],
                        "finished_at": str(rows[0][2])})
    return jsonify({"status": "NEVER_RUN"})

@api.route("/api/learn/cancel/<device_id>", methods=["POST"])
def cancel_learn(device_id):
    learn_engine.cancel(device_id)
    return jsonify({"status": "cancelled"})

@api.route("/api/status/<device_id>", methods=["GET"])
def get_status(device_id):
    rows = db.fetchall(
        """
        SELECT state, last_severity, last_diag_code, last_diag_message, last_action,
               flow_permeate_lpm, pressure_membrane_bar, recovery, efficiency,
               last_seen, online,
               health_status, health_code, health_message, health_action, health_updated_at,
               biz_liters_today, biz_target_liters, biz_fulfillment_pct,
               biz_waste_liters_today, biz_waste_pct,
               biz_risk_level, biz_risk_score,
               biz_degradation_pct, biz_degradation_days, biz_degradation_label,
               biz_health_age_hours,
               pressure_brine_bar, pressure_membrane_voltage, pressure_brine_voltage, delta_p_bar
        FROM device_status WHERE device_id=%s
        """,
        (device_id,)
    )
    if not rows:
        return jsonify({"error": "device not found"}), 404
    r = rows[0]
    # Derive online dynamically — the DB flag is never cleared, so it goes stale.
    # ONLINE = last telemetry or heartbeat arrived within 90 seconds.
    last_seen_dt = r[9]
    now_utc = datetime.now(timezone.utc)
    if last_seen_dt is not None:
        if last_seen_dt.tzinfo is None:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
        seconds_ago = int((now_utc - last_seen_dt).total_seconds())
        online = seconds_ago < 90
    else:
        seconds_ago = None
        online = False
    qrow = db.fetchall(
        "SELECT tds_in_ppm, tds_out_ppm FROM telemetry_quality "
        "WHERE device_id=%s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    return jsonify({
        "state": r[0],         "last_severity": r[1],
        "diag_code": r[2],     "diag_message": r[3],   "diag_action": r[4],
        "flow_permeate_lpm": r[5], "pressure": r[6],
        "pressure_brine_bar": r[27],
        "pressure_membrane_voltage": r[28], "pressure_brine_voltage": r[29],
        "delta_p_bar": r[30],
        "recovery": r[7],      "efficiency": r[8],
        "last_seen": str(last_seen_dt) if last_seen_dt else None,
        "online": online,
        "seconds_since_seen": seconds_ago,
        "tds_in_ppm":  qrow[0][0] if qrow else None,
        "tds_out_ppm": qrow[0][1] if qrow else None,
        "health": {
            "status": r[11],   "code": r[12],
            "message": r[13],  "action": r[14],
            "updated_at": str(r[15]),
            "age_hours": r[26],
        },
        "business": {
            "liters_today": r[16],       "target_liters": r[17],
            "fulfillment_pct": r[18],    "waste_liters_today": r[19],
            "waste_pct": r[20],          "risk_level": r[21],
            "risk_score": r[22],         "degradation_pct": r[23],
            "degradation_days": r[24],   "degradation_label": r[25],
        },
    })

@api.route("/api/alerts/<device_id>", methods=["GET"])
def get_alerts(device_id):
    active_only = request.args.get("active", "true").lower() in ("true", "1")
    limit = min(int(request.args.get("limit", "20")), 100)
    where = "device_id=%s AND active=TRUE" if active_only else "device_id=%s"
    rows = db.fetchall(
        f"SELECT id, code, severity, message, active, notification_count, "
        f"       created_at, updated_at, resolved_at, last_notified_at "
        f"FROM alerts WHERE {where} ORDER BY created_at DESC LIMIT %s",
        (device_id, limit)
    )
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        created = r[6]
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_sec = int((now - created).total_seconds()) if created else None
        result.append({
            "id":                 r[0],
            "code":               r[1],
            "severity":           r[2],
            "message":            r[3],
            "active":             r[4],
            "notification_count": r[5],
            "created_at":         created.isoformat() if created else None,
            "updated_at":         r[7].isoformat() if r[7] else None,
            "resolved_at":        r[8].isoformat() if r[8] else None,
            "last_notified_at":   r[9].isoformat() if r[9] else None,
            "age_seconds":        age_sec,
        })
    return jsonify(result)


@api.route("/api/alerts/ack/<int:alert_id>", methods=["POST"])
def ack_alert(alert_id):
    """Acknowledge (resolve) an active alert by ID."""
    db.execute(
        "UPDATE alerts SET active=FALSE, resolved_at=NOW(), updated_at=NOW() "
        "WHERE id=%s AND active=TRUE",
        (alert_id,)
    )
    log.info(f"[ALERT] ACK — id={alert_id}")
    return jsonify({"id": alert_id, "acked": True})


@api.route("/api/alerts/resolve/<device_id>", methods=["POST"])
def resolve_all_alerts(device_id):
    """Resuelve todas las alertas activas de un dispositivo (equivalente al
    botón 'Limpiar alarmas' en la UI — sin enviar comando al equipo)."""
    if not db.fetchall("SELECT 1 FROM devices WHERE device_id=%s", (device_id,)):
        return jsonify({"error": "device_not_found"}), 404
    alert_manager.resolve_all_active(device_id)
    return jsonify({"status": "ok", "device_id": device_id})


@api.route("/api/business/<device_id>", methods=["GET"])
def get_business_history(device_id):
    """Historial de métricas de negocio (últimos 30 días)."""
    days = int(request.args.get("days", 30))
    rows = db.fetchall(
        """
        SELECT day, liters_produced, liters_rejected,
               daily_target_liters, fulfillment_pct, waste_pct,
               avg_efficiency, avg_recovery, risk_level, estimated_cost
        FROM business_metrics
        WHERE device_id=%s AND day >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY day DESC
        """,
        (device_id, days)
    )
    return jsonify([{
        "day": str(r[0]),        "liters_produced": r[1],
        "liters_rejected": r[2], "target": r[3],
        "fulfillment_pct": r[4], "waste_pct": r[5],
        "avg_efficiency": r[6],  "avg_recovery": r[7],
        "risk_level": r[8],      "estimated_cost": r[9],
    } for r in rows])


@api.route("/api/command/<device_id>", methods=["POST"])
def post_command(device_id):
    """
    Issue a remote command to a device.

    Body: {"cmd": "START" | "STOP" | "FLUSH" | "RST"}

    Responses:
      201  {"command_id": "...", "status": "SENT"}
      400  {"error": "unknown_command", "detail": "..."}
      404  {"error": "device_not_found"}
      409  {"error": "command_pending", "command_id": "..."}
      503  {"error": "command_engine_not_ready"}
    """
    if not command_engine:
        return jsonify({"error": "command_engine_not_ready"}), 503

    if not db.fetchall("SELECT 1 FROM devices WHERE device_id = %s", (device_id,)):
        return jsonify({"error": "device_not_found"}), 404

    data = request.json or {}
    cmd  = (data.get("cmd") or "").upper().strip()

    result = command_engine.issue(device_id, cmd, issued_by="api")

    if "error" in result:
        if result["error"] == "command_pending":
            return jsonify(result), 409
        if result["error"] == "unknown_command":
            return jsonify(result), 400
        return jsonify(result), 500

    # RST emitido con éxito → limpiar todas las alertas activas del dispositivo.
    # El operador reconoce el fault; si la condición persiste se re-disparará
    # en el siguiente ciclo de telemetría.
    if cmd == "RST":
        alert_manager.resolve_all_active(device_id)

    return jsonify(result), 201


@api.route("/api/command/<device_id>", methods=["GET"])
def get_commands(device_id):
    """
    Returns the last 20 commands for a device, newest first.
    Includes full lifecycle timestamps for audit and debugging.
    """
    rows = db.fetchall(
        "SELECT command_id, cmd, status, issued_by, "
        "       issued_at, deadline_at, executed_at, rejected_at, "
        "       timeout_at, last_ack_at, reject_reason, retry_count "
        "FROM device_commands WHERE device_id = %s "
        "ORDER BY issued_at DESC LIMIT 20",
        (device_id,)
    )
    return jsonify([
        {
            "command_id":    r[0],
            "cmd":           r[1],
            "status":        r[2],
            "issued_by":     r[3],
            "issued_at":     r[4].isoformat()  if r[4]  else None,
            "deadline_at":   r[5].isoformat()  if r[5]  else None,
            "executed_at":   r[6].isoformat()  if r[6]  else None,
            "rejected_at":   r[7].isoformat()  if r[7]  else None,
            "timeout_at":    r[8].isoformat()  if r[8]  else None,
            "last_ack_at":   r[9].isoformat()  if r[9]  else None,
            "reject_reason": r[10],
            "retry_count":   r[11],
        }
        for r in rows
    ])


# ── AI / External service endpoints (v1) ──────────────────────────────────────

@api.route("/api/v1/device/<device_id>/context", methods=["GET"])
@require_api_key
def get_device_context(device_id):
    """
    Returns a raw snapshot of all firmware data for AI reasoning.

    Delivers uninterpreted data only:
      raw.connectivity  — online status, last_seen, seconds_since_seen
      raw.fsm           — current state, running flag, retry_count
      raw.process       — latest sensor values (flows, pressures, volumes)
      raw.process_window — recent telemetry history (chronological)
      raw.quality       — TDS raw values
      raw.inputs        — digital input states (demand, raw_water_ok, etc.)
      raw.outputs       — relay/valve states
      raw.state_history — last 5 FSM transitions
      commands          — active command + last 5 in history with details

    No backend interpretations, flags, or derived KPIs are included.
    The AI is responsible for all reasoning.

    LOCKDOWN mode blocks this endpoint.
    GET /api/v1/ai/mode always works regardless of gate mode.

    Query params:
      window (int, default=AI_CONTEXT_WINDOW_ROWS, max=60)
    """
    if _ai_gate["mode"] == "LOCKDOWN":
        return jsonify({"error": "lockdown", "detail": "AI access blocked"}), 403

    window = min(int(request.args.get("window", AI_CONTEXT_WINDOW_ROWS)), 60)

    if not db.fetchall("SELECT 1 FROM devices WHERE device_id = %s", (device_id,)):
        return jsonify({"error": "device_not_found"}), 404

    now_utc = datetime.now(timezone.utc)

    st = db.fetchall(
        "SELECT state, online, last_seen FROM device_status WHERE device_id = %s",
        (device_id,)
    )
    dev = db.fetchall(
        "SELECT fw_version FROM devices WHERE device_id = %s", (device_id,)
    )
    connectivity = {}
    fsm_state = "UNKNOWN"
    if st:
        seconds_ago = int((now_utc - st[0][2]).total_seconds()) if st[0][2] else None
        connectivity = {
            "online":             st[0][1],
            "last_seen_utc":      st[0][2].isoformat() if st[0][2] else None,
            "seconds_since_seen": seconds_ago,
        }
        fsm_state = st[0][0] or "UNKNOWN"

    fsm_row = db.fetchall(
        "SELECT state, running, retry_count, time FROM telemetry_state "
        "WHERE device_id = %s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    fsm = {
        "state":           fsm_state,
        "running":         fsm_row[0][1] if fsm_row else None,
        "retry_count":     fsm_row[0][2] if fsm_row else None,
        "state_since_utc": fsm_row[0][3].isoformat() if fsm_row else None,
    }

    proc = db.fetchall(
        "SELECT time, flow_permeate_lpm, flow_reject_lpm, pressure_membrane_bar, "
        "       pressure_brine_bar, volume_permeate_l, volume_reject_l, fw_version "
        "FROM telemetry_process WHERE device_id = %s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    process_latest = None
    if proc:
        r = proc[0]
        process_latest = {
            "sampled_at_utc":        r[0].isoformat(),
            "flow_permeate_lpm":         r[1], "flow_reject_lpm":      r[2],
            "pressure_membrane_bar": r[3], "pressure_brine_bar":    r[4],
            "volume_permeate_l":         r[5], "volume_reject_l":      r[6],
            "fw_version":            r[7],
        }

    wrows = db.fetchall(
        "SELECT time, flow_permeate_lpm, flow_reject_lpm, pressure_membrane_bar, "
        "       pressure_brine_bar, volume_permeate_l, volume_reject_l "
        "FROM telemetry_process WHERE device_id = %s "
        "ORDER BY time DESC LIMIT %s",
        (device_id, window)
    )
    process_window = [
        {
            "ts_utc":                r[0].isoformat(),
            "flow_permeate_lpm":         r[1], "flow_reject_lpm":      r[2],
            "pressure_membrane_bar": r[3], "pressure_brine_bar":    r[4],
            "volume_permeate_l":         r[5], "volume_reject_l":      r[6],
        }
        for r in reversed(wrows)
    ]

    qrow = db.fetchall(
        "SELECT time, tds_in_voltage, tds_out_voltage, tds_in_ppm, tds_out_ppm "
        "FROM telemetry_quality "
        "WHERE device_id = %s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    quality = None
    if qrow:
        quality = {
            "sampled_at_utc":  qrow[0][0].isoformat(),
            "tds_in_voltage":  qrow[0][1],
            "tds_out_voltage": qrow[0][2],
            "tds_in_ppm":      qrow[0][3],
            "tds_out_ppm":     qrow[0][4],
        }

    irow = db.fetchall(
        "SELECT time, demand, raw_water_ok, dose_ok, pressure_switch, feed_tank_level_low, spare2 "
        "FROM telemetry_inputs WHERE device_id = %s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    inputs = None
    if irow:
        inputs = {
            "sampled_at_utc": irow[0][0].isoformat(),
            "demand":   irow[0][1], "raw_water_ok":   irow[0][2],
            "dose_ok":  irow[0][3], "pressure_switch": irow[0][4],
            "feed_tank_level_low": irow[0][5], "spare2":   irow[0][6],
        }

    orow = db.fetchall(
        "SELECT time, pump_low, pump_high, pump_inlet, pump_dose, "
        "       valve_flush, valve_inlet "
        "FROM telemetry_outputs WHERE device_id = %s ORDER BY time DESC LIMIT 1",
        (device_id,)
    )
    outputs = None
    if orow:
        outputs = {
            "sampled_at_utc": orow[0][0].isoformat(),
            "pump_low":    orow[0][1], "pump_high":   orow[0][2],
            "pump_inlet":  orow[0][3], "pump_dose":   orow[0][4],
            "valve_flush": orow[0][5], "valve_inlet": orow[0][6],
        }

    srows = db.fetchall(
        "SELECT time, state, running, retry_count FROM telemetry_state "
        "WHERE device_id = %s ORDER BY time DESC LIMIT 5",
        (device_id,)
    )
    state_history = [
        {"ts_utc": r[0].isoformat(), "state": r[1],
         "running": r[2], "retry_count": r[3]}
        for r in reversed(srows)
    ]

    active_cmd = db.fetchall(
        "SELECT command_id, cmd, status, issued_at, deadline_at "
        "FROM device_commands WHERE device_id = %s "
        "AND status IN ('SENT','RECEIVED','ACCEPTED') LIMIT 1",
        (device_id,)
    )
    cmd_history = db.fetchall(
        "SELECT command_id, cmd, status, issued_at, executed_at, "
        "       rejected_at, timeout_at, reject_reason, details "
        "FROM device_commands WHERE device_id = %s "
        "ORDER BY issued_at DESC LIMIT 5",
        (device_id,)
    )
    commands = {
        "active": {
            "command_id":  active_cmd[0][0],
            "cmd":         active_cmd[0][1],
            "status":      active_cmd[0][2],
            "issued_at":   active_cmd[0][3].isoformat(),
            "deadline_at": active_cmd[0][4].isoformat() if active_cmd[0][4] else None,
        } if active_cmd else None,
        "history": [
            {
                "command_id":    r[0],
                "cmd":           r[1],
                "status":        r[2],
                "issued_at_utc": r[3].isoformat(),
                "executed_at":   r[4].isoformat() if r[4] else None,
                "rejected_at":   r[5].isoformat() if r[5] else None,
                "timeout_at":    r[6].isoformat() if r[6] else None,
                "reject_reason": r[7],
                "details":       r[8],
            }
            for r in cmd_history
        ],
    }

    return jsonify({
        "api_version":       API_VERSION,
        "device_id":         device_id,
        "generated_at_utc":  now_utc.isoformat(),
        "fw_version":        dev[0][0] if dev else None,
        "raw": {
            "connectivity":   connectivity,
            "fsm":            fsm,
            "process":        process_latest,
            "process_window": process_window,
            "quality":        quality,
            "inputs":         inputs,
            "outputs":        outputs,
            "state_history":  state_history,
        },
        "commands": commands,
    })


@api.route("/api/v1/ai/mode", methods=["GET"])
@require_api_key
def get_ai_mode():
    """
    Returns the current AI Control Gate mode.
    Accessible to the AI service (AI_API_KEY) for transparency.
    Always works regardless of gate mode — including LOCKDOWN.

    Modes:
      OBSERVE_ONLY — read allowed, commands blocked (default on startup)
      AUTO_EXECUTE — read and commands allowed
      LOCKDOWN     — read and commands blocked (emergency isolation)
    """
    return jsonify({
        "api_version":  API_VERSION,
        "mode":         _ai_gate["mode"],
        "updated_at":   _ai_gate["updated_at"],
        "updated_by":   _ai_gate["updated_by"],
        "allowed_modes": sorted(AI_GATE_MODES),
        "_note": "State is in-memory. Resets to OBSERVE_ONLY on backend restart.",
    })


@api.route("/api/v1/ai/mode", methods=["POST"])
@require_admin_key
def set_ai_mode():
    """
    Changes the AI Control Gate mode. Requires ADMIN_API_KEY.

    The AI service (AI_API_KEY) cannot call this endpoint — it must
    never be able to modify its own permission level.

    Body: {"mode": "OBSERVE_ONLY" | "AUTO_EXECUTE" | "LOCKDOWN"}

    Every change is logged with previous mode, new mode, and timestamp.

    IN-MEMORY: mode resets to OBSERVE_ONLY on backend restart.
    Re-enabling AUTO_EXECUTE after restart requires explicit admin action.
    """
    data     = request.json or {}
    new_mode = (data.get("mode") or "").upper().strip()

    if new_mode not in AI_GATE_MODES:
        return jsonify({"error": "invalid_mode", "allowed": sorted(AI_GATE_MODES)}), 400

    previous              = _ai_gate["mode"]
    _ai_gate["mode"]      = new_mode
    _ai_gate["updated_at"] = datetime.now(timezone.utc).isoformat()
    _ai_gate["updated_by"] = "admin_api"

    log.info(f"[AI GATE] {previous} → {new_mode}")

    return jsonify({
        "api_version":   API_VERSION,
        "mode":          new_mode,
        "previous_mode": previous,
        "updated_at":    _ai_gate["updated_at"],
    })


@api.route("/api/v1/command/<device_id>", methods=["POST"])
@require_api_key
def post_ai_command(device_id):
    """
    Issues a command from the AI service through the AI Control Gate.

    Body:
      {"cmd": "START|STOP|FLUSH|RST", "reason": "<mandatory explanation>"}

    'reason' is mandatory — every AI action must be auditable.
    Stored in device_commands.details JSONB alongside ai_mode.

    Gate behavior:
      OBSERVE_ONLY → 403 ai_gate_blocked (commands not allowed)
      AUTO_EXECUTE → executes immediately, issued_by="ai"
      LOCKDOWN     → 403 lockdown (all AI access blocked)

    Cooldown: AI_COMMAND_COOLDOWN_SEC between commands per device.
    IN-MEMORY cooldown resets on restart; DB unique index is the hard guard.

    Audit trail stored in device_commands.details:
      {"ai_mode": "...", "reason": "...", "issued_at": "..."}
    """
    mode = _ai_gate["mode"]

    if mode == "LOCKDOWN":
        return jsonify({"error": "lockdown", "detail": "AI access blocked"}), 403

    if mode == "OBSERVE_ONLY":
        return jsonify({
            "error":        "ai_gate_blocked",
            "detail":       "Gate is OBSERVE_ONLY. Commands not allowed.",
            "current_mode": mode,
        }), 403

    # AUTO_EXECUTE from here on
    if not command_engine:
        return jsonify({"error": "command_engine_not_ready"}), 503

    if not db.fetchall("SELECT 1 FROM devices WHERE device_id = %s", (device_id,)):
        return jsonify({"error": "device_not_found"}), 404

    data   = request.json or {}
    cmd    = (data.get("cmd") or "").upper().strip()
    reason = (data.get("reason") or "").strip()

    if not reason:
        return jsonify({
            "error":  "missing_reason",
            "detail": "Field 'reason' is mandatory for AI commands.",
        }), 400

    # Per-device cooldown (in-memory, resets on restart)
    now_ts    = time.time()
    remaining = AI_COMMAND_COOLDOWN_SEC - (now_ts - _ai_cooldown.get(device_id, 0))
    if remaining > 0:
        return jsonify({
            "error":           "cooldown_active",
            "retry_after_sec": round(remaining, 1),
        }), 403

    result = command_engine.issue(device_id, cmd, issued_by="ai")

    if "error" in result:
        if result["error"] == "command_pending":
            return jsonify(result), 409
        if result["error"] == "unknown_command":
            return jsonify(result), 400
        return jsonify(result), 500

    # Audit: persist ai_mode + reason in details JSONB
    db.execute(
        "UPDATE device_commands SET details = %s::jsonb WHERE command_id = %s",
        (
            json.dumps({
                "ai_mode":   mode,
                "reason":    reason,
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }),
            result["command_id"],
        )
    )

    _ai_cooldown[device_id] = now_ts
    log.info(
        f"[AI CMD] {cmd} device={device_id} mode={mode} "
        f"id={result['command_id'][:8]}… reason={reason!r}"
    )

    return jsonify({**result, "ai_mode": mode}), 201


# ── Admin Panel ───────────────────────────────────────────────────────────────

_ADMIN_PANEL_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KAIROX · Admin</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;padding:2rem}
h1{font-size:1.3rem;letter-spacing:.06em;color:#64748b;margin-bottom:1.75rem;text-transform:uppercase}
.card{background:#1e2130;border:1px solid #2d3348;border-radius:8px;padding:1.25rem;margin-bottom:1.25rem}
.card-title{font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;color:#475569;margin-bottom:.85rem}
.badge{display:inline-block;padding:.22rem .65rem;border-radius:999px;font-size:.78rem;font-weight:700}
.bg{background:#14532d;color:#4ade80}.br{background:#7f1d1d;color:#f87171}
.by{background:#713f12;color:#fbbf24}.bb{background:#1e3a5f;color:#60a5fa}
.bg2{background:#1e293b;color:#94a3b8}
.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.meta{font-size:.72rem;color:#475569}
.btn-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem}
button{padding:.7rem;border:none;border-radius:6px;font-size:.88rem;font-weight:700;
  cursor:pointer;letter-spacing:.04em;transition:opacity .12s}
button:hover:not(:disabled){opacity:.82}button:active:not(:disabled){opacity:.65}
button:disabled{opacity:.28;cursor:not-allowed}
.bs{background:#16a34a;color:#fff}.bst{background:#dc2626;color:#fff}
.bf{background:#2563eb;color:#fff}.br2{background:#d97706;color:#fff}
.rbox{background:#0f1117;border:1px solid #2d3348;border-radius:6px;padding:.7rem 1rem;
  font-family:monospace;font-size:.78rem;min-height:2.4rem;white-space:pre-wrap;color:#64748b}
.rbox.ok{border-color:#16a34a;color:#4ade80}.rbox.er{border-color:#dc2626;color:#f87171}
.crow{display:flex;justify-content:space-between;align-items:center;
  padding:.42rem 0;border-bottom:1px solid #1a1f2e;font-size:.78rem;gap:.5rem}
.crow:last-child{border-bottom:none}
select{background:#1e2130;border:1px solid #2d3348;color:#e2e8f0;
  padding:.38rem .7rem;border-radius:6px;font-size:.83rem;margin-bottom:1.25rem}
#ri{font-size:.68rem;color:#334155;margin-left:.5rem}
.id-name{font-size:1.05rem;font-weight:700;color:#e2e8f0}
.id-did{font-size:.72rem;color:#475569;font-family:monospace;margin-top:.25rem}
.id-meta{font-size:.68rem;color:#334155;margin-top:.3rem}
.seen-row{display:flex;gap:1rem;align-items:baseline;margin-top:.55rem;flex-wrap:wrap}
.cfg-grid{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:1rem}
.cfg-section{font-size:.65rem;text-transform:uppercase;letter-spacing:.08em;color:#475569;
  padding-top:.5rem;grid-column:1/-1;border-top:1px solid #1a1f2e}
.cfg-section:first-child{border-top:none;padding-top:0}
.cfg-field label{display:block;font-size:.65rem;color:#475569;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:.3rem}
.cfg-field input{width:100%;background:#0f1117;border:1px solid #2d3348;color:#e2e8f0;
  padding:.45rem .65rem;border-radius:6px;font-size:.85rem}
.cfg-field input:focus{outline:none;border-color:#3b82f6}
.cfg-field.cfg-check{display:flex;flex-direction:row;align-items:center;gap:.5rem}
.cfg-field.cfg-check label{margin-bottom:0;flex:1}
.cfg-field.cfg-check input{width:auto;accent-color:#7c3aed;cursor:pointer}
.hint{font-size:.65rem;color:#334155;margin-top:.6rem}
.alert-row{display:flex;gap:.5rem;align-items:flex-start;
  padding:.42rem 0;border-bottom:1px solid #1a1f2e}
.alert-row:last-child{border-bottom:none}
.alert-body{flex:1;min-width:0}
.alert-code{font-weight:700;font-size:.8rem}
.alert-msg{font-size:.7rem;color:#94a3b8;margin-top:.1rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.alert-time{font-size:.62rem;color:#334155;margin-top:.1rem}
.iomap-table{width:100%;border-collapse:collapse;margin-bottom:1rem;font-size:.78rem}
.iomap-table th{text-align:left;font-size:.62rem;text-transform:uppercase;letter-spacing:.06em;
  color:#475569;padding:.3rem .5rem;border-bottom:1px solid #2d3348}
.iomap-table td{padding:.3rem .5rem;border-bottom:1px solid #1a1f2e;vertical-align:middle}
.iomap-table select{margin-bottom:0;width:100%}
.iomap-table .iomap-chk{text-align:center;width:4.5rem}
.iomap-table .iomap-chk input{accent-color:#7c3aed;cursor:pointer}
</style>
</head>
<body>
<h1>⚙ KAIROX Admin</h1>

<label class="meta">Dispositivo</label><br>
<select id="dev" onchange="onDevChange()">
{% for d in devices %}<option value="{{ d.device_id }}">{{ d.display_name }} · {{ d.device_id }}</option>{% endfor %}
</select>
<span id="ri">—</span>

<div class="card">
  <div class="id-name" id="id-name">{{ devices[0].display_name if devices }}</div>
  <div class="id-did"  id="id-did">{{ devices[0].device_id if devices }}</div>
  <div class="id-meta" id="id-meta">{% if devices and devices[0].installed_at %}Instalado: {{ devices[0].installed_at[:10] }}{% endif %}</div>
</div>

<div class="card">
  <div class="card-title">Estado</div>
  <div class="row">
    <span id="b-online" class="badge bg2">···</span>
    <span id="b-fsm"    class="badge bg2">···</span>
  </div>
  <div class="seen-row">
    <span class="meta" id="b-seen">—</span>
    <span class="meta" id="b-age"></span>
  </div>
  <div class="seen-row" id="pressure-row" style="display:none">
    <span class="meta">P. membrana: <b id="b-pm-bar">—</b> bar (<span id="b-pm-v">—</span> V)</span>
    <span class="meta">P. rechazo: <b id="b-pb-bar">—</b> bar (<span id="b-pb-v">—</span> V)</span>
    <span class="meta">ΔP: <b id="b-dp">—</b> bar</span>
  </div>
</div>

<div class="card">
  <div class="card-title">Alertas activas</div>
  <div id="alerts-list"><span class="meta">Cargando...</span></div>
  <button onclick="resolveAllAlerts()" style="margin-top:.6rem;background:#1e293b;color:#94a3b8;
    border:1px solid #334155;width:100%;padding:.5rem;border-radius:.4rem;cursor:pointer;font-size:.75rem">
    Limpiar alarmas
  </button>
</div>

<div class="card">
  <div class="card-title">Comandos</div>
  <div class="btn-grid">
    <button id="btn-start" class="bs"  onclick="send('START')" disabled>START</button>
    <button id="btn-stop"  class="bst" onclick="send('STOP')"  disabled>STOP</button>
    <button id="btn-flush" class="bf"  onclick="send('FLUSH')" disabled>FLUSH</button>
    <button id="btn-rst"   class="br2" onclick="send('RST')"   disabled>RST</button>
  </div>
  <p class="hint" id="cmd-hint">Esperando estado del dispositivo...</p>
</div>

<div class="card">
  <div class="card-title">Configuración</div>
  <div class="cfg-grid">
    <div class="cfg-section">Calibración sensores</div>
    <div class="cfg-field">
      <label>Factor Q1 (pulsos/L)</label>
      <input type="number" id="ff1" step="1" min="10" max="5000" placeholder="450">
    </div>
    <div class="cfg-field">
      <label>Factor Q2 (pulsos/L)</label>
      <input type="number" id="ff2" step="1" min="10" max="5000" placeholder="450">
    </div>
    <div class="cfg-field">
      <label>Temperatura TDS (°C)</label>
      <input type="number" id="tds-t" step="0.5" min="0" max="80" placeholder="25">
    </div>
    <div class="cfg-section">Calibración TDS (slope=0 → sin calibrar, usa fórmula DFRobot)</div>
    <div class="cfg-field">
      <label>TDS1 slope (ppm/mV)</label>
      <input type="number" id="tds1-cal-sl" step="0.001" min="0" max="10" placeholder="0">
    </div>
    <div class="cfg-field">
      <label>TDS1 offset (ppm)</label>
      <input type="number" id="tds1-cal-of" step="0.1" min="-500" max="500" placeholder="0">
    </div>
    <div class="cfg-field">
      <label>TDS2 slope (ppm/mV)</label>
      <input type="number" id="tds2-cal-sl" step="0.001" min="0" max="10" placeholder="0">
    </div>
    <div class="cfg-field">
      <label>TDS2 offset (ppm)</label>
      <input type="number" id="tds2-cal-of" step="0.1" min="-500" max="500" placeholder="0">
    </div>
    <div class="cfg-section">Protección de caudal permeado</div>
    <div class="cfg-field cfg-check">
      <label>Protección habilitada (FAULT)</label>
      <input type="checkbox" id="flow-prot-en" checked>
    </div>
    <div class="cfg-field">
      <label>Caudal mínimo (L/min)</label>
      <input type="number" id="min-flow" step="0.05" min="0" max="50" placeholder="0.2">
    </div>
    <div class="cfg-field">
      <label>Delay falla caudal (s)</label>
      <input type="number" id="flow-delay" step="5" min="5" max="300" placeholder="30">
    </div>
    <div class="cfg-section">Protección de recovery</div>
    <div class="cfg-field cfg-check">
      <label>Protección habilitada (FAULT)</label>
      <input type="checkbox" id="rec-prot-en" checked>
    </div>
    <div class="cfg-field">
      <label>Recovery mínimo (%)</label>
      <input type="number" id="min-rec" step="1" min="1" max="99" placeholder="10">
    </div>
    <div class="cfg-field">
      <label>Recovery máximo (%)</label>
      <input type="number" id="max-rec" step="1" min="1" max="99" placeholder="85">
    </div>
    <div class="cfg-field">
      <label>Delay falla recovery (s)</label>
      <input type="number" id="rec-delay" step="5" min="5" max="300" placeholder="60">
    </div>
    <div class="cfg-section">Presión — Membrana (P1)</div>
    <div class="cfg-field cfg-check">
      <label>Calibración habilitada</label>
      <input type="checkbox" id="pm-en">
    </div>
    <div class="cfg-field cfg-check">
      <label>Protección alta presión (FAULT)</label>
      <input type="checkbox" id="pm-lim">
    </div>
    <div class="cfg-field">
      <label>Voltaje mínimo (V)</label>
      <input type="number" id="pm-minv" step="0.01" min="0" max="15" placeholder="0.5">
    </div>
    <div class="cfg-field">
      <label>Voltaje máximo (V)</label>
      <input type="number" id="pm-maxv" step="0.01" min="0" max="15" placeholder="4.5">
    </div>
    <div class="cfg-field">
      <label>Presión mínima (bar)</label>
      <input type="number" id="pm-minb" step="0.1" min="0" max="50" placeholder="0">
    </div>
    <div class="cfg-field">
      <label>Presión máxima (bar)</label>
      <input type="number" id="pm-maxb" step="0.1" min="0" max="50" placeholder="14">
    </div>
    <div class="cfg-field">
      <label>Límite alta presión (bar)</label>
      <input type="number" id="pm-hi" step="0.1" min="0" max="50" placeholder="12">
    </div>
    <div class="cfg-field">
      <label>Delay falla presión (s)</label>
      <input type="number" id="p-fdly" step="1" min="0" max="300" placeholder="3">
    </div>
    <div class="cfg-section">Presión — Rechazo (P2)</div>
    <div class="cfg-field cfg-check">
      <label>Calibración habilitada</label>
      <input type="checkbox" id="pb-en">
    </div>
    <div class="cfg-field">
      <label>Voltaje mínimo (V)</label>
      <input type="number" id="pb-minv" step="0.01" min="0" max="15" placeholder="0.5">
    </div>
    <div class="cfg-field">
      <label>Voltaje máximo (V)</label>
      <input type="number" id="pb-maxv" step="0.01" min="0" max="15" placeholder="4.5">
    </div>
    <div class="cfg-field">
      <label>Presión mínima (bar)</label>
      <input type="number" id="pb-minb" step="0.1" min="0" max="50" placeholder="0">
    </div>
    <div class="cfg-field">
      <label>Presión máxima (bar)</label>
      <input type="number" id="pb-maxb" step="0.1" min="0" max="50" placeholder="14">
    </div>
    <div class="cfg-section">Alarmas de presión (diagnóstico, no detiene el equipo)</div>
    <div class="cfg-field cfg-check">
      <label>Alarma presión rechazo alta</label>
      <input type="checkbox" id="pb-alarm-en">
    </div>
    <div class="cfg-field">
      <label>Límite alarma rechazo (bar)</label>
      <input type="number" id="pb-hi" step="0.1" min="0" max="50" placeholder="8">
    </div>
    <div class="cfg-field cfg-check">
      <label>Alarma ΔP elevado</label>
      <input type="checkbox" id="dp-alarm-en">
    </div>
    <div class="cfg-field">
      <label>Límite alarma ΔP (bar)</label>
      <input type="number" id="dp-alarm-lim" step="0.1" min="0" max="50" placeholder="5">
    </div>
    <div class="cfg-section">KPIs operacionales</div>
    <div class="cfg-field">
      <label>Potencia bomba (kW)</label>
      <input type="number" id="pump-kw" step="0.01" min="0" max="50" placeholder="0.75">
    </div>
    <div class="cfg-field">
      <label>Costo energía ($/kWh)</label>
      <input type="number" id="cost-kwh" step="0.01" min="0" placeholder="0.12">
    </div>
    <div class="cfg-field">
      <label>Meta diaria (L)</label>
      <input type="number" id="daily-l" step="10" min="0" placeholder="0">
    </div>
  </div>
  <button onclick="saveConfig()" style="background:#7c3aed;color:#fff;width:100%;padding:.75rem">
    Guardar configuración
  </button>
</div>

<div class="card">
  <div class="card-title">Tiempos de proceso FSM</div>
  <p class="hint">Parámetros de temporización del ciclo de OI. Se aplican en runtime sin reiniciar
  el equipo — requiere FW ≥ 1.2.0. Los defaults reproducen el comportamiento anterior.</p>
  <div class="cfg-section">Arranque</div>
  <div class="cfg-field">
    <label>Espera baja→alta presión [s]</label>
    <input type="number" id="pc-stab" step="1" min="0" max="300" placeholder="10">
  </div>
  <div class="cfg-field">
    <label>Timeout verificación presostato [s]</label>
    <input type="number" id="pc-tout" step="1" min="1" max="300" placeholder="5">
  </div>
  <div class="cfg-section">Reintentos</div>
  <div class="cfg-field">
    <label>Espera entre reintentos [s]</label>
    <input type="number" id="pc-retry" step="1" min="0" max="600" placeholder="10">
  </div>
  <div class="cfg-field">
    <label>Reintentos máx. antes de FAULT</label>
    <input type="number" id="pc-maxr" step="1" min="1" max="20" placeholder="5">
  </div>
  <div class="cfg-section">Flush</div>
  <div class="cfg-field">
    <label>Duración ciclo de flush [s]</label>
    <input type="number" id="pc-flush" step="1" min="1" max="600" placeholder="60">
  </div>
  <button onclick="saveProcessConfig()" style="background:#7c3aed;color:#fff;width:100%;padding:.75rem">
    Guardar tiempos de proceso
  </button>
</div>

<div class="card">
  <div class="card-title">Anti-congelamiento (opcional)</div>
  <p class="hint">Hace circular agua de pozo cuando la temperatura ambiente cae bajo el umbral,
  para evitar congelamiento de agua estancada con el equipo detenido. Requiere sensor DHT22
  cableado y FW ≥ 2.1.0. Deshabilitado por defecto — sin sensor, no tiene efecto.</p>
  <div class="cfg-field cfg-check">
    <label>Protección habilitada</label>
    <input type="checkbox" id="af-enabled">
  </div>
  <div class="cfg-field cfg-check">
    <label>Sensor DHT22 habilitado</label>
    <input type="checkbox" id="af-sensor-enabled">
  </div>
  <div class="cfg-field">
    <label>GPIO del sensor</label>
    <input type="number" id="af-gpio" step="1" min="0" max="39" placeholder="21">
  </div>
  <div class="cfg-section">Histéresis</div>
  <div class="cfg-field">
    <label>Umbral de riesgo [°C] (activa)</label>
    <input type="number" id="af-thr-low" step="0.5" placeholder="0">
  </div>
  <div class="cfg-field">
    <label>Umbral de recuperación [°C] (desactiva)</label>
    <input type="number" id="af-thr-high" step="0.5" placeholder="3">
  </div>
  <div class="cfg-section">Cadencia</div>
  <div class="cfg-field">
    <label>Duración del ciclo [s]</label>
    <input type="number" id="af-flush-dur" step="10" min="10" max="3600" placeholder="300">
  </div>
  <div class="cfg-field">
    <label>Intervalo entre evaluaciones [s]</label>
    <input type="number" id="af-eval-int" step="60" min="60" max="86400" placeholder="3600">
  </div>
  <div class="cfg-field">
    <label>Inhibición post-arranque [s]</label>
    <input type="number" id="af-boot-inhibit" step="10" min="0" max="3600" placeholder="120">
  </div>
  <div class="cfg-section">Validación del sensor</div>
  <div class="cfg-field">
    <label>Temperatura válida mínima [°C]</label>
    <input type="number" id="af-min-valid" step="1" placeholder="-40">
  </div>
  <div class="cfg-field">
    <label>Temperatura válida máxima [°C]</label>
    <input type="number" id="af-max-valid" step="1" placeholder="60">
  </div>
  <div class="cfg-field">
    <label>Fallos consecutivos -&gt; sensor_fault</label>
    <input type="number" id="af-max-fail" step="1" min="1" max="20" placeholder="5">
  </div>
  <button onclick="saveAntifreezeConfig()" style="background:#7c3aed;color:#fff;width:100%;padding:.75rem">
    Guardar anti-congelamiento
  </button>
</div>

<div class="card">
  <div class="card-title">Mapeo de E/S (avanzado)</div>
  <p class="hint">Asigna pines físicos a señales lógicas y habilita features del equipo.
  No cambia el comportamiento actual del equipo — requiere FW ≥ 1.1.5 para sincronizar.</p>
  <div class="cfg-section">Entradas digitales</div>
  <table class="iomap-table">
    <thead><tr><th>Señal</th><th>GPIO</th><th>Modo</th><th>Invertir</th><th>Debounce [s]</th></tr></thead>
    <tbody id="iomap-inputs"></tbody>
  </table>
  <div class="cfg-section">Salidas digitales</div>
  <table class="iomap-table">
    <thead><tr><th>Señal</th><th>GPIO</th><th>Invertir</th></tr></thead>
    <tbody id="iomap-outputs"></tbody>
  </table>
  <div class="cfg-section">Features del dispositivo</div>
  <div class="cfg-grid" id="iomap-features"></div>
  <button onclick="saveIomap()" style="background:#7c3aed;color:#fff;width:100%;padding:.75rem">
    Guardar mapeo de E/S
  </button>
</div>

<div class="card">
  <div class="card-title">Motor de reglas (avanzado)</div>
  <p class="hint">process_permits / independent_outputs / fault_rules — editor JSON.
  Requiere FW ≥ 1.1.6 para sincronizar. Ver catálogo de señales abajo.</p>
  <pre class="hint" id="rules-catalog" style="white-space:pre-wrap"></pre>
  <textarea id="rules-json" rows="16" style="width:100%;font-family:monospace;font-size:.78rem;
    background:#0d1117;color:#c9d1d9;border:1px solid #1a1f2e;border-radius:.4rem;padding:.5rem"></textarea>
  <button onclick="saveRules()" style="background:#7c3aed;color:#fff;width:100%;padding:.75rem;margin-top:.5rem">
    Guardar reglas
  </button>
</div>

<div class="card">
  <div class="card-title">Perfil de instalación (io_map + features + rules)</div>
  <p class="hint">Importa/exporta el perfil completo en una sola operación
  (1 POST en vez de 2). Mismo formato que docs/chamico_lab_config.json.
  Requiere FW ≥ 1.1.7.</p>
  <textarea id="profile-json" rows="16" style="width:100%;font-family:monospace;font-size:.78rem;
    background:#0d1117;color:#c9d1d9;border:1px solid #1a1f2e;border-radius:.4rem;padding:.5rem"></textarea>
  <div style="display:flex;gap:.5rem;margin-top:.5rem">
    <button onclick="exportProfile()" style="background:#30363d;color:#fff;flex:1;padding:.75rem">
      Exportar actual
    </button>
    <button onclick="importProfile()" style="background:#7c3aed;color:#fff;flex:1;padding:.75rem">
      Importar perfil
    </button>
  </div>
</div>

<div class="card">
  <div class="card-title">Respuesta</div>
  <div class="rbox" id="resp">—</div>
</div>

<div class="card">
  <div class="card-title">Últimos comandos</div>
  <div id="hist"><span class="meta">Cargando...</span></div>
</div>

<div class="card">
  <div class="card-title">Integración IA</div>
  <div class="row" style="margin-bottom:.75rem">
    <span class="meta">Modo actual:</span>
    <span id="ai-mode-badge" class="badge bg2">···</span>
  </div>
  <div class="row" style="gap:.5rem;margin-bottom:.6rem">
    <select id="ai-select" style="flex:1">
      <option value="OFF">OFF — deshabilitado</option>
      <option value="VIEWER">VIEWER — solo observa</option>
      <option value="AUTO">AUTO — ejecuta automáticamente</option>
    </select>
    <button onclick="saveAiMode()" style="background:#7c3aed;color:#fff;padding:.5rem 1rem">Guardar</button>
  </div>
  <div class="card-title" style="margin-top:.75rem">Última decisión IA</div>
  <div id="ai-last"><span class="meta">Cargando...</span></div>
</div>

<script>
const DEVICES = {
{% for d in devices %}"{{ d.device_id }}": {"name": "{{ d.display_name }}", "installed": "{{ d.installed_at }}"},
{% endfor %}};

const FSM_COLOR = {
  PRODUCING:'bg', FLUSHING:'bb', STARTING:'by',
  FAULT:'br',     IDLE:'bg2',    STOPPING:'bg2', UNKNOWN:'bg2'
};

// Commands allowed per FSM state — frontend hint only; backend+firmware enforce.
const CMD_STATES = {
  START: ['IDLE'],
  STOP:  ['PRODUCING', 'STARTING', 'FLUSHING'],
  FLUSH: ['PRODUCING'],
  RST:   ['FAULT', 'STARTING', 'FLUSHING'],
};

const CMD_HINT = {
  IDLE:      'START disponible',
  PRODUCING: 'STOP · FLUSH disponibles',
  STARTING:  'STOP · RST disponibles',
  FLUSHING:  'STOP · RST disponibles',
  STOPPING:  'Esperando IDLE...',
  FAULT:     'RST disponible',
  UNKNOWN:   'Sin estado confirmado',
};

const STATUS_COLOR = {
  EXECUTED:'#4ade80', REJECTED:'#f87171',
  TIMEOUT:'#fbbf24',  SENT:'#94a3b8'
};

let currentFsm = 'UNKNOWN';

function dev(){ return document.getElementById('dev').value; }

function updateButtons(){
  const f = currentFsm;
  document.getElementById('btn-start').disabled = !CMD_STATES.START.includes(f);
  document.getElementById('btn-stop').disabled  = !CMD_STATES.STOP.includes(f);
  document.getElementById('btn-flush').disabled = !CMD_STATES.FLUSH.includes(f);
  document.getElementById('btn-rst').disabled   = !CMD_STATES.RST.includes(f);
  document.getElementById('cmd-hint').textContent = CMD_HINT[f] || '—';
}

function onDevChange(){
  const d = dev();
  const info = DEVICES[d] || {};
  document.getElementById('id-name').textContent = info.name || d;
  document.getElementById('id-did').textContent  = d;
  const ia = info.installed || '';
  document.getElementById('id-meta').textContent = ia ? 'Instalado: ' + ia.slice(0,10) : '';
  currentFsm = 'UNKNOWN';
  updateButtons();
  poll();
  pollAi();
  loadConfig();
  loadIomap();
  loadRules();
  fetchAlerts();
}

function fmtAge(sec){
  if(sec == null) return '';
  if(sec < 60)   return sec+'s';
  if(sec < 3600) return Math.floor(sec/60)+'m '+( sec%60)+'s';
  return Math.floor(sec/3600)+'h '+Math.floor((sec%3600)/60)+'m';
}

async function ackAlert(id){
  try {
    await fetch('/api/alerts/ack/'+id, {method:'POST'});
    fetchAlerts();
  } catch(e){}
}

async function resolveAllAlerts(){
  await fetch('/api/alerts/resolve/'+dev(), {method:'POST'});
  fetchAlerts();
}

async function fetchAlerts(){
  try {
    const r = await fetch('/api/alerts/'+dev()+'?active=false&limit=15');
    if(!r.ok) return;
    const alerts = await r.json();
    const el = document.getElementById('alerts-list');
    if(!alerts.length){
      el.innerHTML = '<span class="meta">Sin alertas recientes</span>';
      return;
    }
    const SEV_CLS   = {CRITICAL:'br', WARNING:'by', INFO:'bb'};
    const SEV_ICONS = {CRITICAL:'🔴', WARNING:'⚠', INFO:'ℹ'};
    el.innerHTML = alerts.map(a => {
      const cls  = SEV_CLS[a.severity]   || 'bg2';
      const icon = SEV_ICONS[a.severity] || '•';
      const ts   = a.created_at ? a.created_at.replace('T',' ').slice(0,16)+' UTC' : '—';
      const age  = a.active ? fmtAge(a.age_seconds) : '';
      const msg  = a.message.length > 90 ? a.message.slice(0,90)+'…' : a.message;
      const notif = a.notification_count > 0 ? ' · '+a.notification_count+'× notif.' : '';
      const statusBadge = a.active
        ? '<span class="badge br" style="font-size:0.7rem;padding:1px 6px">ACTIVA</span>'
        : '<span class="badge bg2" style="font-size:0.7rem;padding:1px 6px">RESUELTA</span>';
      const ackBtn = a.active
        ? '<button onclick="ackAlert('+a.id+')" style="font-size:0.72rem;padding:2px 8px;border:1px solid #475569;background:#1e293b;color:#94a3b8;border-radius:4px;cursor:pointer">ACK</button>'
        : '';
      return '<div class="alert-row" style="align-items:flex-start;gap:8px">'
        +'<span class="badge '+cls+'" style="min-width:4.5rem;text-align:center;flex-shrink:0;font-size:0.75rem">'+icon+' '+a.severity+'</span>'
        +'<div class="alert-body" style="flex:1;min-width:0">'
        +'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
        +'<span class="alert-code">'+a.code+'</span>'+statusBadge
        +(age ? '<span class="meta" style="font-size:0.72rem">'+age+'</span>' : '')
        +'</div>'
        +'<div class="alert-msg">'+msg+'</div>'
        +'<div class="alert-time">'+ts+notif+'</div>'
        +'</div>'
        +(ackBtn ? '<div style="flex-shrink:0">'+ackBtn+'</div>' : '')
        +'</div>';
    }).join('');
  } catch(e){}
}

async function poll(){
  const d = dev();
  try {
    const r = await fetch('/api/status/'+d);
    if(r.ok){
      const s   = await r.json();
      const fsm = s.state || 'UNKNOWN';
      currentFsm = fsm;

      const ob = document.getElementById('b-online');
      ob.textContent = s.online ? 'ONLINE' : 'OFFLINE';
      ob.className   = 'badge '+(s.online ? 'bg' : 'br');

      const fb = document.getElementById('b-fsm');
      fb.textContent = fsm;
      fb.className   = 'badge '+(FSM_COLOR[fsm]||'bg2');

      const seenEl = document.getElementById('b-seen');
      const ageEl  = document.getElementById('b-age');
      if(s.last_seen && s.last_seen !== 'None'){
        seenEl.textContent = 'Último contacto: ' + s.last_seen.replace('T',' ').slice(0,19) + ' UTC';
        const sec = s.seconds_since_seen;
        ageEl.textContent  = sec != null ? '(' + sec + 's)' : '';
      } else {
        seenEl.textContent = 'Sin contacto registrado';
        ageEl.textContent  = '';
      }

      const pRow = document.getElementById('pressure-row');
      if(s.pressure_membrane_bar != null || s.pressure_brine_bar != null){
        pRow.style.display = '';
        document.getElementById('b-pm-bar').textContent = s.pressure_membrane_bar != null ? s.pressure_membrane_bar.toFixed(2) : '—';
        document.getElementById('b-pm-v').textContent   = s.pressure_membrane_voltage != null ? s.pressure_membrane_voltage.toFixed(2) : '—';
        document.getElementById('b-pb-bar').textContent = s.pressure_brine_bar != null ? s.pressure_brine_bar.toFixed(2) : '—';
        document.getElementById('b-pb-v').textContent   = s.pressure_brine_voltage != null ? s.pressure_brine_voltage.toFixed(2) : '—';
        document.getElementById('b-dp').textContent     = s.delta_p_bar != null ? s.delta_p_bar.toFixed(2) : '—';
      } else {
        pRow.style.display = 'none';
      }

      updateButtons();
    }
  } catch(e){}

  try {
    const r = await fetch('/api/command/'+d);
    if(r.ok){
      const cmds = await r.json();
      const el   = document.getElementById('hist');
      if(!cmds.length){
        el.innerHTML = '<span class="meta">Sin comandos registrados</span>';
      } else {
        el.innerHTML = cmds.slice(0,5).map(c => {
          const ts  = (c.issued_at||'').replace('T',' ').slice(0,19);
          const sc  = STATUS_COLOR[c.status]||'#94a3b8';
          const rej = c.reject_reason
            ? '<span class="meta" style="color:#f87171">'+c.reject_reason+'</span>' : '';
          return '<div class="crow">'
            +'<span style="font-weight:700">'+c.cmd+'</span>'
            +'<span style="color:'+sc+';font-weight:700">'+c.status+'</span>'
            +'<span class="meta">'+ts+'</span>'
            +rej+'</div>';
        }).join('');
      }
    }
  } catch(e){}

  document.getElementById('ri').textContent = '↻ '+new Date().toLocaleTimeString();
  fetchAlerts();
}

async function send(cmd){
  // Disable all command buttons during send
  ['btn-start','btn-stop','btn-flush','btn-rst'].forEach(id =>
    document.getElementById(id).disabled = true
  );
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Enviando '+cmd+'...';
  try {
    const r = await fetch('/api/command/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cmd})
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
    if(r.ok) setTimeout(poll, 1500);
  } catch(e){
    box.className   = 'rbox er';
    box.textContent = 'Error de red: '+e.message;
  } finally {
    updateButtons();
  }
}

async function loadConfig(){
  try {
    const r = await fetch('/api/config/'+dev());
    if(!r.ok) return;
    const c = await r.json();
    document.getElementById('ff1').value       = c.flow_factor_1            ?? '';
    document.getElementById('ff2').value       = c.flow_factor_2            ?? '';
    document.getElementById('tds-t').value     = c.tds_temperature          ?? '';
    document.getElementById('tds1-cal-sl').value = c.tds1_cal_slope         ?? '';
    document.getElementById('tds1-cal-of').value = c.tds1_cal_offset        ?? '';
    document.getElementById('tds2-cal-sl').value = c.tds2_cal_slope         ?? '';
    document.getElementById('tds2-cal-of').value = c.tds2_cal_offset        ?? '';
    document.getElementById('flow-prot-en').checked = c.flow_protection_enabled !== false;
    document.getElementById('min-flow').value  = c.min_flow_lpm             ?? '';
    document.getElementById('flow-delay').value= c.flow_fault_delay_sec     ?? '';
    document.getElementById('rec-prot-en').checked  = c.recovery_protection_enabled !== false;
    document.getElementById('min-rec').value   = c.min_recovery_pct         ?? '';
    document.getElementById('max-rec').value   = c.max_recovery_pct         ?? '';
    document.getElementById('rec-delay').value = c.recovery_fault_delay_sec ?? '';
    document.getElementById('pump-kw').value  = c.pump_power_kw       ?? '';
    document.getElementById('cost-kwh').value = c.cost_kwh            ?? '';
    document.getElementById('daily-l').value  = c.daily_target_liters ?? '';
    document.getElementById('pm-en').checked   = !!c.pressure_membrane_enabled;
    document.getElementById('pm-minv').value   = c.pressure_membrane_min_voltage   ?? '';
    document.getElementById('pm-maxv').value   = c.pressure_membrane_max_voltage   ?? '';
    document.getElementById('pm-minb').value   = c.pressure_membrane_min_bar       ?? '';
    document.getElementById('pm-maxb').value   = c.pressure_membrane_max_bar       ?? '';
    document.getElementById('pm-lim').checked  = !!c.pressure_membrane_limits_enabled;
    document.getElementById('pm-hi').value     = c.pressure_membrane_high_limit    ?? '';
    document.getElementById('p-fdly').value    = c.pressure_fault_delay_sec        ?? '';
    document.getElementById('pb-en').checked   = !!c.pressure_brine_enabled;
    document.getElementById('pb-minv').value   = c.pressure_brine_min_voltage      ?? '';
    document.getElementById('pb-maxv').value   = c.pressure_brine_max_voltage      ?? '';
    document.getElementById('pb-minb').value   = c.pressure_brine_min_bar          ?? '';
    document.getElementById('pb-maxb').value   = c.pressure_brine_max_bar          ?? '';
    document.getElementById('pb-hi').value     = c.pressure_brine_high_limit       ?? '';
    document.getElementById('pb-alarm-en').checked = !!c.pressure_brine_alarm_enabled;
    document.getElementById('dp-alarm-en').checked = !!c.delta_p_alarm_enabled;
    document.getElementById('dp-alarm-lim').value   = c.delta_p_alarm_limit        ?? '';
  } catch(e){}
}

async function saveConfig(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Guardando configuración...';
  const payload = {
    flow_factor_1:            parseFloat(document.getElementById('ff1').value)        || 450,
    flow_factor_2:            parseFloat(document.getElementById('ff2').value)        || 450,
    tds_temperature:          parseFloat(document.getElementById('tds-t').value)      || 25,
    tds1_cal_slope:           parseFloat(document.getElementById('tds1-cal-sl').value) || 0,
    tds1_cal_offset:          parseFloat(document.getElementById('tds1-cal-of').value) || 0,
    tds2_cal_slope:           parseFloat(document.getElementById('tds2-cal-sl').value) || 0,
    tds2_cal_offset:          parseFloat(document.getElementById('tds2-cal-of').value) || 0,
    flow_protection_enabled:  document.getElementById('flow-prot-en').checked,
    min_flow_lpm:             parseFloat(document.getElementById('min-flow').value)   || 0.2,
    flow_fault_delay_sec:     parseInt(document.getElementById('flow-delay').value)   || 30,
    recovery_protection_enabled: document.getElementById('rec-prot-en').checked,
    min_recovery_pct:         parseFloat(document.getElementById('min-rec').value)    || 10,
    max_recovery_pct:         parseFloat(document.getElementById('max-rec').value)    || 85,
    recovery_fault_delay_sec: parseInt(document.getElementById('rec-delay').value)    || 60,
    pump_power_kw:            parseFloat(document.getElementById('pump-kw').value)    || 0.75,
    cost_kwh:            parseFloat(document.getElementById('cost-kwh').value) || 0.12,
    daily_target_liters: parseFloat(document.getElementById('daily-l').value)  || 0,
    pressure_membrane_enabled:        document.getElementById('pm-en').checked,
    pressure_membrane_min_voltage:    parseFloat(document.getElementById('pm-minv').value) || 0.5,
    pressure_membrane_max_voltage:    parseFloat(document.getElementById('pm-maxv').value) || 4.5,
    pressure_membrane_min_bar:        parseFloat(document.getElementById('pm-minb').value) || 0,
    pressure_membrane_max_bar:        parseFloat(document.getElementById('pm-maxb').value) || 14,
    pressure_membrane_limits_enabled: document.getElementById('pm-lim').checked,
    pressure_membrane_high_limit:     parseFloat(document.getElementById('pm-hi').value)   || 12,
    pressure_fault_delay_sec:         parseInt(document.getElementById('p-fdly').value)    || 3,
    pressure_brine_enabled:           document.getElementById('pb-en').checked,
    pressure_brine_min_voltage:       parseFloat(document.getElementById('pb-minv').value) || 0.5,
    pressure_brine_max_voltage:       parseFloat(document.getElementById('pb-maxv').value) || 4.5,
    pressure_brine_min_bar:           parseFloat(document.getElementById('pb-minb').value) || 0,
    pressure_brine_max_bar:           parseFloat(document.getElementById('pb-maxb').value) || 14,
    pressure_brine_high_limit:        parseFloat(document.getElementById('pb-hi').value)   || 8,
    pressure_brine_alarm_enabled:     document.getElementById('pb-alarm-en').checked,
    delta_p_alarm_enabled:            document.getElementById('dp-alarm-en').checked,
    delta_p_alarm_limit:              parseFloat(document.getElementById('dp-alarm-lim').value) || 5,
  };
  try {
    const r = await fetch('/api/config/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
  } catch(e){
    box.className   = 'rbox er';
    box.textContent = 'Error: '+e.message;
  }
}

let IOMAP_CATALOG = null;

// Etiquetas de hardware (wiring fijo D1-D6/R1-R6/Q1-Q2/AIN0-AIN1/TDS1-TDS2,
// ver firmware/Ro4.0-V1/include/config.h) — solo informativo en la UI.
const GPIO_LABELS = {
  4:'R1', 16:'R2', 17:'R3', 18:'R4', 19:'R5', 2:'R6',
  27:'D1', 26:'D2', 25:'D3', 33:'D4', 32:'D5', 23:'D6',
  14:'Q1', 13:'Q2', 36:'AIN0', 39:'AIN1', 34:'TDS1', 35:'TDS2',
};

function gpioOptionsHtml(selected){
  let html = '<option value=""'+(selected==null?' selected':'')+'>— sin asignar —</option>';
  for(let g=0; g<=39; g++){
    const label = GPIO_LABELS[g] ? ' ('+GPIO_LABELS[g]+')' : '';
    html += '<option value="'+g+'"'+(selected===g?' selected':'')+'>GPIO '+g+label+'</option>';
  }
  return html;
}

// Para salidas: solo R1-R6 (los relés disponibles en la placa).
// Los valores siguen siendo GPIOs numéricos — la UI solo simplifica la selección.
const RELAY_GPIO = {R1:4, R2:16, R3:17, R4:18, R5:19, R6:2};
function relayOptionsHtml(selected){
  let html = '<option value=""'+(selected==null?' selected':'')+'>— sin asignar —</option>';
  for(const [name, gpio] of Object.entries(RELAY_GPIO)){
    html += '<option value="'+gpio+'"'+(selected===gpio?' selected':'')+'>'+name+'</option>';
  }
  return html;
}

async function loadIomap(){
  try {
    const r = await fetch('/api/iomap/'+dev());
    if(!r.ok) return;
    const c = await r.json();
    IOMAP_CATALOG = c.catalog;

    const inBody = document.getElementById('iomap-inputs');
    inBody.innerHTML = '';
    for(const sig of c.catalog.inputs){
      const e = c.io_map.inputs[sig] || {};
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>'+(c.catalog.input_labels[sig]||sig)+'</td>'+
        '<td><select id="io-in-'+sig+'-gpio">'+gpioOptionsHtml(e.gpio)+'</select></td>'+
        '<td><select id="io-in-'+sig+'-mode">'+
          '<option value="pullup"'+(e.mode==='pulldown'?'':' selected')+'>Pull-up</option>'+
          '<option value="pulldown"'+(e.mode==='pulldown'?' selected':'')+'>Pull-down</option>'+
        '</select></td>'+
        '<td class="iomap-chk"><input type="checkbox" id="io-in-'+sig+'-inv"'+(e.invert?' checked':'')+'></td>'+
        '<td><input type="number" id="io-in-'+sig+'-deb" min="0" max="60" step="0.1" style="width:4.5rem;text-align:right" value="'+((e.debounce_ms||0)/1000)+'"></td>';
      inBody.appendChild(tr);
    }

    const outBody = document.getElementById('iomap-outputs');
    outBody.innerHTML = '';
    for(const sig of c.catalog.outputs){
      const e = c.io_map.outputs[sig] || {};
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>'+(c.catalog.output_labels[sig]||sig)+'</td>'+
        '<td><select id="io-out-'+sig+'-gpio">'+relayOptionsHtml(e.gpio)+'</select></td>'+
        '<td class="iomap-chk"><input type="checkbox" id="io-out-'+sig+'-inv"'+(e.invert?' checked':'')+'></td>';
      outBody.appendChild(tr);
    }

    const featDiv = document.getElementById('iomap-features');
    featDiv.innerHTML = '';
    for(const f of c.catalog.features){
      const div = document.createElement('div');
      div.className = 'cfg-field cfg-check';
      div.innerHTML =
        '<label>'+(c.catalog.feature_labels[f]||f)+'</label>'+
        '<input type="checkbox" id="io-feat-'+f+'"'+(c.features[f]?' checked':'')+'>';
      featDiv.appendChild(div);
    }
  } catch(e){}
}

async function saveIomap(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Guardando mapeo de E/S...';
  if(!IOMAP_CATALOG){ box.className='rbox er'; box.textContent='Catálogo no cargado'; return; }

  const io_map = {inputs:{}, outputs:{}};
  for(const sig of IOMAP_CATALOG.inputs){
    const g = document.getElementById('io-in-'+sig+'-gpio').value;
    const deb = document.getElementById('io-in-'+sig+'-deb').value;
    io_map.inputs[sig] = {
      gpio:        g===''? null : parseInt(g),
      mode:        document.getElementById('io-in-'+sig+'-mode').value,
      invert:      document.getElementById('io-in-'+sig+'-inv').checked ? 1 : 0,
      debounce_ms: Math.round((parseFloat(deb)||0)*1000),
    };
  }
  for(const sig of IOMAP_CATALOG.outputs){
    const g = document.getElementById('io-out-'+sig+'-gpio').value;
    io_map.outputs[sig] = {
      gpio:   g===''? null : parseInt(g),
      invert: document.getElementById('io-out-'+sig+'-inv').checked ? 1 : 0,
    };
  }
  const features = {};
  for(const f of IOMAP_CATALOG.features){
    features[f] = document.getElementById('io-feat-'+f).checked;
  }

  try {
    const r = await fetch('/api/iomap/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({io_map, features})
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
  } catch(e){
    box.className   = 'rbox er';
    box.textContent = 'Error: '+e.message;
  }
}

async function loadRules(){
  try {
    const r = await fetch('/api/rules/'+dev());
    if(!r.ok) return;
    const c = await r.json();

    document.getElementById('rules-json').value = JSON.stringify(c.rules, null, 2);

    const cat = c.catalog;
    document.getElementById('rules-catalog').textContent =
      'processes: '+cat.processes.join(', ')+'\\n'+
      'independent_outputs: '+cat.independent_outputs.join(', ')+'\\n'+
      'inputs: '+cat.inputs.join(', ')+'\\n'+
      'derived_signals: '+cat.derived_signals.join(', ')+'\\n'+
      'fault_reasons: '+cat.fault_reasons.join(', ');
  } catch(e){}
}

async function saveRules(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Guardando reglas...';

  let rules;
  try {
    rules = JSON.parse(document.getElementById('rules-json').value);
  } catch(e){
    box.className = 'rbox er'; box.textContent = 'JSON inválido: '+e.message;
    return;
  }

  try {
    const r = await fetch('/api/rules/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({rules})
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
  } catch(e){
    box.className   = 'rbox er';
    box.textContent = 'Error: '+e.message;
  }
}

async function exportProfile(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Exportando perfil...';
  try {
    const r = await fetch('/api/profile/'+dev());
    const data = await r.json();
    if(!r.ok){
      box.className = 'rbox er'; box.textContent = JSON.stringify(data, null, 2);
      return;
    }
    document.getElementById('profile-json').value = JSON.stringify(data, null, 2);
    box.className = 'rbox ok'; box.textContent = 'Perfil actual cargado abajo.';
  } catch(e){
    box.className = 'rbox er'; box.textContent = 'Error: '+e.message;
  }
}

async function importProfile(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Importando perfil...';

  let profile;
  try {
    profile = JSON.parse(document.getElementById('profile-json').value);
  } catch(e){
    box.className = 'rbox er'; box.textContent = 'JSON inválido: '+e.message;
    return;
  }

  try {
    const r = await fetch('/api/profile/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(profile)
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
    if(r.ok){ loadIomap(); loadRules(); }
  } catch(e){
    box.className   = 'rbox er';
    box.textContent = 'Error: '+e.message;
  }
}

async function loadProcessConfig(){
  try {
    const r = await fetch('/api/process_config/'+dev());
    if(!r.ok) return;
    const c = await r.json();
    const p = c.process_config;
    document.getElementById('pc-stab').value  = p.pressure_stabilization_delay_sec;
    document.getElementById('pc-tout').value  = p.startup_timeout_sec;
    document.getElementById('pc-retry').value = p.retry_interval_sec;
    document.getElementById('pc-maxr').value  = p.max_retries;
    document.getElementById('pc-flush').value = p.flush_duration_sec;
  } catch(e){}
}

async function saveProcessConfig(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Guardando tiempos de proceso...';
  const payload = {
    pressure_stabilization_delay_sec: parseInt(document.getElementById('pc-stab').value)  || 10,
    startup_timeout_sec:              parseInt(document.getElementById('pc-tout').value)  || 5,
    retry_interval_sec:               parseInt(document.getElementById('pc-retry').value) || 10,
    max_retries:                      parseInt(document.getElementById('pc-maxr').value)  || 5,
    flush_duration_sec:               parseInt(document.getElementById('pc-flush').value) || 60,
  };
  try {
    const r = await fetch('/api/process_config/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
    if(r.ok) loadProcessConfig();
  } catch(e){
    box.className = 'rbox er'; box.textContent = 'Error: '+e.message;
  }
}

async function loadAntifreezeConfig(){
  try {
    const r = await fetch('/api/antifreeze_config/'+dev());
    if(!r.ok) return;
    const c = await r.json();
    const a = c.antifreeze_config;
    document.getElementById('af-enabled').checked        = !!a.enabled;
    document.getElementById('af-sensor-enabled').checked  = !!a.sensor_enabled;
    document.getElementById('af-gpio').value              = a.sensor_gpio;
    document.getElementById('af-thr-low').value           = a.temp_threshold_low_c;
    document.getElementById('af-thr-high').value          = a.temp_threshold_high_c;
    document.getElementById('af-flush-dur').value         = a.flush_duration_sec;
    document.getElementById('af-eval-int').value          = a.eval_interval_sec;
    document.getElementById('af-boot-inhibit').value      = a.boot_inhibit_sec;
    document.getElementById('af-min-valid').value         = a.min_valid_temp_c;
    document.getElementById('af-max-valid').value         = a.max_valid_temp_c;
    document.getElementById('af-max-fail').value          = a.max_consecutive_failures;
  } catch(e){}
}

async function saveAntifreezeConfig(){
  const box = document.getElementById('resp');
  box.className = 'rbox'; box.textContent = 'Guardando anti-congelamiento...';
  const payload = {
    enabled:                  document.getElementById('af-enabled').checked ? 1 : 0,
    sensor_enabled:           document.getElementById('af-sensor-enabled').checked ? 1 : 0,
    sensor_gpio:              parseInt(document.getElementById('af-gpio').value)        || 21,
    temp_threshold_low_c:     parseFloat(document.getElementById('af-thr-low').value)   || 0,
    temp_threshold_high_c:    parseFloat(document.getElementById('af-thr-high').value)  || 3,
    flush_duration_sec:       parseInt(document.getElementById('af-flush-dur').value)   || 300,
    eval_interval_sec:        parseInt(document.getElementById('af-eval-int').value)    || 3600,
    boot_inhibit_sec:         parseInt(document.getElementById('af-boot-inhibit').value) || 120,
    min_valid_temp_c:         parseFloat(document.getElementById('af-min-valid').value) || -40,
    max_valid_temp_c:         parseFloat(document.getElementById('af-max-valid').value) || 60,
    max_consecutive_failures: parseInt(document.getElementById('af-max-fail').value)    || 5,
  };
  try {
    const r = await fetch('/api/antifreeze_config/'+dev(), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await r.json();
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
    if(r.ok) loadAntifreezeConfig();
  } catch(e){
    box.className = 'rbox er'; box.textContent = 'Error: '+e.message;
  }
}

setInterval(poll, 5000);
poll();
loadConfig();
loadProcessConfig();
loadAntifreezeConfig();
loadIomap();
loadRules();
fetchAlerts();

// ── AI Integration ──────────────────────────────────────────────────────────
const AI_MODE_COLOR = {OFF:'bg2', VIEWER:'bb', AUTO:'bg'};
const EXEC_COLOR    = {SUCCESS:'#4ade80', REJECTED:'#fbbf24', FAILED:'#f87171'};

let aiPollBusy = false;

async function pollAi(){
  if(aiPollBusy) return;
  aiPollBusy = true;
  try {
    const r = await fetch('/api/device/'+dev()+'/ai-status');
    if(!r.ok) return;
    const s    = await r.json();
    const mode = s.ai_mode || 'OFF';
    document.getElementById('ai-select').value = mode;
    const mb   = document.getElementById('ai-mode-badge');
    mb.textContent = mode;
    mb.className   = 'badge '+(AI_MODE_COLOR[mode]||'bg2');

    const el = document.getElementById('ai-last');
    const ld = s.last_decision;
    if(ld){
      const conf = ld.confidence != null ? (ld.confidence*100).toFixed(0)+'%' : '-';
      const sc   = EXEC_COLOR[ld.exec_status] || '#94a3b8';
      el.innerHTML = '';
      // Use textContent for all AI-provided text to prevent XSS
      const line1 = document.createElement('div');
      line1.style.fontWeight = '600';
      line1.textContent = ld.decision + ' (' + conf + ')' +
          (ld.suggested_cmd ? ' → ' + ld.suggested_cmd : '') +
          (ld.exec_status   ? ' → ' + ld.exec_status   : '');
      if(ld.exec_status) line1.style.color = sc;
      el.appendChild(line1);
      const line2 = document.createElement('div');
      line2.className   = 'meta';
      line2.textContent = ld.reason || '';
      el.appendChild(line2);
      const line3 = document.createElement('div');
      line3.className   = 'meta';
      line3.textContent = ld.decided_at ? ld.decided_at.replace('T',' ').slice(0,19) : '';
      el.appendChild(line3);
    } else {
      el.innerHTML = '<span class="meta">Sin decisiones registradas</span>';
    }
  } catch(e){ console.error('[pollAi]', e); }
  finally { aiPollBusy = false; }
}

async function saveAiMode(){
  const mode = document.getElementById('ai-select').value;
  try {
    const r = await fetch('/api/device/'+dev()+'/ai-mode', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({mode})
    });
    const data = await r.json();
    const box  = document.getElementById('resp');
    box.className   = 'rbox '+(r.ok?'ok':'er');
    box.textContent = JSON.stringify(data, null, 2);
    if(r.ok) pollAi();
  } catch(e){ console.error('[saveAiMode]', e); }
}

setInterval(pollAi, 10000);
pollAi();
</script>
</body>
</html>"""


@api.route("/admin/panel", methods=["GET"])
@require_basic_auth
def admin_panel():
    """
    Admin command panel — HTML interface for manual device control.
    Protected by HTTP Basic Auth (ADMIN_PANEL_USER / ADMIN_PANEL_PASS).
    Auth disabled when ADMIN_PANEL_USER is empty (dev mode).

    Reads device list from DB. Calls /api/status and /api/command
    via fetch() — no MQTT access from the browser.
    All command execution goes through CommandEngine as normal.
    """
    rows = db.fetchall(
        "SELECT device_id, display_name, installed_at FROM devices ORDER BY registered_at"
    )
    if not rows:
        return ("<h2 style='font-family:sans-serif;padding:2rem'>"
                "No hay dispositivos registrados.</h2>"), 200
    devices = [
        {"device_id": r[0], "display_name": r[1] or r[0], "installed_at": str(r[2]) if r[2] else ""}
        for r in rows
    ]
    return render_template_string(_ADMIN_PANEL_HTML, devices=devices)


# ── AI mode endpoints ─────────────────────────────────────────────────────────

@api.route("/api/device/<device_id>/ai-mode", methods=["POST"])
def set_device_ai_mode(device_id):
    """Set the AI operation mode for a device.
    Body: {"mode": "OFF" | "VIEWER" | "AUTO"}
    """
    if not db.fetchall("SELECT 1 FROM devices WHERE device_id = %s", (device_id,)):
        return jsonify({"error": "device_not_found"}), 404

    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").upper().strip()
    if mode not in ("OFF", "VIEWER", "AUTO"):
        return jsonify({"error": "invalid_mode", "allowed": ["OFF", "VIEWER", "AUTO"]}), 400

    db.execute("UPDATE devices SET ai_mode = %s WHERE device_id = %s", (mode, device_id))
    log.info(f"[AI-MODE] device={device_id} ai_mode={mode}")
    return jsonify({"device_id": device_id, "ai_mode": mode})


@api.route("/api/device/<device_id>/ai-mode", methods=["GET"])
def get_device_ai_mode(device_id):
    rows = db.fetchall("SELECT ai_mode FROM devices WHERE device_id = %s", (device_id,))
    if not rows:
        return jsonify({"error": "device_not_found"}), 404
    return jsonify({"device_id": device_id, "ai_mode": rows[0][0] or "OFF"})


@api.route("/api/device/<device_id>/ai-status", methods=["GET"])
def get_device_ai_status(device_id):
    """Current AI mode and last recorded decision. Used by the admin panel."""
    rows = db.fetchall("SELECT ai_mode FROM devices WHERE device_id = %s", (device_id,))
    if not rows:
        return jsonify({"error": "device_not_found"}), 404

    ai_mode = rows[0][0] or "OFF"
    last_dec = db.fetchall(
        "SELECT decided_at, decision, confidence, reason, "
        "       suggested_cmd, executed, exec_status, exec_result "
        "FROM ai_decisions WHERE device_id = %s "
        "ORDER BY decided_at DESC LIMIT 1",
        (device_id,),
    )
    last_decision = None
    if last_dec:
        r = last_dec[0]
        last_decision = {
            "decided_at":    r[0].isoformat() if r[0] else None,
            "decision":      r[1],
            "confidence":    r[2],
            "reason":        r[3],
            "suggested_cmd": r[4],
            "executed":      r[5],
            "exec_status":   r[6],
            "exec_result":   r[7],
        }
    return jsonify({"device_id": device_id, "ai_mode": ai_mode, "last_decision": last_decision})


def _start_api():
    api.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

# ============================================================
# MAIN
# ============================================================

def main():
    log.info("🚀 Fyntek RO Backend v3.3 iniciando...")

    for attempt in range(10):
        try:
            db.connect()
            break
        except Exception as e:
            log.warning(f"DB no disponible ({attempt+1}/10): {e}")
            time.sleep(3)
    else:
        log.critical("No se pudo conectar a la DB. Abortando.")
        return

    # Safe schema migrations (idempotent)
    db.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS notification_count INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS min_flow_lpm            FLOAT   DEFAULT 0.2")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS max_flow_lpm            FLOAT   DEFAULT 20.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS flow_fault_delay_sec    INTEGER DEFAULT 30")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS min_recovery_pct        FLOAT   DEFAULT 10.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS max_recovery_pct        FLOAT   DEFAULT 85.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS recovery_fault_delay_sec INTEGER DEFAULT 60")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds1_cal_slope  FLOAT DEFAULT 0.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds1_cal_offset FLOAT DEFAULT 0.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds2_cal_slope  FLOAT DEFAULT 0.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds2_cal_offset FLOAT DEFAULT 0.0")
    # Calibración de presión (voltaje→bar), por canal — sincronizada con firmware vía /config
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_enabled        BOOLEAN DEFAULT FALSE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_min_voltage    FLOAT   DEFAULT 0.5")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_max_voltage    FLOAT   DEFAULT 4.5")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_min_bar        FLOAT   DEFAULT 0.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_max_bar        FLOAT   DEFAULT 14.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_limits_enabled BOOLEAN DEFAULT FALSE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_membrane_high_limit     FLOAT   DEFAULT 12.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_fault_delay_sec         INTEGER DEFAULT 3")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_enabled           BOOLEAN DEFAULT FALSE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_min_voltage       FLOAT   DEFAULT 0.5")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_max_voltage       FLOAT   DEFAULT 4.5")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_min_bar           FLOAT   DEFAULT 0.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_max_bar           FLOAT   DEFAULT 14.0")
    # Alarmas diagnósticas backend-only (NO se publican a firmware)
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_high_limit        FLOAT   DEFAULT 8.0")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS pressure_brine_alarm_enabled     BOOLEAN DEFAULT FALSE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS delta_p_alarm_enabled            BOOLEAN DEFAULT FALSE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS delta_p_alarm_limit              FLOAT   DEFAULT 5.0")
    # Telemetría de presión — voltaje por canal + delta_p (membrana-brine)
    db.execute("ALTER TABLE telemetry_process ADD COLUMN IF NOT EXISTS pressure_membrane_voltage FLOAT")
    db.execute("ALTER TABLE telemetry_process ADD COLUMN IF NOT EXISTS pressure_brine_voltage    FLOAT")
    db.execute("ALTER TABLE telemetry_process ADD COLUMN IF NOT EXISTS delta_p_bar               FLOAT")
    db.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS pressure_brine_bar        FLOAT")
    db.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS pressure_membrane_voltage FLOAT")
    db.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS pressure_brine_voltage    FLOAT")
    db.execute("ALTER TABLE device_status ADD COLUMN IF NOT EXISTS delta_p_bar               FLOAT")
    # Capa de abstracción Pin<->Señal lógica + features por dispositivo (ver io_catalog.py)
    db.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS io_map   JSONB DEFAULT '{}'::jsonb")
    db.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '{}'::jsonb")
    # Motor de reglas — process_permits[]/independent_outputs[]/fault_rules[] (ver rule_catalog.py)
    db.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS rules    JSONB DEFAULT '{}'::jsonb")
    # Protecciones de flujo/recovery habilitables (CFG_VERSION 2, ver firmware sensors.h)
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS flow_protection_enabled     BOOLEAN DEFAULT TRUE")
    db.execute("ALTER TABLE device_config ADD COLUMN IF NOT EXISTS recovery_protection_enabled BOOLEAN DEFAULT TRUE")
    # Parámetros de temporización FSM configurables (ver process_config_catalog.py)
    db.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS process_config JSONB DEFAULT '{}'::jsonb")
    # Protección anti-congelamiento opcional, DHT22 (ver antifreeze_catalog.py)
    db.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS antifreeze_config JSONB DEFAULT '{}'::jsonb")
    log.info("✅ Schema migrations aplicadas")

    # Pre-populate tracker from DB so panels show correct state after restart
    # without waiting for the firmware to re-send state via MQTT (on-change only)
    rows = db.fetchall(
        "SELECT d.device_id, ds.state, ds.last_seen "
        "FROM devices d "
        "LEFT JOIN device_status ds ON d.device_id = ds.device_id "
        "WHERE COALESCE(ds.state, 'UNKNOWN') != 'UNKNOWN'"
    )
    for device_id, state, _ in rows:
        tracker.update_state(device_id, state)
    if rows:
        log.info(f"✅ Tracker inicializado: {len(rows)} dispositivos desde DB")

    api_thread = threading.Thread(target=_start_api, daemon=True)
    api_thread.start()
    log.info("✅ API HTTP en puerto 8080")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        log.critical(f"No se pudo conectar al broker: {e}")
        return

    global command_engine, ai_decision_engine, realtime_engine, mqtt_client
    mqtt_client    = client
    command_engine = CommandEngine(client)
    command_engine.start()

    telegram_worker.start()
    email_worker.start()
    offline_checker.start()

    ai_decision_engine = AIDecisionEngine()
    ai_decision_engine.start()

    realtime_engine = RealtimeAIEngine()
    if AI_REALTIME_ENDPOINT_URL:
        log.info(
            f"[AI-RT] Realtime engine active — endpoint={AI_REALTIME_ENDPOINT_URL} "
            f"timeout={AI_REALTIME_TIMEOUT_SEC}s context_sec={AI_REALTIME_CONTEXT_SECONDS}"
        )
    else:
        log.info("[AI-RT] AI_REALTIME_ENDPOINT_URL not set — realtime engine idle")

    log.info("✅ Escuchando MQTT...")
    client.loop_forever()


if __name__ == "__main__":
    main()
