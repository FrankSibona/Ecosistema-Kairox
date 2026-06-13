# KAIROX — Plantilla de Relevamiento para Nuevas Instalaciones

**Versión:** 3.0
**Última revisión:** 2026-06-13
**Uso:** completar esta plantilla durante la visita/relevamiento de un cliente
nuevo, **antes** de tocar firmware o backend.

## Estructura

1. **Datos generales** — identificación del proyecto/equipo.
2. **Relevamiento físico** — inventario puro de lo que existe (sensores,
   actuadores, tanques, instrumentación). Solo "qué hay", sin comportamiento.
3. **Arquitectura hidráulica** — checklist de subsistemas presentes en la
   planta (pozo, ablandador, tanques, recirculación, CIP, dosificación).
4. **Diagrama de proceso** — secuencia hidráulica guiada, origen → RO →
   destino, en texto.
5. **Perfil funcional (cuestionario guiado)** — preguntas obligatorias sobre
   comportamiento deseado: demanda, producción, protección, regeneración,
   tanques, pozo, transferencia, flush, alarmas.
6. **Matriz de control** — resumen tabular: qué arranca/detiene cada equipo y
   sus interlocks.
7. **IO Mapping** — traducción de la matriz de control a señales lógicas,
   features y borneras físicas (D1–D6 / R1–R6).
8. **Configuración** — valores de `device_config` (parámetros/umbrales).
9. **Capacidad futura** — ampliaciones previstas, para reservar IO desde el
   inicio.
10. **Resumen ejecutivo** — síntesis final de toda la instalación.

El resultado completo permite **diseñar, cablear, configurar (Flask/MQTT/
`device_config`) y generar la hoja de conexiones** de una instalación, y
detectar si hace falta modificar firmware — sin entrevistas adicionales y sin
tocar firmware. Si el cuestionario detecta una necesidad que el catálogo
actual no cubre, se documenta en la sección 5.10 como pendiente de Fase 2 (no
bloquea la puesta en marcha del resto).

> No implementar lógica de firmware en base a este documento sin pasar por el
> proceso de control de cambios de `CLAUDE.md` (explicar impacto, riesgos,
> compatibilidad, generar migraciones si corresponde).

---

# 1. Datos generales del proyecto

| Campo | Valor |
|---|---|
| Cliente | |
| Ubicación / planta | |
| Fecha de relevamiento | |
| Realizado por | |
| `device_id` (ESP32_XXXXXXXXXXXX) | |
| Nombre visible (`display_name`) | |
| Organización Grafana / cliente (`clients.name`) — dejar vacío si es visible solo en Main Org | |
| Firmware objetivo | |
| Contacto técnico en planta | |

---

# 2. Relevamiento físico

Inventario **puro** de lo que existe realmente en la planta — solo "qué hay",
sin describir todavía cómo debe comportarse (eso es la sección 5) ni a qué
bornera KAIROX se conecta (eso es la sección 7).

## 2.1 Tanques y niveles

| Tanque | ¿Presente? | Capacidad aprox. | Sensor nivel alto (tipo / NA-NC) | Sensor nivel bajo (tipo / NA-NC) | Observaciones |
|---|---|---|---|---|---|
| Alimentación (agua cruda) | | | | | |
| Producto / permeado (intermedio) | | | | | |
| Final / distribución | | | | | |
| Pozo (nivel de napa) | | | | | |
| CIP (limpieza química) | | | | | |

## 2.2 Sensores de campo (entradas digitales)

Listar **todo** sensor/contacto seco disponible en la planta, presente o no
en el catálogo lógico actual. Si no encaja con ninguna señal del catálogo
(sección 7.1), anotarlo igual y derivarlo a la sección 5.10.

| # | Dispositivo de campo | Función observada | Tipo de contacto (NA/NC) | Tensión de la señal | Ubicación física | Observaciones |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |

## 2.3 Actuadores de campo (salidas)

Listar **todo** equipo controlable (bomba, válvula, dosificador) presente en
la planta.

