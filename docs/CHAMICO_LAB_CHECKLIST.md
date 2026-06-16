# Checklist de validación — Chamico (placa de laboratorio)

Pre-requisitos:

- Firmware **≥ 1.2.0** flasheado en la placa de banco. 1.2.0 introduce:
  - `ProcessConfig` (NVS `kx_proccfg`, MQTT `process_config` retained): los
    timers de FSM `LOW_PUMP_FILL_TIME`/`PRESSURE_CHECK_TIME`/`RETRY_DELAY`/
    `FSM_MAX_RETRIES`/`FLUSH_TDS_TIME` son ahora configurables en runtime
    (defaults idénticos a los hardcodeados anteriores).
  - `flow_protection_enabled` / `recovery_protection_enabled` en `SensorConfig`
    (default=1 — sin cambio de comportamiento en equipos existentes).
    `CFG_VERSION` bumpeado 1→2: cualquier config guardada en NVS con versión
    anterior se descarta y se restaura desde el MQTT retained `/config`.
  - 1.1.9 bumpeó `IOMAP_VERSION` 2→3 (desacople IO/FSM). Si se actualiza
    desde 1.1.7/1.1.8, **re-importar el perfil es obligatorio** (ver abajo).
- Backend corriendo, broker MQTT accesible, `mosquitto_sub` disponible para
  observar `fyntek/{device_id}/state`, `/outputs`, `/iomap`, `/rules`.
