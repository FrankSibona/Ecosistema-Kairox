# KAIROX — Perfiles / Plantillas de equipo (Fase 2, propuesta de arquitectura)

**Estado:** No implementado. Documento de diseño para guiar la Fase 2.
**Depende de:** capa Pin↔Señal lógica + features (Fase 1, implementada — ver
`python_iot/io_catalog.py`, `firmware/Ro4.0-V1/src/io/io_map.*`, `/api/iomap`).
**Last updated:** 2026-06-13

---

## Objetivo

Permitir configurar instalaciones con variantes hidráulicas distintas (bomba de
pozo, ablandador, transferencia, tanque intermedio, equipos industriales a
medida, etc.) **sin programar firmware nuevo por instalación**, reutilizando:

* el catálogo de señales lógicas (`LogicalInput`/`LogicalOutput`),
* el mapeo Pin↔Señal persistido (`io_map`),
* las `features` por dispositivo,

agregando una capa superior — el **perfil de equipo** — que actúa como
*plantilla* de `io_map` + `features` + (a futuro) reglas de operación
hidráulica.

Este documento NO introduce código. Define cómo encajaría la capa de perfiles
sobre la infraestructura de la Fase 1 ya implementada.

---

## Relación con la Fase 1 (io_map / features)

La Fase 1 ya provee:

* Catálogo append-only de señales (`io_catalog.py` / `io_catalog.h`).
* `io_map` por dispositivo: qué GPIO físico corresponde a cada señal lógica,
  persistido en NVS y Postgres (`devices.io_map`), sincronizado por
  `fyntek/{device_id}/iomap`.
* `features` por dispositivo: flags booleanos backend-only (`devices.features`),
  sin efecto en FSM todavía.

Un **perfil** es, conceptualmente, un *preset* de `io_map` + `features` para
una variante hidráulica conocida. Aplicar un perfil = poblar `io_map`/`features`
de un dispositivo con los valores del perfil (el dispositivo sigue pudiendo
editarlos individualmente después — el perfil es un punto de partida, no una
restricción).