| # | Equipo | Tipo (bomba/válvula/dosif.) | Potencia / tensión de alimentación | Tipo de contacto del relé que lo comanda | Observaciones |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |
| 6 | | | | | |

## 2.4 Instrumentación analógica

| Instrumento | ¿Presente? | Modelo / rango | Señal de salida (pulsos / 0-5V / 4-20mA) | Observaciones |
|---|---|---|---|---|
| Caudalímetro permeado (Q1) | | | | |
| Caudalímetro rechazo (Q2) | | | | |
| Transmisor de presión de membrana (P1) | | | | |
| Transmisor de presión de rechazo (P2) | | | | |
| Sensor TDS entrada | | | | |
| Sensor TDS salida | | | | |

---

# 3. Arquitectura hidráulica

Checklist de **subsistemas** presentes en la planta. Permite identificar
rápidamente el tipo de instalación antes de entrar al detalle funcional.

| Subsistema | ¿Presente? | Notas |
|---|---|---|
| Pozo (fuente de agua subterránea) | [ ] | |
| Tanque de alimentación (cisterna de agua cruda) | [ ] | |
| Ablandador | [ ] | |
| Ósmosis inversa (RO) | [x] siempre presente | — |
| Tanque de permeado (intermedio) | [ ] | |
| Tanque final / distribución | [ ] | |
| Bomba de transferencia | [ ] | |
| Recirculación (retorno de rechazo o permeado a un tanque) | [ ] | |
| Tanque CIP (limpieza química de membranas) | [ ] | |
| Dosificación química (anti-incrustante / biocida / pH) | [ ] | |

---

# 4. Diagrama de proceso

Describir la secuencia hidráulica completa en texto, usando los subsistemas
marcados en la sección 3. Sirve para entender el proceso sin mirar planos.

**Origen del agua:**

- [ ] Red
- [ ] Pozo
- [ ] Cisterna
- [ ] Otro: ____

**Secuencia hidráulica** (completar cada paso con un subsistema de la
sección 3, en orden; agregar/quitar pasos según corresponda):

```
Origen (____________)
   ↓
____________
   ↓
____________
   ↓
RO (membrana)
   ↓
____________
   ↓
____________
   ↓
Destino final (____________)
```

**Recirculación / derivaciones** (si aplica): describir qué corriente
(rechazo/permeado) retorna a qué tanque y bajo qué condición.

---

# 5. Perfil funcional (cuestionario guiado)

Responder **todas** las preguntas obligatorias (marcadas **[OBLIGATORIO]**).
Cada pregunta indica su impacto (`→`) sobre `features` / señal lógica /
`device_config`. Las respuestas de esta sección alimentan la Matriz de
control (sección 6) y, desde ahí, el IO Mapping (sección 7) y la
Configuración (sección 8).

## 5.1 Demanda

- **[OBLIGATORIO]** ¿Quién/qué genera la señal de demanda? → señal lógica
  `demand`
  - [ ] Presostato/flotante de tanque de producto (arranca al bajar el nivel)
  - [ ] Señal externa de un PLC/sistema de terceros
  - [ ] Botón/selector manual
  - [ ] Otro: ____
- **[OBLIGATORIO]** ¿Qué significa "pedir agua" en esta planta? (describir en
  una frase — ej. "el tanque de producto bajó del 80%")
- **[OBLIGATORIO]** ¿La señal de demanda es NA o NC en reposo (sin demanda)?
  - [ ] NA (cierra cuando hay demanda) [ ] NC (abre cuando hay demanda)
- ¿La demanda se mantiene activa por tiempos largos (horas) o es siempre
  breve/intermitente?

## 5.2 Producción

- **[OBLIGATORIO]** ¿Qué condiciones **permiten** producir? (marcar todas las
  que apliquen)
  - [ ] Agua cruda disponible → `raw_water_available` / `feed_tank_low` (en
    estado normal)
  - [ ] Presión de alimentación OK (presostato) → `pressure_ok`
  - [ ] Ablandador NO regenerando → `softener_regenerating` (en estado
    normal)
  - [ ] Tanque final NO lleno → `final_tank_high` (en estado normal)
  - [ ] Otro permisivo: ____
