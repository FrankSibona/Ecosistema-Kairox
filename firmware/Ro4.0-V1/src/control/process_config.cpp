#include "process_config.h"
#include <Preferences.h>
#include <Arduino.h>
#include <config.h>

static ProcessConfig _cfg;

static ProcessConfig defaultProcessConfig() {
    ProcessConfig c;
    c.pressure_stabilization_delay_sec = PROCCFG_PRESSURE_STABILIZATION_DELAY_SEC_DEFAULT;
    c.startup_timeout_sec              = PROCCFG_STARTUP_TIMEOUT_SEC_DEFAULT;
    c.retry_interval_sec               = PROCCFG_RETRY_INTERVAL_SEC_DEFAULT;
    c.max_retries                      = PROCCFG_MAX_RETRIES_DEFAULT;
    c.flush_duration_sec               = PROCCFG_FLUSH_DURATION_SEC_DEFAULT;
    c.updated_at                       = 0;
    return c;
}

void processConfigInit() {
    Preferences p;
    p.begin("kx_proccfg", true);
    uint32_t magic   = p.getUInt("magic",   0);
    uint32_t version = p.getUInt("version", 0);
    if (magic == PROCCFG_MAGIC && version == PROCCFG_VERSION) {
        size_t n = p.getBytes("data", &_cfg, sizeof(_cfg));
        if (n == sizeof(_cfg)) {
            p.end();
            Serial.printf("[PROCCFG] Init OK — updated_at=%u\n", (unsigned)_cfg.updated_at);
            return;
        }
    }
    p.end();
    Serial.println("[PROCCFG] NVS vacío/incompatible — usando defaults");
    _cfg = defaultProcessConfig();
}

const ProcessConfig& processConfigGet() {
    return _cfg;
}

bool processConfigSet(const ProcessConfig& incoming) {
    if (incoming.updated_at > 0 && incoming.updated_at <= _cfg.updated_at) {
        Serial.printf("[PROCCFG] IGNORED — updated_at=%u <= actual=%u\n",
                      (unsigned)incoming.updated_at, (unsigned)_cfg.updated_at);
        return false;
    }
    _cfg.pressure_stabilization_delay_sec = incoming.pressure_stabilization_delay_sec;
    _cfg.startup_timeout_sec              = incoming.startup_timeout_sec;
    _cfg.retry_interval_sec               = incoming.retry_interval_sec;
    _cfg.max_retries                      = incoming.max_retries;
    _cfg.flush_duration_sec               = incoming.flush_duration_sec;
    _cfg.updated_at                       = incoming.updated_at;
    Preferences p;
    p.begin("kx_proccfg", false);
    p.putUInt("magic",   PROCCFG_MAGIC);
    p.putUInt("version", PROCCFG_VERSION);
    p.putBytes("data",   &_cfg, sizeof(_cfg));
    p.end();
    Serial.printf("[PROCCFG] Guardado en NVS — updated_at=%u\n", (unsigned)_cfg.updated_at);
    return true;
}
