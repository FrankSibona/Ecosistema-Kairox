#pragma once

// ================= PINES =================

// Relés
#define PIN_R1 4   // bomba baja presión (pumpLow)
#define PIN_R2 16  // bomba alta presión (pumpHigh)
#define PIN_R3 17  // bomba de pozo     (pumpInlet)
#define PIN_R4 18  // dosificación      (pumpDose — sin uso)
#define PIN_R5 19  // válvula flush     (valveFlush)
#define PIN_R6 2   // válvula entrada   (valveInlet)

// Digitales
#define PIN_D1 27  // demanda de agua
#define PIN_D2 26  // agua cruda disponible
#define PIN_D3 25  // dosificación OK
#define PIN_D4 33  // presostato
#define PIN_D5 32  // flotante nivel bajo pozo
#define PIN_D6 23  // reserva

// Caudal
#define PIN_Q1 14
#define PIN_Q2 13

// Analógicos
#define PIN_AIN0 36
#define PIN_AIN1 39
#define PIN_TDS1 34
#define PIN_TDS2 35

// ================= CALIBRACIÓN DE SENSORES =================
// Valores default cargados en NVS al primer arranque.
// Actualizables en runtime vía MQTT fyntek/{device_id}/config.
// flow_factor = pulsos/litro (convención: Hz/(L/s) × 60).
// Ej. YF-S201: 450 pulsos/litro. Ajustar según datasheet del sensor.
#define FLOW_FACTOR_DEFAULT     450.0f
#define TDS_TEMPERATURE_DEFAULT  25.0f  // °C — compensación térmica TDS (nominal)

// ── Calibración TDS voltaje→ppm (capa reemplazable) ──────────────────────────
// slope == 0.0f  → sin calibración cargada: fallback a voltageToPpm() (polinomio
//                  DFRobot). Estado por defecto de fábrica / equipos sin tocar.
// slope >  0.0f  → CAL_MODE_LINEAR: ppm = slope * mV + offset.
// Actualizable por canal/dispositivo vía MQTT fyntek/{device_id}/config.
#define TDS1_CAL_SLOPE_DEFAULT   0.0f
#define TDS1_CAL_OFFSET_DEFAULT  0.0f
#define TDS2_CAL_SLOPE_DEFAULT   0.0f
#define TDS2_CAL_OFFSET_DEFAULT  0.0f

// ── Protecciones de proceso (configurables vía MQTT/Flask) ───────────────────
#define MIN_FLOW_LPM_DEFAULT          0.2f   // L/min — mínimo permeado en PRODUCING
#define MAX_FLOW_LPM_DEFAULT         20.0f   // L/min — máximo permeado (reservado)
#define FLOW_FAULT_DELAY_SEC_DEFAULT   30U    // segundos continuo fuera de rango → FAULT

#define MIN_RECOVERY_PCT_DEFAULT      10.0f  // % — recovery mínima en PRODUCING
#define MAX_RECOVERY_PCT_DEFAULT      85.0f  // % — recovery máxima en PRODUCING
#define RECOVERY_FAULT_DELAY_SEC_DEFAULT 60U // segundos continuo fuera de rango → FAULT

// ── Calibración de presión (voltaje→bar), por canal ──────────────────────────
// *_enabled = 0 (default): pressure_membrane_bar/pressure_brine_bar se calculan
// con la fórmula legacy (adc/4095*10, EWMA), sin cambios respecto al firmware
// actual. *_enabled = 1: calibración lineal v_min/v_max → bar_min/bar_max.
#define PRESSURE_ADC_VREF                       3.1f  // ADC_11db full-scale (V), PIN_AIN0/AIN1

