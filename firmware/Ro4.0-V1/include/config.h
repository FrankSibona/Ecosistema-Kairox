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
#define CFG_VERSION  2U            // v2: +flow_protection_enabled, +recovery_protection_enabled

// ── NVS io_map integrity ──────────────────────────────────────────────────────
// IOMAP_MAGIC/IOMAP_VERSION cubren el blob del mapeo Pin<->Señal lógica en NVS
// namespace "kx_iomap" (ver src/io/io_map.h). Mismo patrón que CFG_MAGIC/
// CFG_VERSION: si no coinciden, se descarta lo guardado y se usa el mapeo por
// defecto (reproduce el wiring actual D1-D6/R1-R6, ver defaultIOMap()).
#define IOMAP_MAGIC   0x4B584D41U  // 'K','X','M','A' — identifica el blob io_map
#define IOMAP_VERSION 3U           // v2: LogicalInput::COUNT 12->15 (permeate_tank_demand,
                                    // final_tank_demand, phase_failure) cambia sizeof(IOMapConfig).
                                    // v3: IOPinConfig += default_value/debounce_ms (desacople
                                    // IO/FSM — Sensors::getSignal() reemplaza demanda()/
                                    // crudoDisponible()/presionOK()). Bump fuerza fallback a
                                    // defaultIOMap() en equipos con NVS v1/v2, con log explícito
                                    // (en vez de fallo silencioso por tamaño). Equipos con io_map
                                    // custom (ej. lab1/Chamico) requieren re-importar el perfil
                                    // tras actualizar.

// ── NVS process config integrity ─────────────────────────────────────────────
// PROCCFG_MAGIC/PROCCFG_VERSION cubren el blob de parámetros de temporización
// de la FSM (antes hardcodeados en config.h como #define) en NVS namespace
// "kx_proccfg" (ver src/control/process_config.h). Defaults = valores exactos
// que tenían los #define originales → sin cambio de comportamiento en equipos
// sin process_config MQTT configurado.
#define PROCCFG_MAGIC   0x4B585043U   // 'K','X','P','C'
#define PROCCFG_VERSION 1U
#define PROCCFG_PRESSURE_STABILIZATION_DELAY_SEC_DEFAULT   5U  // seg. antes de encender bomba de alta
#define PROCCFG_STARTUP_TIMEOUT_SEC_DEFAULT               15U  // seg. totales en STARTING (debe ser > stabilization)
#define PROCCFG_RETRY_INTERVAL_SEC_DEFAULT                10U  // RETRY_DELAY/1000
#define PROCCFG_MAX_RETRIES_DEFAULT                        5U  // FSM_MAX_RETRIES
#define PROCCFG_FLUSH_DURATION_SEC_DEFAULT                60U  // FLUSH_TDS_TIME/1000

// ── Flags de habilitación de protecciones activas (CFG_VERSION 2) ─────────────
// Default = 1 porque son protecciones YA ACTIVAS en firmware anterior. Defaultear
// a 0 las desactivaría silenciosamente en todos los equipos tras actualizar.
// Contraste: pressure_membrane_limits_enabled default=0 (nueva feature, opt-in).
#define FLOW_PROTECTION_ENABLED_DEFAULT       1U
#define RECOVERY_PROTECTION_ENABLED_DEFAULT   1U

// ── NVS rules integrity ───────────────────────────────────────────────────────
// RULES_MAGIC/RULES_VERSION cubren el blob del motor de reglas (process_permits/
// independent_outputs/fault_rules) en NVS namespace "kx_rules" (ver
// src/rules/rules.h). Mismo patrón que IOMAP_MAGIC/IOMAP_VERSION: si no
// coinciden, se descarta lo guardado y se usan los defaults (defaultRules(),
// reproducen el comportamiento actual sin reglas configuradas).
#define RULES_MAGIC   0x4B58524CU  // 'K','X','R','L' — identifica el blob rules
#define RULES_VERSION 1U           // incrementar ante cambios de catálogo/struct

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

// ── Fallback de reconfiguración WiFi en campo (sin botón/reflash) ───────────
// Si el equipo YA tiene credenciales guardadas pero no logra conectar durante
// WIFI_FALLBACK_DELAY_SEC (debounce — evita abrir/cerrar el portal ante
// micro-cortes normales de WiFi), abre el portal WiFiManager en modo AP+STA
// (SSID/password derivados del device_id, ver comms.cpp) y lo deja abierto
// SIN timeout mientras dure la desconexión. La STA sigue reintentando las
// credenciales guardadas en background — apenas reconecta (red original o
// credenciales nuevas vía portal), el portal se cierra automáticamente.
// No afecta el portal de primer arranque (rama sin credenciales guardadas en
// setupWiFi()), que sigue siendo bloqueante/único.
#define WIFI_FALLBACK_DELAY_SEC  20U     // seg. sin conexión -> abrir portal (debounce)
#define WIFI_PORTAL_HEAP_LOG_SEC 1800U   // log periódico de heap libre con portal abierto