- Perfil `docs/chamico_lab_config.json` importado vía
  `POST /api/profile/<device_id>` (un solo POST — ver tarjeta "Perfil de
  instalación" en la UI). Confirmar en Serial:
  - `[IOMAP] Guardado en NVS — updated_at=N`
  - `[IOMAP] pinMode aplicado en caliente: input[...] -> GPIOx` (x6, una por
    señal nueva) y `output[...] -> GPIO12` (transfer_pump)
  - `[RULES] Guardado en NVS — updated_at=N`
- No requiere reboot tras el import (validado por el punto 2 — pinMode en
  caliente para señales que pasan de `IOMAP_GPIO_NONE` a GPIO real).

Suscribirse antes de empezar:

```
mosquitto_sub -v -t 'fyntek/<device_id>/state' -t 'fyntek/<device_id>/outputs'
```

---

## Caso 1 — Arranque por demanda de permeado

Objetivo: `permeate_tank_demand=1` + `softener_regenerating=0` habilita
`process_permits["ro"]` → RO entra en STARTING → PRODUCING (sujeto a
`presionOK`, sin cambios respecto a 1.1.5).

| Paso | Acción | Esperado |
|---|---|---|
| 1 | Forzar `permeate_tank_demand=0` (GPIO5 abierto/pull-up) | `state=IDLE` |
| 2 | Cerrar contacto en GPIO5 (`permeate_tank_demand=1`) | Serial: `[EVENT] Demanda detectada -> arranque`; `state=STARTING` |
| 3 | Esperar `pressure_stabilization_delay_sec` (default 10s) | Bomba baja presión (R1) ON |
| 4 | Esperar `startup_timeout_sec` (default 5s) con presostato OK (GPIO33) | Serial: `[EVENT] Presión OK`; `state=PRODUCING`; R2 (alta presión) ON |
| 5 | `/state` MQTT | `state="PRODUCING"`, `fault_reason=""` |

Si el presostato no cierra a tiempo: `retryCount++`, `state=IDLE`,
`[FAULT] Presión no alcanzada` — comportamiento idéntico a 1.1.5 (punto 1,
no afectado por `process_permits`).

---

## Caso 2 — Regeneración de ablandador

Objetivo: `softener_regenerating=1` → `process_permits["ro"]` pasa a
`false` (término `NOT softener_regenerating`) → RO no puede permanecer en
PRODUCING; simultáneamente `independent_outputs["well_pump"]` se activa por
el término `softener_regenerating` (OR).

| Paso | Acción | Esperado |
|---|---|---|
| 1 | Con RO en PRODUCING (caso 1 completo) | `state=PRODUCING`, well_pump según `ro_producing` (ON) |
| 2 | Cerrar contacto en GPIO23 (`softener_regenerating=1`) | `permitOk=false` → Serial `[FAULT] Pérdida condición`; `state=IDLE`; R1/R2 OFF |
| 3 | Verificar well_pump | Sigue **ON** — `independent_outputs["well_pump"] = ro_producing OR softener_regenerating`; aunque `ro_producing` ya es `false`, `softener_regenerating=1` lo mantiene ON |
| 4 | `/outputs` MQTT (`pump_inlet`) | `true` |
| 5 | `permeate_tank_demand=1` sostenido durante este paso | RO permanece en IDLE — no reintenta STARTING mientras `softener_regenerating=1` |

Nota de instrumentación: `transfer_pump` no se publica en `/outputs` (no es
parte de `OutputsState`) — verificar con multímetro/LED en GPIO12 si no hay
visibilidad MQTT directa.

---

## Caso 3 — Rearranque automático al finalizar regeneración

Objetivo: al volver `softener_regenerating=0` (con `permeate_tank_demand=1`
sostenido), `process_permits["ro"]` vuelve a `true` y la FSM rearranca sola
desde IDLE — sin intervención manual ni comando.

| Paso | Acción | Esperado |
|---|---|---|
| 1 | Continuar desde caso 2 (`state=IDLE`, `softener_regenerating=1`, `permeate_tank_demand=1`) | — |
| 2 | Abrir contacto GPIO23 (`softener_regenerating=0`) | `permitOk` vuelve a `true` (AND ya no es bloqueado por NOT) |
| 3 | Observar siguiente ciclo de `loop()` | `state: IDLE -> STARTING` automático (sin comando externo), igual que caso 1 paso 2 |
| 4 | well_pump | Pasa a depender solo de `ro_producing` — OFF hasta que `state=PRODUCING` de nuevo |
| 5 | Verificar `retryCount` | Debe estar en 0 si la transición previa a IDLE fue por `permitOk=false` (no por falla de presión) — confirmar que no quedó un retry pendiente bloqueando el rearranque |

Si `retryCount > 0` por una falla de presión previa no relacionada, el
rearranque puede demorar hasta `retry_interval_sec` (default 10s) —
comportamiento normal de la FSM (sin cambios), no específico de Chamico.

---

## Caso 4 — Transferencia al tanque final

Objetivo: `independent_outputs["transfer_pump"]` = `final_tank_demand AND
permeate_tank_low` se evalúa cada loop, independiente del estado de RO
(IDLE, STARTING, PRODUCING, FLUSHING).

| Paso | Acción | Esperado |
|---|---|---|
| 1 | `final_tank_demand=0` (GPIO15), `permeate_tank_low=0` (GPIO22) | transfer_pump (GPIO12) OFF |
| 2 | Cerrar solo GPIO15 (`final_tank_demand=1`) | transfer_pump sigue OFF (AND no satisfecho) |
| 3 | Cerrar también GPIO22 (`permeate_tank_low=1`), ambos =1 | transfer_pump ON (GPIO12 en alto) |
| 4 | Abrir GPIO22 (`permeate_tank_low=0`) | transfer_pump OFF inmediatamente |
| 5 | Repetir con RO en PRODUCING (caso 1) | transfer_pump responde igual — regla independiente de `state` de RO |

Verificación: multímetro/LED en GPIO12 (no hay publicación MQTT de
`transfer_pump` en `/outputs` — ver nota de instrumentación del caso 2).

---

## Caso 5 — Falla de fase

Objetivo: `fault_rules[0]` (`phase_failure` sostenido ≥ `delay_sec=1s`) →
`state=FAULT`, `faultReason=PHASE_FAILURE`. No afecta
`process_permits`/`independent_outputs` — pipeline independiente.

| Paso | Acción | Esperado |
|---|---|---|
| 1 | Con RO en PRODUCING (caso 1) | `state=PRODUCING` |
| 2 | Cerrar contacto GPIO21 (`phase_failure=1`) por < 1s y volver a abrir | Sin efecto — `faultRuleArmed[0]` se resetea antes del debounce |
| 3 | Cerrar contacto GPIO21 y sostener > 1s | Serial: `[FAULT] fault_rules[0] -> PHASE_FAILURE`; `state=FAULT` |
| 4 | `/state` MQTT | `state="FAULT"`, `fault_reason="PHASE_FAILURE"` |
| 5 | R1/R2 | OFF (`stopAll()` en FAULT) |
| 5b | well_pump / transfer_pump | `independent_outputs[]` se evalúa cada loop **independientemente del `state`** (incluso en FAULT) — well_pump pasa a OFF porque `ro_producing=false` en FAULT y (en este escenario) `softener_regenerating=0`; transfer_pump sigue respondiendo a `final_tank_demand AND permeate_tank_low` sin relación con el FAULT de RO |
| 6 | Abrir GPIO21 (`phase_failure=0`) | `state` permanece en `FAULT` — requiere reset manual (`CommandType::RST`), igual que otras fault_rules |
| 7 | Enviar comando RST | `state=IDLE`, `faultRuleArmed[]` reseteado, listo para nuevo ciclo |

---

## Notas generales de la sesión de laboratorio

- Cada caso es independiente — si uno falla, documentar: `state` antes/
  después, salida de Serial completa del evento, y payload `/state` +
  `/outputs` capturado con `mosquitto_sub`.
- `pressure_ok` (GPIO33, presostato) debe estar en condición OK para
  cualquier transición STARTING→PRODUCING (casos 1, 3) — si la placa de
  banco no tiene presostato real, puentear GPIO33 a nivel alto (pulldown,
  invert=0 → activo en alto) para simular `presionOK=true`.
- Reportar cualquier caso donde una transición de `state` NO esté precedida
  por el log `[EVENT]`/`[FAULT]` correspondiente en Serial — indicaría una
  transición de FSM no cubierta por las reglas configuradas.
