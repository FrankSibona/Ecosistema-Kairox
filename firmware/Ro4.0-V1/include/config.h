#pragma once

// ================= PINES =================

// Relés
#define PIN_R1 4
#define PIN_R2 16
#define PIN_R3 17
#define PIN_R4 18
#define PIN_R5 19
#define PIN_R6 2

// Digitales
#define PIN_D1 27
#define PIN_D2 26
#define PIN_D3 25
#define PIN_D4 33
#define PIN_D5 32
#define PIN_D6 23

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
#define MAX_RETRIES              5

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
#define MQTT_USER "fyntek"
#define MQTT_PASS "dlgfyntek0912"

#define DEVICE_ID "osmosis_01"

#define TOPIC_STATE   "fyntek/osmosis_01/state"
#define TOPIC_PROCESS "fyntek/osmosis_01/process"
#define TOPIC_QUALITY "fyntek/osmosis_01/quality"
#define TOPIC_EVENT   "fyntek/osmosis_01/event"
#define TOPIC_CMD     "fyntek/osmosis_01/cmd"