// Cadencia de reintento de STA mientras el portal de fallback está abierto.
// WiFiManager deshabilita la interfaz STA al abrir el AP con la STA caída
// (_disableSTAConn=true), y WiFi.reconnect() es un no-op silencioso en ese
// estado — sin este reintento explícito (WiFi.begin(), que sí re-habilita STA)
// el equipo queda en AP-only indefinidamente aunque la red vuelva enseguida.
// 30s: suficientemente frecuente para recuperar rápido, suficientemente
// espaciado para no dejar el AP del portal inutilizable (radio compartida).
#define WIFI_PORTAL_STA_RETRY_SEC 30U

// Timeout del portal FORZADO por comando MQTT fyntek/{id}/wifi/reset (cambio
// de red planificado desde la plataforma). A diferencia del portal de
// fallback, este se abre con el equipo normalmente CONECTADO a la red vieja,
// por lo que no puede cerrarse por WL_CONNECTED — se cierra por este timeout
// o cuando WiFiManager lo cierra solo al guardar credenciales nuevas.
// 10 min: margen para que el técnico llegue al equipo, se asocie al AP y
// complete la carga. Vencido, el equipo vuelve SIEMPRE a operación normal —
// si quedó sin red, la lógica de fallback lo reabre y reintenta sola.
#define WIFI_FORCED_PORTAL_TIMEOUT_SEC 600U

// ── NVS antifreeze integrity ──────────────────────────────────────────────────
// AFREEZE_MAGIC/AFREEZE_VERSION cubren el blob de protección anti-congelamiento
// (ver src/safety/antifreeze.h) en NVS namespace "kx_afreeze". Mismo patrón que
// PROCCFG_MAGIC/PROCCFG_VERSION: si no coinciden, se descarta lo guardado y se
// usan los defaults (enabled=0 — sin impacto en equipos existentes).
#define AFREEZE_MAGIC   0x4B584652U   // 'K','X','F','R'
#define AFREEZE_VERSION 2U            // v2: +boot_inhibit_sec (debounce post-boot)

// Pin digital bidireccional libre para el bus 1-wire del DHT22. NO usar
// PIN_TDS1/PIN_TDS2/PIN_AIN0/PIN_AIN1 — son input-only en el ESP32 y no
// soportan el protocolo del DHT22 (requiere que el MCU tire la línea a GND
// para iniciar la lectura). GPIO21/22 (SDA/SCL nominales — I2C no usado en
// este firmware) confirmados libres y bidireccionales.
#define PIN_AFREEZE_DHT_DEFAULT 21U

#define AFREEZE_ENABLED_DEFAULT                   0U     // opt-in — sin impacto en equipos existentes
#define AFREEZE_SENSOR_ENABLED_DEFAULT             0U
#define AFREEZE_TEMP_THRESHOLD_LOW_C_DEFAULT     0.0f     // °C — por debajo: riesgo de congelamiento
#define AFREEZE_TEMP_THRESHOLD_HIGH_C_DEFAULT    3.0f     // °C — por encima: riesgo despejado (histéresis)
#define AFREEZE_FLUSH_DURATION_SEC_DEFAULT      300U      // 5 min de circulación por ciclo
#define AFREEZE_EVAL_INTERVAL_SEC_DEFAULT      3600U      // 1 hora entre evaluaciones (no entre fin de ciclo)
#define AFREEZE_BOOT_INHIBIT_SEC_DEFAULT        120U      // sin evaluar hasta 2 min después del boot (debounce brownouts)
#define AFREEZE_MIN_VALID_TEMP_C_DEFAULT      -40.0f      // rango de validez DHT22 (descarta sensor en falla)
#define AFREEZE_MAX_VALID_TEMP_C_DEFAULT       60.0f
#define AFREEZE_MAX_CONSECUTIVE_FAILURES_DEFAULT  5U      // lecturas inválidas seguidas -> sensor_fault

// Cadencia de lectura del DHT22, independiente de AFREEZE_EVAL_INTERVAL_SEC —
// mantiene la telemetría (ambient_temp_c, sensor_fault) fresca para monitoreo
// aunque la decisión de flush solo se reevalúe cada hora. DHT22 exige >=2s
// entre lecturas (datasheet); 5s deja margen.
#define AFREEZE_SENSOR_POLL_INTERVAL_MS         5000U

// ================= WATCHDOG =================
// Task watchdog del ESP32 — red de seguridad ante cuelgues reales (deadlock,
// bucle infinito, bloqueo de librería). 30s es deliberadamente holgado para
// no interferir con operación normal ni con conectividad degradada — solo
// debe disparar ante un loop() que deja de iterar por completo.
// Se alimenta únicamente desde el loop principal (ver main.cpp).
#define WATCHDOG_TIMEOUT_SEC 30U

