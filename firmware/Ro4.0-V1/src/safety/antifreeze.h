#pragma once
#include <stdint.h>

// ============================================================
// ANTI-CONGELAMIENTO — protección opcional, prioridad mínima del sistema.
// ============================================================
//
// Hace circular agua de pozo (vía bomba de baja presión + válvula de flush,
// sin presurizar membrana) cuando la temperatura ambiente cae por debajo de
// un umbral, para evitar congelamiento de agua estancada en la tubería
// mientras el equipo está detenido.
//
// Decisiones de diseño (ver docs de arquitectura de la sesión):
//   - NO es un SystemState nuevo. Vive enteramente dentro de Control::update(),
//     case IDLE, evaluado solo cuando esa iteración no arrancó por demanda.
//   - Prioridad mínima: se evalúa DESPUÉS del chequeo de arranque por demanda,
//     usa las MISMAS condiciones mínimas (crudoOK/permitOk) que un arranque
//     normal, y se aborta de inmediato ante cualquier transición fuera de
//     IDLE (demanda, comando manual START), y también ante STOP/RST recibidos
//     mientras el ciclo está activo (ver antifreezeAbort() y
//     Control::isValidTransition()/applyCommand()). pressure_ok NO participa
//     de las condiciones mínimas — es una señal específica del desempeño de
//     la bomba de ALTA presión (con su propio tiempo de estabilización), y
//     antifreeze nunca la enciende (flushOn() = baja presión + flush, sin
//     presurizar membrana). Exigirla bloquearía el feature permanentemente,
//     ya que en reposo (IDLE) esa señal lee "sin presión" por diseño.
//   - boot_inhibit_sec: sin evaluar hasta N segundos después del arranque del
//     ESP32 — evita que reinicios repetidos (microcortes/brownouts) disparen
//     un ciclo nuevo en cada boot. Implementado comparando directamente contra
//     millis() (now < boot_inhibit_sec*1000), sin estado persistido en NVS ni
//     dependencia de NTP.
//   - Cadencia fija de evaluación (antifreeze_eval_interval_sec), no
//     "flush + esperar": el reloj marca el intervalo ENTRE evaluaciones, no
//     entre fin de ciclo y la próxima. Una vez disparado un ciclo, corre su
//     duración completa (antifreeze_flush_duration_sec) sin reevaluarse a
//     mitad de camino.
//   - Histéresis de dos umbrales (temp_threshold_low_c / _high_c) para evitar
//     disparos repetidos cuando la temperatura oscila cerca del umbral.
//   - Persistencia: NVS namespace "kx_afreeze" + MQTT retained
//     fyntek/{device_id}/antifreeze_config — mismo patrón que SensorConfig/
//     RulesConfig/ProcessConfig (magic/version/updated_at, partial update).
//   - Con enabled=0 y sensor_enabled=0 (defaults), antifreezeEvaluate()
//     retorna false sin tocar GPIOs ni leer el sensor — cero impacto en
//     equipos sin DHT22.

struct AntifreezeConfig {
    uint32_t magic;
    uint32_t version;
    uint8_t  enabled;                   // protección completa on/off
    uint8_t  sensor_enabled;            // lectura del DHT22 on/off (independiente)
    uint8_t  sensor_gpio;               // GPIO digital bidireccional (no 34/35/36/39)
    float    temp_threshold_low_c;      // por debajo -> entra en riesgo (dispara ciclo)
    float    temp_threshold_high_c;     // por encima -> sale de riesgo (histéresis)
    uint32_t flush_duration_sec;        // duración de cada ciclo de circulación
    uint32_t eval_interval_sec;         // cadencia fija entre evaluaciones
    uint32_t boot_inhibit_sec;          // sin evaluar hasta N seg. después del boot
                                         // (debounce ante reinicios repetidos por
                                         // microcortes — ver antifreezeEvaluate())
    float    min_valid_temp_c;          // fuera de [min,max] -> lectura descartada
    float    max_valid_temp_c;
    uint8_t  max_consecutive_failures;  // lecturas inválidas seguidas -> sensor_fault
    uint32_t updated_at;
};

void                     antifreezeConfigInit();
const AntifreezeConfig&  antifreezeConfigGet();
bool                     antifreezeConfigSet(const AntifreezeConfig& incoming);

// Llamado una vez por tick desde Control::update(), case IDLE, SOLO en la
// rama donde no hubo arranque por demanda este tick. waterAvailable/
// processPermitted deben ser las mismas variables (crudoOK/permitOk) que
// gatean el arranque normal — antifreeze nunca actúa si un arranque real
// tampoco podría. Retorna true si el ciclo de circulación debe estar activo
// en este tick (Control decide qué outputs aplicar — este módulo no escribe
// GPIOs de actuadores).
bool antifreezeEvaluate(unsigned long now, bool waterAvailable, bool processPermitted);

// Aborta de inmediato cualquier ciclo en curso, sin esperar a que cumpla su
// duración. Llamar desde Control en cualquier punto donde la FSM toma el
// control de la planta por otra vía (arranque por demanda, comando manual
// START) — la protección debe ceder sin demora.
void antifreezeAbort();

// ── Telemetría ──────────────────────────────────────────────────────────
float    antifreezeGetTempC();         // NAN si no hay lectura válida
float    antifreezeGetHumidityPct();   // NAN si no hay lectura válida
bool     antifreezeIsSensorFault();
bool     antifreezeIsActive();         // true mientras un ciclo está en curso
