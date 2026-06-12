- ============================================================
-- Fyntek RO Backend - Schema v3.3
-- Compatible con worker v3.3
-- PostgreSQL >= 13
-- ============================================================
-- Cambios vs v3.2:
--   device_config:  +daily_target_liters
--   device_status:  +campos de negocio (waste, fulfillment, risk, degradation)
--   business_metrics: tabla nueva con KPIs de negocio por día
-- ============================================================

CREATE TABLE IF NOT EXISTS telemetry_process (
    time                    TIMESTAMPTZ     NOT NULL,
    device_id               TEXT            NOT NULL,
    flow_permeate_lpm           FLOAT,
    flow_reject_lpm        FLOAT,
    pressure_membrane_bar   FLOAT,
    pressure_brine_bar      FLOAT,
    volume_permeate_l           FLOAT,
    volume_reject_l        FLOAT,
    fw_version              TEXT
);
CREATE INDEX IF NOT EXISTS idx_process_device_time
    ON telemetry_process (device_id, time DESC);

CREATE TABLE IF NOT EXISTS telemetry_quality (
    time             TIMESTAMPTZ     NOT NULL,
    device_id        TEXT            NOT NULL,
    tds_in_voltage   FLOAT,          -- V, calibrated via analogReadMilliVolts
    tds_out_voltage  FLOAT,
    tds_in_ppm       FLOAT,          -- ppm, DFRobot SEN0244 polynomial at tds_temperature
    tds_out_ppm      FLOAT,
    fw_version       TEXT
);
CREATE INDEX IF NOT EXISTS idx_quality_device_time
    ON telemetry_quality (device_id, time DESC);

CREATE TABLE IF NOT EXISTS telemetry_state (
    time            TIMESTAMPTZ     NOT NULL,
    device_id       TEXT            NOT NULL,
    state           TEXT            NOT NULL,
    state_numeric   SMALLINT        NOT NULL,
    running         BOOLEAN,
    retry_count     SMALLINT
);
CREATE INDEX IF NOT EXISTS idx_state_device_time
    ON telemetry_state (device_id, time DESC);