- **[OBLIGATORIO]** ¿Qué condiciones **bloquean/impiden** producir
  (interlocks)? Describir.
- **[OBLIGATORIO]** Orden de arranque de los actuadores al iniciar producción
  (ej.: 1. `inlet_valve`, 2. `high_pressure_pump`):
  1. ____
  2. ____
  3. ____
- ¿Hay un retardo deseado entre etapas de arranque? (el FSM actual no
  soporta retardos por etapa — si se requiere, documentar en 5.10)

## 5.3 Protección

- **[OBLIGATORIO]** ¿Qué fallas deben **detener el sistema** (`FAULT` /
  `stopAll()`)? Marcar las que apliquen — todas excepto
  `PRESSURE_MEMBRANE_HIGH` están **siempre activas** (lógica FSM base):
  - [ ] Caudal de permeado bajo sostenido → `FaultReason::FLOW_LOW` (sección
    8.2)
  - [ ] % recovery fuera de rango sostenido → `RECOVERY_LOW`/`RECOVERY_HIGH`
    (sección 8.2)
  - [ ] Presión de membrana alta sostenida → `PRESSURE_MEMBRANE_HIGH`
    (requiere sensor P1, sección 8.3)
- **[OBLIGATORIO]** ¿Hay transmisor de presión en la línea de membrana (P1)?
  → `pressure_membrane_enabled`
  - Si sí: rango del transmisor (V o mA) y presión máxima admisible del
    sistema → `pressure_membrane_high_limit` (sección 8.3). Ante
    sobrepresión, el comportamiento es **siempre** `FAULT`/parada de
    emergencia (no configurable por planta).
- ¿Hay transmisor de presión en la línea de rechazo (P2)?
  → `pressure_brine_enabled`
- ¿Se requiere monitoreo de ΔP membrana−rechazo (detección de
  ensuciamiento/fouling)? Requiere P1 y P2 instalados.
  → `feature_delta_pressure` + `delta_p_alarm_*` (sección 8.3 — solo alarma,
  no detiene el equipo)

## 5.4 Regeneración (ablandador)

- **[OBLIGATORIO]** ¿La planta tiene ablandador de agua? (ver sección 3)
  - [ ] Sí [ ] No — si "No", omitir el resto de este bloque.
- Si sí: ¿cómo se señaliza "regenerando"? (tipo de contacto NA/NC, tensión)
  → señal lógica `softener_regenerating`
- **[OBLIGATORIO]** Mientras el ablandador regenera, el sistema debe:
  - [ ] Detener/impedir producción (interlock duro) →
    `feature_softener_interlock`
  - [ ] Solo registrar/alertar, sin detener producción
  - [ ] Ignorar (no aplica)

## 5.5 Tanques

- **[OBLIGATORIO]** Para cada tanque marcado presente en 2.1, ¿qué nivel
  controla qué equipo? (completar — esto alimenta directamente la Matriz de
  control, sección 6)

| Tanque | Nivel | Señal lógica | Equipo que controla | Acción |
|---|---|---|---|---|
| Alimentación | Bajo | `feed_tank_low` | | |
| Alimentación | Alto | `feed_tank_high` | | |
| Permeado (intermedio) | Bajo | `permeate_tank_low` | | |
| Permeado (intermedio) | Alto | `permeate_tank_high` | | |
| Final / distribución | Bajo | `final_tank_low` | | |
| Final / distribución | Alto | `final_tank_high` | | |

- **[OBLIGATORIO]** Si NO hay sensor de nivel bajo en el tanque de
  alimentación: ¿qué pasa si se queda sin agua cruda? (documentar)

## 5.6 Pozo