La FSM y el control (`Sensors`/`Control`) **no cambian** en esta fase ni en la
propuesta de Fase 2 inicial: las reglas de operación específicas por perfil
(p. ej. "si `feature_well_pump`, activar `WELL_PUMP` cuando `well_low_level`
esté en bajo") son un paso *posterior* y deliberadamente fuera de este
documento.

---

## Modelo de datos propuesto

### Tabla `equipment_profiles` (catálogo, global — no por dispositivo)

| Columna | Tipo | Descripción |
|---|---|---|
| `profile_id` | TEXT PK | slug estable, p. ej. `ro_standard`, `ro_well_pump` |
| `label` | TEXT | nombre visible en UI, p. ej. "RO con bomba de pozo" |
| `description` | TEXT | texto explicativo para el instalador |
| `io_map_overrides` | JSONB | subconjunto de `io_map` (mismo formato que hoy) — solo las señales relevantes al perfil |
| `features` | JSONB | valores de `features` que define el perfil |
| `version` | INTEGER | para evolucionar plantillas sin romper dispositivos ya configurados |

Append-only por convención (igual que el catálogo de señales): no se borran
`profile_id` existentes, se versiona o se agregan nuevos.

### Columna nueva en `devices`

| Columna | Tipo | Descripción |
|---|---|---|
| `profile_id` | TEXT NULL, FK→`equipment_profiles.profile_id` | perfil aplicado. `NULL` = "personalizado" (estado actual de todos los equipos hoy — compatibilidad total) |
| `profile_applied_at` | TIMESTAMPTZ NULL | auditoría: cuándo se aplicó el perfil por última vez |

Un dispositivo con `profile_id = NULL` se comporta exactamente como hoy
(`io_map`/`features` editados manualmente, sin plantilla). Esto preserva
compatibilidad total con los equipos existentes sin migración obligatoria.

---

## Flujo de aplicación de un perfil

1. UI Flask: nuevo selector "Perfil de equipo" en la pestaña de configuración
   (junto a Mapeo de E/S), poblado desde `GET /api/profiles`.
2. Al seleccionar un perfil y confirmar (`POST /api/devices/{id}/profile`):
   * backend hace `merge` de `io_map_overrides` del perfil sobre el `io_map`
     actual del dispositivo (mismo `merge_io_map` ya existente — perfil
     completa/sobreescribe solo las señales que define).
   * backend hace `merge` de `features` del perfil sobre `features` actual.
   * se persiste `devices.profile_id` + `profile_applied_at`.
   * se reutiliza `_publish_device_iomap()` ya existente para sincronizar el
     `io_map` resultante al firmware vía `/iomap` (sin tocar el contrato MQTT).
3. El instalador puede seguir ajustando `io_map`/`features` individualmente
   desde la UI existente — aplicar un perfil no bloquea ediciones posteriores.
4. Cambiar de perfil es: aplicar el nuevo perfil (merge) — no resetea a cero
   ni borra señales fuera del nuevo perfil (evita sorpresas en equipos ya
   cableados).

Ningún paso de este flujo requiere cambios en FSM, `Sensors` o `Control`.

---

## Catálogo inicial de perfiles propuesto

Basado en las variantes mencionadas. La columna "Señales relevantes" indica
qué entradas/salidas del catálogo Fase 1 activa el perfil (vía
`io_map_overrides` — típicamente asignar GPIO a señales que en el perfil
"estándar" quedan `gpio: null`). La columna "Features" indica qué flags activa.

| `profile_id` | Label | Señales relevantes (además del set estándar) | Features |
|---|---|---|---|
| `ro_standard` | RO estándar | `demand`, `raw_water_available`, `pressure_ok`, `low_pressure_pump`, `high_pressure_pump`, `flush_valve`, `inlet_valve` (= wiring actual por defecto) | ninguna |
| `ro_well_pump` | RO con bomba de pozo | + `well_low_level` (in), `well_pump` (out) | `feature_well_pump` |
| `ro_softener` | RO con ablandador | + `softener_regenerating` (in) | `feature_softener_interlock` |
| `ro_transfer_pump` | RO con bomba de transferencia | + `permeate_tank_high`/`permeate_tank_low` (in), `transfer_pump` (out) | `feature_transfer_pump` |
| `ro_intermediate_tank` | RO con tanque intermedio | + `feed_tank_high`/`feed_tank_low` y/o `permeate_tank_high`/`permeate_tank_low` (in), según posición del tanque | combinable con `ro_transfer_pump` |
| `ro_industrial_custom` | RO industrial personalizada | — (no define `io_map_overrides`; punto de partida vacío) | todas disponibles, sin defaults — configuración 100% manual vía UI Mapeo de E/S |

Notas:

* Los perfiles **no son mutuamente excluyentes a nivel de dato** — un equipo
  real puede necesitar `ro_well_pump` + `ro_transfer_pump` simultáneamente
  (pozo aguas arriba + transferencia aguas abajo). El modelo de "un solo
  `profile_id`" cubre el caso simple; si se requiere combinar perfiles, una
  alternativa es modelar `equipment_profiles` como *componentes aditivos*
  (m:n con `devices`) en lugar de selección única — a decidir en Fase 2 según
  qué tan común sea la combinación en campo.
* `ro_industrial_custom` es esencialmente "sin perfil" (`profile_id = NULL`)
  con un nombre amigable en la UI — existe para que el instalador no tenga
  que dejar el selector vacío cuando el equipo es un caso único.

---

## Reglas de operación hidráulica (Fase 2+, fuera de este documento)

Una vez que `io_map`/`features`/`profile_id` estén en producción y validados
en campo, el siguiente paso (no diseñado aún) sería un **motor de reglas por
feature**, p. ej.:

* `feature_well_pump=true` → `Control` arranca `WELL_PUMP` antes de
  `LOW_PRESSURE_PUMP` cuando `FEED_TANK_LOW` o `RAW_WATER_AVAILABLE=false`, y
  lo detiene con `FEED_TANK_HIGH` o `WELL_LOW_LEVEL`.
* `feature_softener_interlock=true` → bloquear `STARTING` mientras
  `SOFTENER_REGENERATING=true` (nueva espera en FSM, similar a
  `RAW_WATER_AVAILABLE`).
* `feature_transfer_pump=true` → `TRANSFER_PUMP` controlada por
  `PERMEATE_TANK_LOW`/`PERMEATE_TANK_HIGH`, independiente del ciclo
  principal.

Esto **sí** requeriría tocar `Control`/FSM (nuevas transiciones o guardas
condicionadas por `features`), y por lo tanto cae bajo las reglas de control
de cambios de `CLAUDE.md` (explicar impacto, riesgos, compatibilidad antes de
implementar). Explícitamente fuera de alcance de la Fase 1 y de este
documento.

---

## Compatibilidad y migración

* `devices.profile_id NULL` (default) — ningún equipo existente se ve
  afectado. `equipment_profiles` puede no existir todavía sin romper nada
  (la UI simplemente no muestra el selector).
* Aplicar un perfil es una operación explícita e idempotente (merge), iniciada
  por el instalador — nunca automática.
* `io_map_overrides`/`features` de un perfil usan el mismo esquema y
  validación (`validate_io_map`/`validate_features`) que el `io_map`/`features`
  manual — ninguna ruta de datos nueva, solo un origen adicional de "valores
  propuestos".

---

## Fuera de alcance (recordatorio)

* Lógica específica de instalaciones puntuales (p. ej. Chamico) — no se
  modela como perfil genérico hasta tener ≥2 casos reales que confirmen el
  patrón.
* Motor de reglas hidráulicas / cambios de FSM (sección anterior).
* Tabla `equipment_profiles`, columna `devices.profile_id`, endpoints
  `/api/profiles` y `/api/devices/{id}/profile` — **no implementados**. Este
  documento es la referencia para cuando se decida iniciar esa fase.
