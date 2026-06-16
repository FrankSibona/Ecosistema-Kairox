#pragma once
#include <stdint.h>

// Parámetros de temporización de la FSM (antes hardcodeados en config.h como
// #define). Misma arquitectura que SensorConfig/RulesConfig: NVS namespace
// "kx_proccfg" + MQTT retained fyntek/{device_id}/process_config + updated_at.

struct ProcessConfig {
    uint32_t pressure_stabilization_delay_sec;  // LOW_PUMP_FILL_TIME / 1000
    uint32_t startup_timeout_sec;               // PRESSURE_CHECK_TIME / 1000
    uint32_t retry_interval_sec;                // RETRY_DELAY / 1000
    uint8_t  max_retries;                       // FSM_MAX_RETRIES
    uint32_t flush_duration_sec;                // FLUSH_TDS_TIME / 1000
    uint32_t updated_at;
};

void                 processConfigInit();
const ProcessConfig& processConfigGet();
bool                 processConfigSet(const ProcessConfig& incoming);