- **[OBLIGATORIO]** ¿Hay una bomba de pozo que alimenta el tanque de
  alimentación? → `feature_well_pump` / señal lógica `well_pump`
  - Si sí: ¿arranca/para por nivel del tanque de alimentación (ver tabla
    5.5)?
  - ¿Hay protección de nivel bajo de pozo (`well_low_level`)? Si sí, ¿qué
    debe pasar si se activa (detener bomba de pozo / alarma)?

## 5.7 Transferencia

- **[OBLIGATORIO]** ¿Hay una bomba de transferencia (bomba elevadora) que
  envíe el permeado a un tanque final/distribución? →
  `feature_transfer_pump` / señal lógica `transfer_pump`
  - Si sí: ¿cuándo debe arrancar/detenerse? (ver tabla 5.5 — por nivel del
    tanque final, o continuamente mientras hay producción, u otro criterio)
  - ⚠️ **Nota de estado actual:** el firmware vigente no controla esta salida
    automáticamente (sin lógica FSM asociada). El cableado puede preverse,
    pero el accionamiento real queda pendiente de Fase 2 (documentar en
    5.10 si este caso aplica).

## 5.8 Flush

- **[OBLIGATORIO]** ¿Cómo debe comportarse el ciclo de flush/lavado
  (`FLUSHING`)? Frecuencia y duración deseada — el FSM ya soporta este
  estado, confirmar si los parámetros estándar aplican o requieren ajuste.
- ¿Hay alguna condición que deba **impedir** el flush (ej. tanque final
  lleno, sin agua de alimentación)?

## 5.9 Alarmas

- **[OBLIGATORIO]** ¿Qué eventos deben notificarse para esta planta? (ver
  detalle de cada uno en sección 8.4)
  - [ ] `HIGH_TDS_OUTPUT` (calidad de permeado) — requiere sensor TDS salida
  - [ ] `LOW_EFFICIENCY` (recovery bajo sostenido)
  - [ ] `BRINE_HIGH_PRESSURE_ALARM` — requiere P2
  - [ ] `DELTA_P_ALARM` (fouling) — requiere P1 + P2
  - `DEVICE_OFFLINE`, `SENSOR_INVALID`, `NO_PERMEATE_FLOW`,
    `RESIDUAL_FLOW_STOPPED`, `LOW_PRESSURE`/`HIGH_PRESSURE` son automáticas,
    no requieren decisión del cliente.
- **[OBLIGATORIO]** ¿Se debe configurar `telegram_chat_id` para
  notificaciones? ¿Quién recibe las alertas?
- ¿Modo IA deseado (`ai_mode`)? OFF / VIEWER / AUTO.

## 5.10 Casos no cubiertos por el catálogo actual (Fase 2)

Listar aquí cualquier respuesta de 5.1–5.9 que **no** pueda resolverse solo
con `io_map` + `features` + `device_config` actuales (p. ej. retardos por
etapa, control real de `transfer_pump`, lógica condicional nueva). Esto NO
bloquea la puesta en marcha del resto del equipo — queda registrado para una
futura iteración de firmware, ver `docs/EQUIPMENT_PROFILES.md` y las reglas
de control de cambios de `CLAUDE.md`.

| Requerimiento del cliente | Por qué no está cubierto hoy | Workaround temporal | Prioridad |
|---|---|---|---|
| | | | |

---

# 6. Matriz de control

Resumen tabular de toda la lógica de operación: qué arranca y detiene cada
equipo, y sus interlocks. Se completa a partir de las secciones 5.2, 5.5,
5.6, 5.7 y 5.8. Es la entrada directa para el IO Mapping (sección 7).

| Equipo | Arranca cuando | Se detiene cuando | Interlocks |
|---|---|---|---|
| Bomba de pozo | | | |
| Bomba de alta presión (RO) | | | |
| Bomba de transferencia | | | |
| Válvula de entrada | | | |
| Válvula de flush | | | |
| Bomba dosificadora | | | |