CREATE TABLE IF NOT EXISTS telemetry_inputs (
    time        TIMESTAMPTZ     NOT NULL,
    device_id   TEXT            NOT NULL,
    demand      BOOLEAN,
    raw_water_ok    BOOLEAN,
    dose_ok     BOOLEAN,
    pressure_switch  BOOLEAN,
    feed_tank_level_low    BOOLEAN,
    spare2    BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_inputs_device_time
    ON telemetry_inputs (device_id, time DESC);

CREATE TABLE IF NOT EXISTS telemetry_outputs (
    time            TIMESTAMPTZ     NOT NULL,
    device_id       TEXT            NOT NULL,
    pump_low        BOOLEAN,
    pump_high       BOOLEAN,
    pump_inlet      BOOLEAN,
    pump_dose       BOOLEAN,
    valve_flush     BOOLEAN,
    valve_inlet     BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_outputs_device_time
    ON telemetry_outputs (device_id, time DESC);

CREATE TABLE IF NOT EXISTS metrics (
    time                TIMESTAMPTZ     NOT NULL,
    device_id           TEXT            NOT NULL,
    recovery            FLOAT,
    efficiency          FLOAT,
    rejection_ratio     FLOAT,
    delta_pressure_bar  FLOAT,
    flow_permeate_lpm       FLOAT,
    flow_reject_lpm    FLOAT,
    tds_in_ppm          FLOAT,
    tds_out_ppm         FLOAT,
    cost_per_liter      FLOAT
);
CREATE INDEX IF NOT EXISTS idx_metrics_device_time
    ON metrics (device_id, time DESC);

CREATE TABLE IF NOT EXISTS diagnostics (
    time        TIMESTAMPTZ     NOT NULL,
    device_id   TEXT            NOT NULL,
    severity    TEXT            NOT NULL,
    code        TEXT            NOT NULL,
    message     TEXT            NOT NULL,
    action      TEXT,
    details     JSONB
);
CREATE INDEX IF NOT EXISTS idx_diagnostics_device_time
    ON diagnostics (device_id, time DESC);

-- ============================================================
-- TABLA: device_status
-- state       → qué está haciendo el equipo (operativo, cambia rápido)
-- health_*    → cómo está el sistema (persiste, independiente del state)
-- biz_*       → métricas de negocio en tiempo real
-- ============================================================
CREATE TABLE IF NOT EXISTS device_status (
    device_id               TEXT        PRIMARY KEY,
    last_seen               TIMESTAMPTZ,
    online                  BOOLEAN     DEFAULT FALSE,

    -- Estado operativo
    state                   TEXT,

    -- Último diagnóstico del ciclo actual
    last_severity           TEXT,
    last_diag_code          TEXT,
    last_diag_message       TEXT,
    last_action             TEXT,

    -- Lecturas de proceso
    flow_permeate_lpm           FLOAT,
    pressure_membrane       FLOAT,
    recovery                FLOAT,
    efficiency              FLOAT,

    -- Salud persistente (no se resetea al apagarse)
    health_status           TEXT        DEFAULT 'UNKNOWN',
    health_code             TEXT        DEFAULT 'UNKNOWN',
    health_message          TEXT,
    health_action           TEXT,
    health_updated_at       TIMESTAMPTZ,
    secondary_diag_codes    JSONB       DEFAULT '[]'::jsonb,

    -- ── MÉTRICAS DE NEGOCIO (nuevo en v3.3) ──────────────────
    -- Producción
    biz_liters_today        FLOAT,      -- litros producidos hoy
    biz_target_liters       FLOAT,      -- objetivo del día (de device_config)
    biz_fulfillment_pct     FLOAT,      -- cumplimiento vs objetivo [0-100]
    -- Desperdicio
    biz_waste_liters_today  FLOAT,      -- litros rechazados hoy
    biz_waste_pct           FLOAT,      -- % rechazo sobre total [0-100]
    -- Riesgo operativo
    biz_risk_level          TEXT,       -- LOW | MEDIUM | HIGH | CRITICAL
    biz_risk_score          FLOAT,      -- score numérico [0-100]
    -- Degradación
    biz_degradation_pct     FLOAT,      -- % pérdida eficiencia vs baseline (negativo = degradó)
    biz_degradation_days    INT,        -- en cuántos días ocurrió
    biz_degradation_label   TEXT,       -- ej: "Perdió 8.3% en 7 días"
    -- Frescura del diagnóstico de salud
    biz_health_age_hours    FLOAT       -- horas desde el último update de salud
);

CREATE TABLE IF NOT EXISTS devices (
    device_id           TEXT        PRIMARY KEY,
    friendly_name       TEXT,
    telegram_chat_id    TEXT,
    registered_at       TIMESTAMPTZ DEFAULT NOW(),
    fw_version          TEXT,
    notes               TEXT
);

-- ============================================================
-- TABLA: device_config
-- +daily_target_liters: objetivo de producción diaria (nuevo v3.3)
-- ============================================================
CREATE TABLE IF NOT EXISTS device_config (
    device_id               TEXT        PRIMARY KEY,
    pump_power_kw           FLOAT       DEFAULT 0.75,
    cost_kwh                FLOAT       DEFAULT 0.12,
    cost_water_m3           FLOAT       DEFAULT 0.80,
    target_recovery         FLOAT       DEFAULT 0.65,
    target_efficiency       FLOAT       DEFAULT 0.92,
    daily_target_liters     FLOAT       DEFAULT 0,       -- ← nuevo v3.3 (0 = sin objetivo)
    -- ── SENSOR CALIBRATION ───────────────────────────────────────────────────
    flow_factor_1           FLOAT       DEFAULT 450.0,  -- pulsos/litro caudalímetro permeado
    flow_factor_2           FLOAT       DEFAULT 450.0,  -- pulsos/litro caudalímetro rechazo
    tds_temperature         FLOAT       DEFAULT 25.0,   -- °C para compensación temperatura TDS
    -- Calibración TDS voltaje→ppm (CAL_MODE_LINEAR). slope=0 → sin calibrar,
    -- firmware usa fallback voltageToPpm() (polinomio DFRobot).
    tds1_cal_slope          FLOAT       DEFAULT 0.0,    -- ppm/mV — canal TDS1
    tds1_cal_offset         FLOAT       DEFAULT 0.0,    -- ppm — canal TDS1
    tds2_cal_slope          FLOAT       DEFAULT 0.0,    -- ppm/mV — canal TDS2
    tds2_cal_offset         FLOAT       DEFAULT 0.0,    -- ppm — canal TDS2
    -- ── PROCESS PROTECTIONS ──────────────────────────────────────────────────
    min_flow_lpm            FLOAT       DEFAULT 0.2,    -- L/min mínimo en PRODUCING → FLOW_LOW
    max_flow_lpm            FLOAT       DEFAULT 20.0,   -- L/min máximo en PRODUCING (reservado)
    flow_fault_delay_sec    INTEGER     DEFAULT 30,     -- segundos fuera de rango antes de FAULT
    min_recovery_pct        FLOAT       DEFAULT 10.0,   -- % recovery mínima → RECOVERY_LOW
    max_recovery_pct        FLOAT       DEFAULT 85.0,   -- % recovery máxima → RECOVERY_HIGH
    recovery_fault_delay_sec INTEGER    DEFAULT 60,     -- segundos fuera de rango antes de FAULT
    -- ─────────────────────────────────────────────────────────────────────────
    friendly_name           TEXT,
    location                TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS learning_sessions (
    id              SERIAL          PRIMARY KEY,
    device_id       TEXT            NOT NULL,
    started_at      TIMESTAMPTZ     NOT NULL,
    finished_at     TIMESTAMPTZ,
    duration_min    INT             DEFAULT 30,
    status          TEXT            DEFAULT 'RUNNING',
    samples         INT             DEFAULT 0
);

-- ============================================================
-- TABLA: device_baseline
-- ============================================================
CREATE TABLE IF NOT EXISTS device_baseline (
    device_id               TEXT    PRIMARY KEY,
    learned_at              TIMESTAMPTZ,
    session_id              INT,
    efficiency_mean         FLOAT,
    efficiency_std          FLOAT,
    recovery_mean           FLOAT,
    recovery_std            FLOAT,
    flow_permeate_mean          FLOAT,
    flow_permeate_std           FLOAT,
    delta_pressure_mean     FLOAT,
    delta_pressure_std      FLOAT,

    efficiency_warn_low_learned     FLOAT,
    efficiency_warn_low_manual      FLOAT,
    efficiency_warn_low_source      TEXT    DEFAULT 'learned',
    efficiency_crit_low_learned     FLOAT,
    efficiency_crit_low_manual      FLOAT,
    efficiency_crit_low_source      TEXT    DEFAULT 'learned',
    recovery_warn_low_learned       FLOAT,
    recovery_warn_low_manual        FLOAT,
    recovery_warn_low_source        TEXT    DEFAULT 'learned',
    recovery_warn_high_learned      FLOAT,
    recovery_warn_high_manual       FLOAT,
    recovery_warn_high_source       TEXT    DEFAULT 'learned',
    flow_permeate_warn_low_learned      FLOAT,
    flow_permeate_warn_low_manual       FLOAT,
    flow_permeate_warn_low_source       TEXT    DEFAULT 'learned',
    pressure_warn_high_learned      FLOAT,
    pressure_warn_high_manual       FLOAT,
    pressure_warn_high_source       TEXT    DEFAULT 'learned',
    pressure_crit_high_learned      FLOAT,
    pressure_crit_high_manual       FLOAT,
    pressure_crit_high_source       TEXT    DEFAULT 'learned',
    delta_pressure_warn_high_learned    FLOAT,
    delta_pressure_warn_high_manual     FLOAT,
    delta_pressure_warn_high_source     TEXT    DEFAULT 'learned'
);

-- ============================================================
-- TABLA: business_metrics (nuevo v3.3)
-- KPIs de negocio calculados una vez por día por dispositivo.
-- Histórico completo para trending de largo plazo.
-- ============================================================
CREATE TABLE IF NOT EXISTS business_metrics (
    day                     DATE        NOT NULL,
    device_id               TEXT        NOT NULL,
    PRIMARY KEY (day, device_id),

    -- Producción
    liters_produced         FLOAT,      -- litros de permeado producidos
    liters_rejected         FLOAT,      -- litros de rechazo
    daily_target_liters     FLOAT,      -- objetivo del día (snapshot de config)
    fulfillment_pct         FLOAT,      -- % cumplimiento vs objetivo
    -- Desperdicio
    waste_pct               FLOAT,      -- rechazo / (producido + rechazo) * 100
    -- Eficiencia
    avg_efficiency          FLOAT,      -- eficiencia media del día [0-1]
    avg_recovery            FLOAT,      -- recovery medio del día [0-1]
    -- Operación
    hours_producing         FLOAT,      -- horas en estado PRODUCING
    cycle_count             INT,        -- ciclos ON/OFF
    -- Costo
    estimated_cost          FLOAT,      -- costo estimado del día
    -- Riesgo
    risk_level              TEXT,       -- riesgo dominante del día
    -- Metadatos
    calculated_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_device_day
    ON business_metrics (device_id, day DESC);

-- ============================================================
-- VISTA: daily_production
-- ============================================================
CREATE OR REPLACE VIEW daily_production AS
SELECT
    device_id,
    DATE(time AT TIME ZONE 'America/Argentina/Buenos_Aires') AS day,
    MAX(volume_permeate_l)    - MIN(volume_permeate_l)               AS liters_produced,
    MAX(volume_reject_l) - MIN(volume_reject_l)            AS liters_rejected,
    ROUND(CAST(
        (MAX(volume_permeate_l) - MIN(volume_permeate_l)) /
        NULLIF(
            (MAX(volume_permeate_l)    - MIN(volume_permeate_l)) +
            (MAX(volume_reject_l) - MIN(volume_reject_l)),
        0) * 100
    AS numeric), 1) AS recovery_pct
FROM telemetry_process
WHERE volume_permeate_l IS NOT NULL
GROUP BY device_id, DATE(time AT TIME ZONE 'America/Argentina/Buenos_Aires')
ORDER BY day DESC;

-- ============================================================
-- Multi-cliente — v1
-- ============================================================
CREATE TABLE IF NOT EXISTS clients (
    id              SERIAL       PRIMARY KEY,
    name            TEXT         NOT NULL,
    grafana_org_id  INTEGER,     -- ID de la Organization en Grafana
    email           TEXT,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Ownership de dispositivos
ALTER TABLE devices ADD COLUMN IF NOT EXISTS client_id    INTEGER REFERENCES clients(id);
ALTER TABLE devices ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS enabled      BOOLEAN DEFAULT TRUE;

-- Integración IA por dispositivo.
-- ai_mode es la única fuente de verdad: OFF | VIEWER | AUTO
-- No se necesita ai_enabled — ai_mode='OFF' cumple ese rol sin ambigüedad.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS ai_mode TEXT DEFAULT 'OFF'
    CHECK (ai_mode IN ('OFF', 'VIEWER', 'AUTO'));

-- ============================================================
-- TABLA: ai_decisions
--
-- Registro histórico de decisiones del motor IA externo.
--
-- Flujo típico por fila:
--   1. Se inserta la decisión IA (decided_at, ai_mode, decision,
--      confidence, reason, suggested_cmd).
--   2. Si corresponde, el backend ejecuta el comando via CommandEngine.
--   3. La misma fila se actualiza con:
--        - executed     = TRUE/FALSE
--        - executed_at  = timestamp de ejecución
--        - exec_status  = SUCCESS | REJECTED | FAILED
--        - exec_result  = detalle textual (command_id, motivo, etc.)
--
-- OFF    → no se generan filas
-- VIEWER → sugerencia registrada, executed=FALSE, exec_status=REJECTED
-- AUTO   → decisión registrada y actualizada con resultado de ejecución
-- ============================================================
CREATE TABLE IF NOT EXISTS ai_decisions (
    id            SERIAL       PRIMARY KEY,
    device_id     TEXT         NOT NULL
                  REFERENCES devices(device_id),    -- FK: devices.device_id es PRIMARY KEY
    decided_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    ai_mode       TEXT         NOT NULL
                  CHECK (ai_mode IN ('OFF', 'VIEWER', 'AUTO')),

    decision      TEXT         NOT NULL
                  CHECK (decision IN ('NONE', 'EXECUTE', 'ERROR')),

    confidence    FLOAT,
    reason        TEXT,

    suggested_cmd TEXT
                  CHECK (
                      suggested_cmd IS NULL OR
                      suggested_cmd IN ('START', 'STOP', 'FLUSH', 'RST')
                  ),
    -- Si decision='EXECUTE', debe haber un comando asociado
    CONSTRAINT ai_decisions_execute_requires_cmd
        CHECK (
            (decision = 'EXECUTE' AND suggested_cmd IS NOT NULL)
            OR (decision IN ('NONE', 'ERROR'))
        ),

    executed      BOOLEAN      DEFAULT FALSE,
    executed_at   TIMESTAMPTZ,

    exec_status   TEXT
                  CHECK (
                      exec_status IS NULL OR
                      exec_status IN ('SUCCESS', 'REJECTED', 'FAILED')
                  ),

    exec_result   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_device
    ON ai_decisions (device_id, decided_at DESC);

-- ============================================================
-- TABLA: device_commands
-- Command Engine v3 — lifecycle: SENT → RECEIVED → ACCEPTED
--                                     → REJECTED | EXECUTED | TIMEOUT
-- ============================================================
CREATE TABLE IF NOT EXISTS device_commands (
    id            SERIAL       PRIMARY KEY,
    command_id    TEXT         NOT NULL UNIQUE,
    device_id     TEXT         NOT NULL,
    cmd           TEXT         NOT NULL
                  CHECK (cmd IN ('START','STOP','FLUSH','RST')),
    status        TEXT         NOT NULL DEFAULT 'SENT'
                  CHECK (status IN ('SENT','RECEIVED','ACCEPTED',
                                    'REJECTED','EXECUTED','TIMEOUT')),
    issued_by     TEXT         DEFAULT 'api',
    retry_count   INTEGER      NOT NULL DEFAULT 0,
    issued_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    deadline_at   TIMESTAMPTZ,
    received_at   TIMESTAMPTZ,
    accepted_at   TIMESTAMPTZ,
    rejected_at   TIMESTAMPTZ,
    executed_at   TIMESTAMPTZ,
    timeout_at    TIMESTAMPTZ,
    last_ack_at   TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reject_reason TEXT,
    details       JSONB        DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_commands_device_time
    ON device_commands (device_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_commands_id
    ON device_commands (command_id);
CREATE INDEX IF NOT EXISTS idx_commands_active
    ON device_commands (device_id, status)
    WHERE status IN ('SENT','RECEIVED','ACCEPTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_commands_one_active_per_device
    ON device_commands (device_id)
    WHERE status IN ('SENT','RECEIVED','ACCEPTED');

-- ============================================================
-- MIGRACIÓN DESDE v3.2 (si ya tenés la DB):
-- ============================================================
-- ALTER TABLE device_config  ADD COLUMN IF NOT EXISTS daily_target_liters FLOAT DEFAULT 0;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_liters_today      FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_target_liters     FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_fulfillment_pct   FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_waste_liters_today FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_waste_pct         FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_risk_level        TEXT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_risk_score        FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_degradation_pct   FLOAT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_degradation_days  INT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_degradation_label TEXT;
-- ALTER TABLE device_status  ADD COLUMN IF NOT EXISTS biz_health_age_hours  FLOAT;
-- CREATE TABLE IF NOT EXISTS business_metrics ( ... ver definición arriba ... );

-- ============================================================
-- MIGRACIÓN DESDE v3.3 → v3.4 (si ya tenés la DB):
--
-- BREAKING: tds_in_raw / tds_out_raw eliminados.
-- No hay datos históricos reales — tablas borradas y recreadas
-- limpiamente. Si tuvieras datos que conservar:
--   1. Renombrar en telemetry_quality con ALTER TABLE ... RENAME COLUMN
--   2. Agregar columnas faltantes
--   3. Actualizar columna metrics
-- ============================================================

-- telemetry_quality — limpiar nomenclatura TDS
-- ALTER TABLE telemetry_quality DROP   COLUMN IF EXISTS tds_in_raw;
-- ALTER TABLE telemetry_quality DROP   COLUMN IF EXISTS tds_out_raw;
-- ALTER TABLE telemetry_quality ADD    COLUMN IF NOT EXISTS tds_in_voltage  FLOAT;
-- ALTER TABLE telemetry_quality ADD    COLUMN IF NOT EXISTS tds_out_voltage FLOAT;
-- ALTER TABLE telemetry_quality ADD    COLUMN IF NOT EXISTS tds_in_ppm      FLOAT;
-- ALTER TABLE telemetry_quality ADD    COLUMN IF NOT EXISTS tds_out_ppm     FLOAT;

-- metrics — alinear nombres con unidades reales
-- ALTER TABLE metrics RENAME COLUMN tds_in_raw  TO tds_in_ppm;
-- ALTER TABLE metrics RENAME COLUMN tds_out_raw TO tds_out_ppm;

-- device_config — calibración de sensores por dispositivo
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS flow_factor_1   FLOAT DEFAULT 450.0;
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS flow_factor_2   FLOAT DEFAULT 450.0;
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds_temperature FLOAT DEFAULT 25.0;

-- ============================================================
-- MIGRACIÓN v3.4 → v3.5: panel "Diagnóstico" simplificado (operador)
-- ============================================================

-- device_status — hasta 2 códigos de diagnóstico secundario para el panel
-- "Factores detectados" (cap=2 fijado en backend, ver DiagnosticEngine).
ALTER TABLE device_status ADD COLUMN IF NOT EXISTS secondary_diag_codes JSONB DEFAULT '[]'::jsonb;

-- ============================================================
-- TABLA: diagnostic_catalog
-- Traducción de códigos técnicos de diagnóstico a lenguaje operador,
-- usada por Grafana vía JOIN. DiagnosticResult.message/.action (texto
-- técnico) no se modifican — siguen alimentando diagnostics/alertas/AI.
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnostic_catalog (
    code            TEXT PRIMARY KEY,
    diagnostic_text TEXT NOT NULL,
    action_text     TEXT NOT NULL
);

INSERT INTO diagnostic_catalog (code, diagnostic_text, action_text) VALUES
  ('NORMAL',               'El equipo está funcionando correctamente.', 'No se requiere ninguna acción.'),
  ('CRITICAL_EFFICIENCY',  'El rechazo de sales de la membrana está fuera de los parámetros esperados (eficiencia muy baja).', 'Inspeccionar la membrana: posible rotura o ensuciamiento severo.'),
  ('LOW_EFFICIENCY',       'El rechazo de sales de la membrana bajó respecto de lo normal (eficiencia reducida).', 'Revisar la calidad del agua de entrada y el estado de la membrana.'),
  ('LOW_RECOVERY',         'Se está desperdiciando más agua de lo normal.', 'Ajustar la válvula de rechazo para mejorar el aprovechamiento del agua.'),
  ('HIGH_TDS_OUTPUT',      'La calidad del agua producida es baja.', 'Revisar el estado de la membrana: posible rotura o derivación (bypass).'),
  ('MEMBRANE_FOULING',     'La membrana parece estar tapada o sucia.', 'Ejecutar un ciclo de limpieza (lavado). Si persiste, reemplazar la membrana.'),
  ('MEMBRANE_SCALING',     'Hay indicios de incrustaciones (sarro) en la membrana.', 'Revisar dosificación de antiincrustante y programar limpieza química.'),
  ('MEMBRANE_DEGRADED',    'La membrana muestra signos de desgaste.', 'Evaluar el reemplazo de la membrana.'),
  ('LOW_PERMEATE_FLOW',    'El equipo está produciendo menos agua de lo normal.', 'Verificar la presión de entrada y el estado de la membrana.'),
  ('LOW_PRESSURE',         'La presión del sistema es más baja de lo normal.', 'Verificar la bomba de alta presión y las válvulas.'),
  ('HIGH_PRESSURE',        'La presión del sistema es más alta de lo seguro.', 'Detener el equipo y revisar la válvula de rechazo y la membrana.'),
  ('NO_PERMEATE_FLOW',     'El equipo está encendido pero no está produciendo agua.', 'Revisar bomba de alta presión, válvula de permeado y membrana.'),
  ('NO_RAW_WATER',         'Falta agua de alimentación mientras el equipo intenta funcionar.', 'Verificar el tanque de agua cruda y el sensor de nivel.'),
  ('FAULT_NO_WATER',       'El equipo no puede arrancar: falta agua de alimentación.', 'Verificar suministro de agua, nivel del tanque y válvula de entrada.'),
  ('FAULT_SYSTEM',         'El equipo se detuvo por una falla y no pudo reiniciar solo.', 'Reiniciar el equipo. Si la falla persiste, contactar a mantenimiento.'),
  ('SENSOR_INVALID',       'Hay una lectura de sensor que no parece correcta.', 'Revisar conexión y calibración de sensores.'),
  ('RESIDUAL_FLOW_STOPPED','Flujo detectado con el equipo detenido.', 'Posible fuga hidráulica o válvula que no cerró. Verificar tuberías y válvulas en los próximos minutos.')
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- MIGRACIÓN v3.5 → v3.6: causas específicas de FAULT (fault_reason)
-- ============================================================
-- El firmware ya publica fault_reason (MAX_RETRIES/FLOW_LOW/RECOVERY_LOW/
-- RECOVERY_HIGH) en el tópico "state". DiagnosticEngine._eval_events ahora
-- emite un código específico por causa en lugar de FAULT_SYSTEM genérico.
INSERT INTO diagnostic_catalog (code, diagnostic_text, action_text) VALUES
  ('FAULT_START_PRESSURE', 'El equipo no logró alcanzar la presión de membrana al arrancar, tras varios intentos.', 'Revisar la bomba de alta presión, las válvulas de entrada y el pressure switch. Verificar posibles obstrucciones.'),
  ('FAULT_LOW_FLOW',       'El equipo se detuvo durante la producción por caudal de permeado bajo.', 'Revisar la bomba de alta presión, posibles obstrucciones o fugas en la membrana, y el sensor de caudal.'),
  ('FAULT_RECOVERY_LOW',   'El equipo se detuvo: produce menos agua de la esperada en relación al agua de entrada (posible fuga o derivación).', 'Revisar fugas o derivación (bypass) en la membrana y el caudal de rechazo.'),
  ('FAULT_RECOVERY_HIGH',  'El equipo se detuvo: produce más agua de la esperada en relación al agua de entrada (posible obstrucción en el rechazo).', 'Revisar la válvula de rechazo (puede estar muy cerrada) y posibles obstrucciones en la línea de rechazo.')
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- MIGRACIÓN v3.6 → v3.7: calibración TDS configurable por canal
-- ============================================================
-- Infraestructura de calibración voltaje→ppm reemplazable (CAL_MODE_LINEAR),
-- por dispositivo y por canal (TDS1/TDS2). slope=0 (default) → firmware usa
-- el polinomio DFRobot como fallback (sin cambio de comportamiento hasta que
-- se cargue una calibración real vía /api/config).
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds1_cal_slope  FLOAT DEFAULT 0.0;
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds1_cal_offset FLOAT DEFAULT 0.0;
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds2_cal_slope  FLOAT DEFAULT 0.0;
-- ALTER TABLE device_config ADD COLUMN IF NOT EXISTS tds2_cal_offset FLOAT DEFAULT 0.0;