#define PRESSURE_MEMBRANE_ENABLED_DEFAULT          0
#define PRESSURE_MEMBRANE_MIN_VOLTAGE_DEFAULT   0.5f
#define PRESSURE_MEMBRANE_MAX_VOLTAGE_DEFAULT   4.5f
#define PRESSURE_MEMBRANE_MIN_BAR_DEFAULT       0.0f
#define PRESSURE_MEMBRANE_MAX_BAR_DEFAULT      14.0f
#define PRESSURE_MEMBRANE_LIMITS_ENABLED_DEFAULT   0
#define PRESSURE_MEMBRANE_HIGH_LIMIT_DEFAULT   12.0f

#define PRESSURE_BRINE_ENABLED_DEFAULT             0
#define PRESSURE_BRINE_MIN_VOLTAGE_DEFAULT      0.5f
#define PRESSURE_BRINE_MAX_VOLTAGE_DEFAULT      4.5f
#define PRESSURE_BRINE_MIN_BAR_DEFAULT          0.0f
#define PRESSURE_BRINE_MAX_BAR_DEFAULT         14.0f

// Debounce compartido para la protección crítica de presión de membrana alta.
#define PRESSURE_FAULT_DELAY_SEC_DEFAULT           3U

// ── NVS config integrity ──────────────────────────────────────────────────────
// CFG_MAGIC is written alongside calibration data in NVS namespace "kx_cfg".
// On load, if magic or version don't match, the entire stored config is
// discarded and safe defaults are used.  Bump CFG_VERSION whenever the
// SensorConfig layout or semantics change to auto-invalidate stale NVS data.
#define CFG_MAGIC    0x4B524F58U   // 'K','R','O','X' — identifies KAIROX config block
#define CFG_VERSION  1U            // increment on struct layout changes

// ================= FSM =================
#define LOW_PUMP_FILL_TIME   10000
#define PRESSURE_CHECK_TIME   5000
#define RETRY_DELAY          10000
#define FSM_MAX_RETRIES          5

#define FLUSH_START_TIME     10000
#define FLUSH_STOP_TIME      10000
#define FLUSH_TDS_TIME       60000

#define TDS_DELAY            3600000
#define MIN_TIME_BETWEEN_FLUSH 14400000
#define TDS_LIMIT_PPM 500.0f  // ppm — umbral referencia (no usado en FSM actual)

// ================= FILTROS =================
#define DEMAND_FILTER_TIME   3000   // 3s
#define CRUDO_FILTER_TIME    3000
#define PRESSURE_FILTER_TIME 2000


// ================= MQTT =================
#define MQTT_BROKER "159.112.132.176"
#define MQTT_PORT 1883
#define MQTT_USER "kairox"
#define MQTT_PASS "admin0102"

// Timeout de socket para mqttClient.connect()/publish() — default de
// PubSubClient es 15s, demasiado largo: mantiene el loop principal
// bloqueado ante un broker inalcanzable. Acotado para que el FSM y el
// resto de comms.update() sigan respondiendo.
#define MQTT_SOCKET_TIMEOUT_SEC 3U

// Portal cautivo de configuración (solo si NO hay credenciales WiFi guardadas).
// Acotado para no bloquear el arranque del FSM indefinidamente — si nadie
// configura WiFi en este tiempo, el equipo arranca offline igual.
#define WIFI_PORTAL_TIMEOUT_SEC 180U

// ================= WATCHDOG =================
// Task watchdog del ESP32 — red de seguridad ante cuelgues reales (deadlock,
// bucle infinito, bloqueo de librería). 30s es deliberadamente holgado para
// no interferir con operación normal ni con conectividad degradada — solo
// debe disparar ante un loop() que deja de iterar por completo.
// Se alimenta únicamente desde el loop principal (ver main.cpp).
#define WATCHDOG_TIMEOUT_SEC 30U

#define DEVICE_ID "osmosis_01"

#define TOPIC_STATE   "fyntek/osmosis_01/state"
#define TOPIC_PROCESS "fyntek/osmosis_01/process"
#define TOPIC_QUALITY "fyntek/osmosis_01/quality"
#define TOPIC_EVENT   "fyntek/osmosis_01/event"
#define TOPIC_CMD     "fyntek/osmosis_01/cmd"