> **Ejemplo de referencia (no completar, solo guía):**
> Bomba de pozo → arranca con `feed_tank_low`, se detiene con
> `feed_tank_high`, interlock `well_low_level`. Bomba de alta presión (RO) →
> arranca con `demand` + `pressure_ok`, se detiene al cesar `demand`,
> interlock `softener_regenerating`. Bomba de transferencia → arranca con
> `final_tank_low`, se detiene con `final_tank_high`, interlock
> `permeate_tank_low`.

---

# 7. IO Mapping

Traducción de la Matriz de control (sección 6) a señales lógicas, features y
borneras físicas.

## 7.1 Señales lógicas requeridas — entradas

Marcar "Sí" según las secciones 5 y 6. Catálogo completo
(`python_iot/io_catalog.py::LOGICAL_INPUTS`) — append-only, si falta una
señal nueva ver sección 5.10.

| Señal lógica | Origen | ¿Requerida? | Sensor físico asociado (de 2.2) |
|---|---|---|---|
| `demand` | 5.1 | | |
| `raw_water_available` | 5.2 | | |
| `pressure_ok` | 5.2 | | |
| `feed_tank_high` | 5.5 / 5.6 | | |
| `feed_tank_low` | 5.5 / 5.6 | | |
| `permeate_tank_high` | 5.5 | | |
| `permeate_tank_low` | 5.5 / 5.7 | | |
| `final_tank_high` | 5.5 / 5.7 | | |
| `final_tank_low` | 5.5 / 5.7 | | |
| `softener_regenerating` | 5.4 | | |
| `well_low_level` | 5.6 | | |
| `dosing_ok` | — | | |

## 7.2 Señales lógicas requeridas — salidas

Catálogo completo (`LOGICAL_OUTPUTS`).

| Señal lógica | Origen | ¿Requerida? | Actuador físico asociado (de 2.3) |
|---|---|---|---|
| `low_pressure_pump` | 6 | | |
| `high_pressure_pump` | 6 | | |
| `well_pump` | 5.6 / 6 | | |
| `transfer_pump` | 5.7 / 6 | | |
| `flush_valve` | 5.8 / 6 | | |
| `inlet_valve` | 5.2 / 6 | | |
| `dosing_pump` | — | | |

## 7.3 Features a habilitar

| Feature | Origen | ¿Habilitar? |
|---|---|---|
| `feature_well_pump` | 5.6 | |
| `feature_transfer_pump` | 5.7 | |
| `feature_softener_interlock` | 5.4 | |
| `feature_dosing` | — | |
| `feature_delta_pressure` | 5.3 | |

## 7.4 Mapeo físico de entradas (D1–D6)

El hardware KAIROX (ESP32 Ro4.0) tiene **6 entradas digitales físicas**
(3.3 V lógica, contacto seco). Asignar como máximo 6 señales marcadas "Sí"
en 7.1, priorizando con el cliente si sobran. El resto queda en 5.10.

| Bornera KAIROX | GPIO | Señal lógica asignada | Modo (pull-up/pull-down) | Invertir | Tipo de contacto (NA/NC) | Observaciones |
|---|---|---|---|---|---|---|
| D1 | 27 | | pull-up (default) | | | |
| D2 | 26 | | pull-up (default) | | | |
| D3 | 25 | | pull-up (default) | | | |
| D4 | 33 | | pull-down (default) | | | |
| D5 | 32 | | pull-up (default) | | | |
| D6 | 23 | | pull-up (default) | | | |

> Convención de polaridad: con `mode=pull-up` e `invert=0`, señal "activa" =
> contacto **abierto** (lectura HIGH), contacto seco a **GND**. Con
> `mode=pull-down` (D4) e `invert=0`, señal "activa" = contacto **cerrado**
> a **+3.3 V**. Si el sensor instalado tiene polaridad contraria, marcar
> `Invertir=1` en "Mapeo de E/S (avanzado)" — no requiere recableado.

## 7.5 Mapeo físico de salidas (R1–R6)

**6 salidas a relé** (contacto seco). Asignar como máximo 6 señales marcadas
"Sí" en 7.2.

| Bornera KAIROX | GPIO | Señal lógica asignada | Invertir | Tensión/carga de campo | Observaciones |
|---|---|---|---|---|---|
| R1 | 4 | | | confirmar en obra | |
| R2 | 16 | | | confirmar en obra | |
| R3 | 17 | | | confirmar en obra | |
| R4 | 18 | | | confirmar en obra | |
| R5 | 19 | | | confirmar en obra | |
| R6 | 2 | | | confirmar en obra | |

> Contacto seco NA del relé — cierra cuando la salida lógica está activa
> (salvo `Invertir=1`). La tensión/carga real (220 VAC bomba, 24 VDC válvula,
> etc.) la define el equipo de campo, confirmar con datasheet del relé
> instalado.

---

# 8. Configuración

Valores de `device_config` (panel Flask → "Configuración"), organizados
siguiendo el mismo orden temático que la sección 5 para trazabilidad
pregunta → parámetro. Completar solo los que difieran del default.

## 8.1 Identificación / KPIs operacionales

| Parámetro | Default | Valor para esta planta |
|---|---|---|
| `friendly_name` | "" | |
| `location` | "" | |
| `pump_power_kw` | 0.75 | |
| `cost_kwh` | 0.12 | |
| `cost_water_m3` | 0.80 | |
| `target_recovery` | 0.65 | |
| `target_efficiency` | 0.92 | |
| `daily_target_liters` | 0 | |

## 8.2 Producción — caudal y recovery (← 5.2 / 5.3)

| Parámetro | Default | Valor para esta planta |
|---|---|---|
| `flow_factor_1` / `flow_factor_2` | 450.0 / 450.0 | |
| `min_flow_lpm` / `max_flow_lpm` | 0.2 / 20.0 | |
| `flow_fault_delay_sec` | 30 | |
| `min_recovery_pct` / `max_recovery_pct` | 10.0 / 85.0 | |
| `recovery_fault_delay_sec` | 60 | |

## 8.3 Presión — protecciones y alarmas (← 5.3)

| Parámetro | Default | Valor para esta planta |
|---|---|---|
| `pressure_membrane_enabled` | false | |
| `pressure_membrane_min/max_voltage` | 0.5 / 4.5 | |
| `pressure_membrane_min/max_bar` | 0.0 / 14.0 | |
| `pressure_membrane_limits_enabled` | false | |
| `pressure_membrane_high_limit` | 12.0 | |
| `pressure_fault_delay_sec` | 3 | |
| `pressure_brine_enabled` | false | |
| `pressure_brine_min/max_voltage` | 0.5 / 4.5 | |
| `pressure_brine_min/max_bar` | 0.0 / 14.0 | |
| `pressure_brine_high_limit` | 8.0 | |
| `pressure_brine_alarm_enabled` | false | |
| `delta_p_alarm_enabled` | false | |
| `delta_p_alarm_limit` | 5.0 | |

## 8.4 Alarmas (← 5.9)

| Código | Descripción | Tipo | Parámetro asociado |
|---|---|---|---|
| `DEVICE_OFFLINE` / `DEVICE_RECONNECTED` | Conectividad MQTT | Automática | — |
| `SENSOR_INVALID` | Lectura fuera de rango físico posible | Automática | — |
| `RESIDUAL_FLOW_STOPPED` | Flujo residual tras detener bombas | Automática | — |
| `NO_PERMEATE_FLOW` | Sin flujo de permeado estando en producción | Automática | — |
| `LOW_PRESSURE` / `HIGH_PRESSURE` | Presión fuera de rango (KPI) | Automática | confirmar con backend (umbrales de `metrics`) |
| `HIGH_TDS_OUTPUT` | TDS de salida por encima de umbral — requiere sensor TDS salida | Configurable | umbral interno (confirmar con backend) |
| `LOW_EFFICIENCY` | Eficiencia/recovery por debajo de umbral sostenido | Configurable | `target_efficiency` (referencia) |
| `MEMBRANE_HIGH_PRESSURE_ALARM` | Mismo umbral que protección crítica (8.3), como aviso temprano | Configurable | `pressure_membrane_high_limit` |
| `BRINE_HIGH_PRESSURE_ALARM` | Presión de rechazo alta — requiere P2 | Configurable | `pressure_brine_alarm_enabled` + `pressure_brine_high_limit` |
| `DELTA_P_ALARM` | ΔP membrana−rechazo alto (fouling) — requiere `feature_delta_pressure` | Configurable | `delta_p_alarm_enabled` + `delta_p_alarm_limit` |

## 8.5 Instrumentación analógica — calibración (← 2.4)

| Canal | Parámetros de calibración (`device_config`) | Valores para esta planta |
|---|---|---|
| Q1 (caudal permeado) | `flow_factor_1` (pulsos/litro) | ___ (default 450.0 — YF-S201) |
| Q2 (caudal rechazo) | `flow_factor_2` (pulsos/litro) | ___ (default 450.0) |
| TDS entrada | `tds1_cal_slope`, `tds1_cal_offset`, `tds_temperature` | slope: ___ · offset: ___ · temp: ___ °C |
| TDS salida | `tds2_cal_slope`, `tds2_cal_offset` | slope: ___ · offset: ___ |

> `tds*_cal_slope=0` usa la fórmula DFRobot estándar (sin calibración propia).
> Dejar en 0 salvo que se haga calibración con soluciones patrón. Calibración
> de presión (P1/P2 voltaje→bar) ya está cubierta en 8.3.

---

# 9. Capacidad futura

Detectar posibles ampliaciones para **reservar IO desde el inicio** (dejar
borneras libres en 7.4/7.5 si corresponde).

- ¿Se prevén más sensores de entrada a futuro? ¿Cuáles y para cuándo?
- ¿Se prevén más actuadores/salidas a futuro?
- ¿Se prevén más tanques (intermedios, CIP, etc.)?
- ¿Se prevé dosificación química a futuro? → `feature_dosing` / `dosing_ok` /
  `dosing_pump`
- ¿Se prevé telemetría analógica adicional (más caudalímetros, presión, TDS)?
- Borneras D/R recomendadas a dejar libres para estas ampliaciones: ____

---

# 10. Resumen ejecutivo

Completar al finalizar el relevamiento. Debe permitir entender la instalación
completa en menos de 1 minuto, sin leer el resto del documento.

**Arquitectura** (de la sección 3 — listar subsistemas presentes):
- ____

**Features activadas** (de la sección 7.3):
- ____

**IO utilizadas** (de las secciones 7.4/7.5):
- Entradas: ___ / 6
- Salidas: ___ / 6

**Protecciones críticas habilitadas** (de la sección 5.3):
- ____

**Pendientes Fase 2** (de la sección 5.10):
- ____

**Notas generales / observaciones de comisionamiento:**
- ____

---

# Anexo — De relevamiento a configuración KAIROX

Con esta plantilla completa, la puesta en marcha consiste en:

1. **Alta del dispositivo** — confirmar `device_id`, `display_name`,
   `client_id`/organización (sección 1).
2. **IO Mapping** — sección 7 → cargar en panel Flask
   "Mapeo de E/S (avanzado)" (`/api/iomap/<device_id>`): features (7.3) y
   asignación de borneras (7.4/7.5).
3. **Configuración** — sección 8 → panel Flask "Configuración"
   (`/api/config/<device_id>`).
4. **Pendientes (5.10)** — si hay ítems, documentarlos como propuesta de
   ampliación (nuevo perfil de equipo / nueva regla de FSM) siguiendo
   `docs/EQUIPMENT_PROFILES.md` y las reglas de control de cambios de
   `CLAUDE.md` — **no implementar directamente** sin pasar por ese proceso.

Generar una hoja de conexiones de campo específica (ver
`docs/CHAMICO_CONEXIONES_CAMPO.md` como ejemplo) a partir de las secciones
7.4 y 7.5 ya completadas, para uso del electricista en obra.
