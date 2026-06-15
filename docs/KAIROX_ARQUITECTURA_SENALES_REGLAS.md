# KAIROX — Arquitectura de Señales y Reglas (Propuesta v1)

**Estado:** Propuesta de arquitectura — no implementado. Documento de diseño,
sin cambios de código.
**Depende de / extiende:** `python_iot/io_catalog.py`,
`firmware/Ro4.0-V1/src/io/io_map.*`, `docs/EQUIPMENT_PROFILES.md`.
**Last updated:** 2026-06-14

---

## 0. Resumen ejecutivo

El modelo actual tiene dos capas implementadas (catálogo de señales
`io_catalog`, mapeo físico `io_map`) pero **dormidas**: `Sensors`/`Control`
siguen leyendo GPIOs hardcodeados (`PIN_D1..PIN_D6`, `PIN_R1..PIN_R6`) y
decidiendo con `if` fijos (`crudoOK && presionOK`, `nivelBajo → R3`). Cada
instalación nueva (Chamico) requiere o "forzar" el catálogo existente (con
conflictos de pines, ver sección 5) o esperar una "Fase 2" sin definir.

Esta propuesta cierra el círculo con TRES cambios estructurales:

1. **Activar `io_map`** — `Sensors`/`Control` dejan de hardcodear
   `PIN_D*`/`PIN_R*` y resuelven cada señal lógica vía `ioMapGet()`. Como
   `defaultIOMap()` ya codifica el wiring actual, esto es un refactor interno
   sin cambio de comportamiento para equipos sin `io_map` personalizado.
2. **Agregar una capa de Reglas genérica** — dos arrays indexados por el
   catálogo existente, **no** slots nombrados:
   - `independent_outputs[]` — una regla AND/OR por cada `LogicalOutput`
     (well_pump, transfer_pump, dosing_pump, futuros UV/booster/recirc...).
   - `process_permits[]` — una regla AND/OR por cada proceso con FSM propia
     (hoy: 1, la RO).
   Cada regla es una expresión plana (AND/OR + negación) sobre **señales**
   del catálogo. Misma mecánica que `io_map`/`features`: JSONB en `devices`,
   retained MQTT, NVS, merge-con-defaults.
3. **Señales derivadas como ciudadanos de primera clase** — además de
   entradas físicas (`io_map`), una regla puede consumir señales derivadas
   del estado de un proceso (`ro_producing`, `ro_fault`, `ro_flushing`,
   futuro `cip_running`) o el resultado de OTRA regla `independent_outputs[]`
   (`transfer_active`), con el mismo mecanismo `DerivedSignal` (1 ciclo de
   delay para resultados de outputs — sección 2).

Con esto, **Chamico (y casos futuros, incluyendo procesos nuevos como CIP) se
configuran, no se programan** — sección 5.

| | Hoy | Propuesto |
|---|---|---|
| Señales | catálogo `io_catalog` (✅ implementado) | + `DerivedSignal` ampliado (estado de proceso + resultados de outputs) |
| Mapeo físico | `io_map` (✅ implementado, **dormido**) | activado, fuente única de verdad |
| Decisión de producción RO | `if (!crudoOK \|\| !presionOK)` hardcodeado (control.cpp:236) | `process_permits["ro"]` (mismo default) |
| Bomba de pozo | `digitalWrite(PIN_R3, nivelBajo)` hardcodeado (control.cpp:339) | `independent_outputs["well_pump"]` (mismo default) |
| Nueva instalación / nuevo proceso (CIP, UV...) | requiere nuevo `if` o nuevo campo en firmware | nuevo `LogicalOutput`/`DerivedSignal` (append mecánico) + configurar `rules` vía API |

---

## 1. Separación en capas funcionales

El principio "Alimentación / Producción / Manejo posterior" ya existe
**parcialmente, por accidente**: la bomba de pozo (R3) es la única salida que
opera fuera del `switch(state)` del FSM (control.cpp:334-339), independiente
de si la RO está en IDLE/PRODUCING/FAULT.

La propuesta GENERALIZA ese accidente en una definición formal de "proceso" y
en dos arrays genéricos de reglas (sección 3).

### 1.1 Definición formal de "proceso"

Un **proceso** es cualquier subsistema modelado como:

- **FSM propia** (opcional) — secuencia con estado/tiempos internos. Hoy
  existe UNA: `Control` (RO), estados IDLE/STARTING/PRODUCING/FLUSHING/
  STOPPING/FAULT.
- **Señales derivadas que expone** — booleanos calculados desde su estado
  interno, consumibles por cualquier regla del sistema (sección 2). RO
  expone hoy `ro_producing`; esta propuesta agrega `ro_fault`,
  `ro_flushing` (mismo mecanismo, más cobertura de estado).
- **Permisos/interlocks que la gatean** — entrada configurable que el propio
  proceso consulta para decidir transiciones (`process_permits[]`, sección
  3).

RO es "proceso #1" bajo esta definición, retroactivamente, sin más cambio de
código que exponer 2 señales derivadas nuevas. Un proceso futuro (CIP,
recirculación con secuencia propia) se agrega con el MISMO patrón: nueva FSM
+ sus derived signals + su entrada en `process_permits[]` — sección 9.

### 1.2 Subsistemas (metadata, no runtime)

Los tres bloques del usuario se modelan como **tag `subsystem`** sobre cada
entrada del catálogo (`LogicalInput`/`LogicalOutput`/`DerivedSignal`) —
puramente organizativo (UI, relevamiento); firmware no lo lee ni lo evalúa:

| `subsystem` | Qué agrupa | Array/índice asociado |
|---|---|---|
| `alimentacion` (pozo, cisterna, tanque alimentación, ablandador) | señales de nivel/disponibilidad + `well_pump` | `independent_outputs["well_pump"]` |
| `produccion` (RO, permisivos, dosificación) | señales de proceso RO + su FSM | `process_permits["ro"]` |
| `distribucion` (tanque final, transferencia) | señales de nivel final + `transfer_pump` | `independent_outputs["transfer_pump"]` |

Las dependencias ENTRE subsistemas (p. ej. "la regeneración del ablandador,
parte de Producción, requiere que la bomba de pozo de Alimentación siga
funcionando") se expresan como TÉRMINOS de `independent_outputs["well_pump"]`
que referencian señales/derivadas de Producción (`ro_producing`,
`softener_regenerating`) — sección 5. El tag `subsystem` hace esa dependencia
cruzada VISIBLE en la UI, sin cambiar su evaluación. No hay lógica fija
acoplando subsistemas en firmware.

---

## 2. Modelo de señales

**Estado actual** (confirmado en código):

- `io_catalog.h`/`.py` — 12 `LogicalInput` + 7 `LogicalOutput`, append-only,
  sincronizados por nombre entre Python y C++. **No cambia.**
- `io_map.h/.cpp` — `{gpio, mode, invert}` por señal, persistido en NVS
  (`kx_iomap`) y sincronizado por MQTT retained `fyntek/{id}/iomap`
  (comms.cpp:148-185, **activamente suscripto y aplicado**). **No cambia su
  formato.**
- `defaultIOMap()` (io_map.cpp:11-39) **ya codifica el wiring físico actual**:
  `DEMAND→D1`, `RAW_WATER_AVAILABLE→D2`, `DOSING_OK→D3`(sin uso),
  `PRESSURE_OK→D4`, `WELL_LOW_LEVEL→D5`; salidas
  `LOW/HIGH_PRESSURE_PUMP→R1/R2`, `WELL_PUMP→R3`, `DOSING_PUMP→R4`,
  `FLUSH_VALVE→R5`, `INLET_VALVE→R6`. `SOFTENER_REGENERATING`,
  `TRANSFER_PUMP`, D6 sin mapear.
- **El problema:** `Sensors`/`Control` nunca llaman `ioMapGet()`. Leen
  `digitalRead(PIN_D1..D6)` (sensors.cpp:376-381) y exponen getters
  hardcodeados 1:1 (`demanda()=d1`, `crudoDisponible()=d2`, `presionOK()=d4`,
  `getNivelBajoPozo()=d5` — sensors.cpp:513-528). Reasignar una señal vía
  "Mapeo de E/S" hoy **no tiene efecto** sobre `Sensors`/`Control`.

**Propuesto — dos funciones genéricas** (firmware):

```cpp
// Sensors
bool readSignal(LogicalInput sig);
// 1) cfg = ioMapGet().inputs[sig]
// 2) si cfg.gpio == IOMAP_GPIO_NONE → return false
// 3) buscar cuál d1..d6 corresponde a cfg.gpio → aplicar cfg.invert

// Control (o nuevo módulo io_signals)
void writeSignal(LogicalOutput sig, bool value);
// 1) cfg = ioMapGet().outputs[sig]
// 2) si cfg.gpio == IOMAP_GPIO_NONE → no-op
// 3) digitalWrite(cfg.gpio, value ^ cfg.invert)
```

Como `defaultIOMap()` == wiring actual, un equipo **sin `io_map`
personalizado se comporta idéntico** tras este cambio — es un refactor
interno (Fase A, sección 6), sin nuevos campos DB/MQTT.

Los getters semánticos existentes se convierten en wrappers de 1 línea
(`demanda() { return readSignal(LogicalInput::DEMAND); }`) — `Control` **no
cambia sus llamadas** en este paso, solo cambia qué hay debajo. Esto habilita,
por primera vez, que las reasignaciones de "Mapeo de E/S" (como las
propuestas en `CHAMICO_CONEXIONES_CAMPO.md`) tengan efecto real.

**Señales derivadas (`DerivedSignal`)** — no vienen de GPIO/`io_map`, son
**ciudadanos de primera clase**: cualquier regla las consume con el mismo
`RuleTerm{signal_id, source=DERIVED, negate}` que usa para una entrada física
(`source=INPUT`) — sección 3. Catálogo separado, pequeño, append-only, con
dos orígenes:

1. **Estado de un proceso** (sección 1.1) — calculadas en vivo cada loop
   desde la FSM correspondiente:
   - `ro_producing` = `Control::isRunning()` (ya existe, control.cpp:18-20:
     `state == PRODUCING || STARTING || FLUSHING`).
   - `ro_fault` = `state == FAULT`, `ro_flushing` = `state == FLUSHING` —
     mismo mecanismo, agregan cobertura del estado de RO.
   - (futuro) `cip_running`, `cip_fault` cuando exista la FSM de CIP.

2. **Resultado de una regla `independent_outputs[]`** — p. ej.
   `transfer_active` = resultado evaluado de
   `independent_outputs["transfer_pump"]`. Para evitar un grafo de
   dependencias dinámico (incompatible con "sin recursión, sin heap"), estos
   se calculan con **1 ciclo de delay**: el valor expuesto como
   `DerivedSignal` en el loop N es el resultado evaluado en el loop N-1. A la
   tasa de loop del ESP32 (sub-100ms) es imperceptible para control de
   bombas/válvulas, y es un delay DOCUMENTADO y CONSTANTE — no un "hidden
   state transition".

Ambos orígenes pueblan el mismo array plano `bool derived[DerivedSignal::COUNT]`
al inicio de cada loop, ANTES de evaluar cualquier regla — `evalRule()` no
distingue el origen.

---

## 3. Modelo de reglas

**Forma:** expresión booleana **plana** (AND u OR, sin anidar) sobre una
lista acotada de términos `{signal, negate}`. `signal` es un nombre del
catálogo unificado — `LogicalInput` o `DerivedSignal` (sección 2) —
indistinguibles para quien escribe la regla:

```json
{
  "op": "AND",
  "terms": [
    {"signal": "raw_water_available"},
    {"signal": "pressure_ok"},
    {"signal": "softener_regenerating", "negate": true}
  ]
}
```

**Anidamiento** (AND-de-ORs, etc.): se resuelve **encadenando** — una regla
de `independent_outputs[]` produce un resultado que queda disponible como
`DerivedSignal` (1 ciclo de delay, sección 2) para que OTRA regla lo consuma
como término. El evaluador no se extiende: sigue siendo AND/OR plano, ≤4
términos, sin recursión — el "árbol" se construye COMPONIENDO slots, no
expresiones anidadas. Mantiene el firmware determinístico (CLAUDE.md: "FSM
only, no hidden transitions", "no dynamic memory allocation in loops").

**Catálogo de reglas — dos arrays genéricos, indexados por el catálogo
existente, NO slots nombrados:**

| Array | Tamaño | Índice | Efecto | Default = comportamiento actual |
|---|---|---|---|---|
| `process_permits[]` | nº de procesos con FSM propia (hoy **1**) | proceso (hoy: `"ro"`) | Si `false` durante STARTING/PRODUCING → `state=IDLE`, reset de arm-flags (mismo patrón que control.cpp:236-243) | `process_permits["ro"] = AND(raw_water_available, pressure_ok)` = hoy `crudoOK && presionOK` |
| `independent_outputs[]` | `LogicalOutput::COUNT` (hoy **7**) | cada `LogicalOutput` del catálogo | Evaluado cada loop, fuera de `switch(state)`, escribe el output vía `writeSignal()` | `independent_outputs["well_pump"] = AND(well_low_level)` = hoy `nivelBajo` (control.cpp:337-339); resto = `AND()` (0 términos ⇒ `false`, sin efecto) |

Representación JSON (mismo patrón que `io_map`: objeto keyed por nombre
lógico, firmware lo mapea a array fijo por índice de enum):

```json
{
  "process_permits": {
    "ro": {
      "op": "AND",
      "terms": [
        {"signal": "raw_water_available"},
        {"signal": "pressure_ok"}
      ]
    }
  },
  "independent_outputs": {
    "well_pump":    { "op": "AND", "terms": [{"signal": "well_low_level"}] },
    "transfer_pump": { "op": "AND", "terms": [] },
    "dosing_pump":   { "op": "AND", "terms": [] }
  }
}
```

**Outputs ya gobernados por la FSM de RO** (`low_pressure_pump`,
`high_pressure_pump`, `flush_valve`, `inlet_valve` — vía `setOutputs`/
`startLow`/`startHigh`/`flushOn`/`stopAll`) tienen su slot en
`independent_outputs[]` por uniformidad del array, pero **no se evalúan ni se
escriben** en Fase B — reservados, sin efecto, por si en el futuro se decide
exponerlos también vía reglas (fuera de alcance, sección 9).

**Crecimiento sin rediseño:**
- Agregar un proceso nuevo (CIP) = +1 entrada en `process_permits[]` junto
  con su FSM (sección 1.1, sección 9) — el array crece, el evaluador no
  cambia.
- Agregar un output nuevo (UV, booster, recirc) = ya tiene su slot en
  `independent_outputs[]` en cuanto exista como `LogicalOutput` en el
  catálogo (append mecánico) — sin nuevos campos de struct.
- Tipos de slot adicionales (p. ej. `START_BLOCK` para CIP — más estricto que
  `PRODUCTION_PERMIT`, bloquea STARTING en sí, no solo PRODUCING) quedan para
  cuando se diseñe ese proceso — sección 9.

**Dónde se evalúan:** en **firmware**, cada loop, determinístico, sin
dependencia de red — igual que hoy `crudoOK`/`presionOK`/`nivelBajo`. La
*configuración* de cada regla (términos, AND/OR, negaciones) viaja
backend→firmware con la MISMA mecánica que `io_map`/`device_config`: JSONB en
Postgres → retained MQTT → NVS. Respeta "MQTT es solo transporte" (la regla
configurada es dato) y "el backend es la autoridad" (autoría/almacenamiento),
sin sacrificar tiempo real ni tolerancia a desconexión — un equipo aislado de
red sigue evaluando sus reglas con la última config persistida en NVS.

---

## 4. Representación de instalaciones distintas

Una instalación = la tripleta **`{io_map, features, rules}`** por
dispositivo — mismo mecanismo para las tres (JSONB en `devices`,
merge-con-defaults, retained MQTT, NVS):

| Capa | Qué resuelve | Estado |
|---|---|---|
| `io_map` | qué GPIO es cada señal/salida | ✅ implementado (Fase 1) |
| `features` | qué capacidades/subsistemas TIENE la instalación (metadata UI/perfil) | ✅ implementado, sin efecto firmware — **sin cambios** |
| `rules` | CÓMO se comportan esas capacidades (los 4 slots de la sección 3) | 🆕 propuesto |

`features` no necesita cambiar de rol: sigue siendo la señal de "esta planta
tiene bomba de pozo / ablandador / transferencia" para la UI y para
`equipment_profiles` (EQUIPMENT_PROFILES.md). `rules` es la capa nueva que
realmente consume firmware.

`equipment_profiles` (EQUIPMENT_PROFILES.md, no implementado) se extiende
naturalmente: un perfil pasa a ser preset de
`{io_map_overrides, features, rule_overrides}` en vez de solo las primeras
dos — mismo flujo "aplicar perfil = merge" (EQUIPMENT_PROFILES.md líneas
83-101), sin rediseño.

**Caso límite ya resuelto por el merge existente:** una regla que referencia
una señal sin mapear en `io_map` (`gpio=None`) → `readSignal()` devuelve
`false`. Cada instalación define SUS PROPIOS términos (no hereda un default
oculto), así que una instalación que no usa `raw_water_available` simplemente
no la incluye en `process_permits["ro"]` — no hay "default roto" que
compensar.

---

## 5. Caso Chamico bajo este modelo

**Requisitos** (sin cambios respecto a lo planteado):

- RO produce cuando hay `demand`.
- RO se detiene durante `softener_regenerating`.
- Bomba de pozo funciona cuando RO produce **o** cuando el ablandador
  regenera (la regeneración consume agua de pozo).
- Bomba de pozo no depende del `state` de la RO — es una salida
  independiente, igual que hoy.

**Señales** (todas ya en el catálogo, ninguna nueva):

| Señal | Pin propuesto | Nota |
|---|---|---|
| `demand` | D1 | sin cambios respecto al wiring de fábrica |
| `pressure_ok` | D4 | sin cambios |
| `softener_regenerating` | **D6** (no D2) | ver corrección abajo |
| `feed_tank_high` / `feed_tank_low` / `final_tank_high` | D2/D3/D5 según relevamiento | libres si Chamico no usa `raw_water_available`/`well_low_level` con esos nombres |
| `ro_producing` | — (derivada) | `Control::isRunning()` |

**Reglas:**

```json
{
  "process_permits": {
    "ro": {
      "op": "AND",
      "terms": [
        {"signal": "pressure_ok"},
        {"signal": "softener_regenerating", "negate": true}
      ]
    }
  },
  "independent_outputs": {
    "well_pump": {
      "op": "OR",
      "terms": [
        {"signal": "ro_producing"},
        {"signal": "softener_regenerating"}
      ]
    }
  }
}
```

`process_permits["ro"]` omite `raw_water_available` porque Chamico no la usa
— cada instalación define su propio set de términos, no extiende un default
oculto (sección 4).

**Efecto:**

- Mientras `softener_regenerating=true` → `process_permits["ro"]=false` → si
  el FSM está en STARTING/PRODUCING, pasa a IDLE (mismo patrón que pérdida de
  `crudoOK`/`presionOK` hoy, control.cpp:236-243). Al terminar la
  regeneración, retoma producción automáticamente si `demand`/`pressure_ok`
  siguen OK — sin estado oculto ni temporizadores nuevos.
- `independent_outputs["well_pump"]` se evalúa **cada loop**, fuera del FSM —
  la bomba de pozo (R3) está ON si la RO está produciendo **o** el ablandador
  regenera, sin importar el `state` de la RO. Es la generalización directa
  del patrón YA EXISTENTE (well_pump independiente del FSM, hoy gobernado
  solo por `well_low_level`).

**Cero código Chamico-specific:** ambas reglas son instancias de los arrays
genéricos `process_permits[]` y `independent_outputs[]` (sección 3) que
cualquier instalación puede configurar con sus propios términos.

### ⚠️ Corrección pendiente en `CHAMICO_CONEXIONES_CAMPO.md`

La hoja actual propone `softener_regenerating`→**D2**. Bajo el modelo
actual (io_map dormido) esto no tiene efecto real; bajo el modelo nuevo
(io_map activo, mapeo 1:1 GPIO↔señal) sería un **conflicto**, porque D2 ya es
`raw_water_available` en `defaultIOMap()`. Recomendación: mover
`softener_regenerating`→**D6**, y decidir en el relevamiento si
`raw_water_available`(D2)/`well_low_level`(D5) se usan en Chamico o quedan
libres para `feed_tank_*`. No se modifica el documento todavía — queda
listado en la sección 8 (modificar).

---

## 6. Migración desde la arquitectura actual

Cuatro fases, cada una **aditiva y retrocompatible** — un equipo que no recibe
configuración nueva se comporta exactamente igual que hoy en todas las fases.

### Fase A — Activar `io_map` (refactor interno de firmware)

- `Sensors::readSignal(LogicalInput)` / `writeSignal(LogicalOutput, bool)` vía
  `ioMapGet()` (sección 2).
- Getters semánticos existentes (`demanda()`, `crudoDisponible()`,
  `presionOK()`, `getNivelBajoPozo()`) → wrappers de 1 línea.
- **Sin cambios de DB/MQTT/payload.** `defaultIOMap()` = wiring actual ⇒ cero
  cambio de comportamiento sin `io_map` personalizado.
- Habilita, por primera vez, que reasignaciones de "Mapeo de E/S (avanzado)"
  tengan efecto real en Sensors/Control.

### Fase B — Motor de reglas en firmware

- Nuevo módulo `rules.h/.cpp`: dos arrays genéricos — `process_permits[]`
  (tamaño = nº de procesos con FSM propia, hoy 1) e `independent_outputs[]`
  (tamaño = `LogicalOutput::COUNT`, hoy 7), sección 3. `RuleConfig` de tamaño
  fijo (≤4 términos, sin allocation dinámica), `evalRule()`, NVS `kx_rules`.
- Cálculo de `DerivedSignal` cada loop (sección 2): estado de proceso
  (`ro_producing`, `ro_fault`, `ro_flushing` desde `Control::getState()`) +
  resultados de `independent_outputs[]` del loop anterior (`transfer_active`,
  1 ciclo de delay) → array plano `derived[]`, poblado ANTES de evaluar
  reglas.
- Nuevo handler MQTT `fyntek/{id}/rules` (mismo patrón retained +
  subscribe/apply/persist que `/iomap`, comms.cpp:148-185).
- `control.cpp`: los 2-3 `if` hardcodeados (líneas 236, 337-339) pasan a
  llamar `evalRule(process_permits["ro"], ...)` /
  `evalRule(independent_outputs["well_pump"], ...)`. **Defaults reproducen
  exactamente la condición actual** ⇒ sin `rules` configurado (NVS vacío →
  defaults de fábrica), comportamiento idéntico.
- Bump `fw_version` (1.1.5 → 1.1.6).

### Fase C — Configuración por instalación (backend + Flask)

- `python_iot/rule_catalog.py` (mirror de `io_catalog.py`): `PROCESSES`,
  `INDEPENDENT_OUTPUTS`, `DERIVED_SIGNALS`, `DEFAULT_RULES`,
  `merge_rules()`/`validate_rules()` — mismas firmas que
  `merge_io_map`/`validate_io_map`.
- `ALTER TABLE devices ADD COLUMN rules JSONB DEFAULT '{}'` — mismo patrón
  que `io_map`/`features`, sin migración de filas existentes (merge-con-
  defaults al leer).
- `/api/rules/<device_id>` GET/POST + `_publish_device_rules()` — mirror de
  `/api/iomap` (app.py:3307-3374).
- Flask: panel "Reglas" junto a "Mapeo de E/S (avanzado)" — editor por slot
  (operador AND/OR + lista de términos con checkbox "Invertir").
- **Aplicar Chamico** (sección 5) vía este endpoint — sin tocar
  firmware/backend de nuevo.

### Fase D — Perfiles de equipo (opcional, ≥2 instalaciones reales)

- `equipment_profiles` + `devices.profile_id` (ya diseñado en
  `EQUIPMENT_PROFILES.md`), extendido con columna `rule_overrides` JSONB.
- Sin esta fase, Fases A-C ya son suficientes para operar cualquier
  instalación — los perfiles son solo un acelerador de UI para repetir
  combinaciones conocidas.

---

## 7. Impacto por capa

### Firmware

- **Nuevo** `src/io/rules.h/.cpp`: catálogo de slots + evaluador, tamaño
  fijo, sin allocation:

  ```cpp
  #define RULE_MAX_TERMS 4
  enum class RuleOp     : uint8_t { AND = 0, OR = 1 };
  enum class SignalSrc  : uint8_t { INPUT = 0, DERIVED = 1 };

  struct RuleTerm {
      uint8_t   signal_id;  // índice en LogicalInput o DerivedSignal
      SignalSrc source;
      uint8_t   negate;     // 0|1
  };

  struct RuleConfig {
      RuleOp  op;
      uint8_t term_count;            // 0..RULE_MAX_TERMS
      RuleTerm terms[RULE_MAX_TERMS];
  };

  #define PROCESS_COUNT 1   // hoy: solo "ro". CIP futuro → 2.
  enum class ProcessId : uint8_t { RO = 0 };

  struct RulesConfig {
      RuleConfig process_permits[PROCESS_COUNT];
      RuleConfig independent_outputs[(size_t)LogicalOutput::COUNT];
      uint32_t   updated_at;
  };

  // inputs[]/derived[] poblados una vez por loop (sección 2), ANTES de evalRule()
  bool evalRule(const RuleConfig& r, const bool* inputs, const bool* derived);
  // term_count == 0 → false (slot deshabilitado)
  // AND: acumula &&; OR: acumula ||  — sin recursión, sin heap
  ```

- **Modificado** `control.cpp`: 3 puntos de reemplazo —
  - línea 236 (`!crudoOK || !presionOK`) →
    `!evalRule(rules.process_permits[(int)ProcessId::RO], inputs, derived)`
  - líneas 337-339 (`nivelBajo → R3`) →
    `writeSignal(LogicalOutput::WELL_PUMP, evalRule(rules.independent_outputs[(int)LogicalOutput::WELL_PUMP], inputs, derived))`
  - nuevo (sin equivalente hoy):
    `writeSignal(LogicalOutput::TRANSFER_PUMP, evalRule(rules.independent_outputs[(int)LogicalOutput::TRANSFER_PUMP], inputs, derived))`
  - FSM (estados, transiciones, command engine) **sin cambios estructurales**.
- **Modificado** `comms.cpp`: nuevo handler `/rules` (mirror de `/iomap`,
  líneas 148-185), suscripción agregada en `reconnect()` (línea ~362), bump
  `fw_version`.
- **Modificado** `sensors.h/.cpp`: `readSignal()`/`writeSignal()` (sección
  2); getters existentes pasan a wrappers.
- **Sin cambios**: `io_map.h/.cpp`, `io_catalog.h/.cpp` (ya correctos y
  suficientes — `LogicalOutput::COUNT` ya está implícito en el enum).

### Backend (Python)

- **Nuevo** `python_iot/rule_catalog.py` — mirror de `io_catalog.py`:

  ```python
  PROCESSES = ["ro"]   # nombres de proceso con FSM propia, hoy 1

  INDEPENDENT_OUTPUTS = [o.name for o in LogicalOutput]   # 7 hoy

  DERIVED_SIGNALS = ["ro_producing", "ro_fault", "ro_flushing", "transfer_active"]

  DEFAULT_RULES = {
      "process_permits": {
          "ro": {"op": "AND", "terms": [
              {"signal": "raw_water_available"},
              {"signal": "pressure_ok"},
          ]},
      },
      "independent_outputs": {
          "well_pump":     {"op": "AND", "terms": [{"signal": "well_low_level"}]},
          "transfer_pump": {"op": "AND", "terms": []},
          "dosing_pump":   {"op": "AND", "terms": []},
      },
  }

  def merge_rules(stored): ...     # mismo patrón que merge_io_map
  def validate_rules(data): ...    # mismo patrón que validate_io_map; valida
                                    # que cada signal exista en LogicalInput ∪ DerivedSignal
  ```

- **Modificado** `app.py`: `/api/rules/<device_id>` GET/POST +
  `_publish_device_rules()` — mirror de `/api/iomap`/`_publish_device_iomap`
  (líneas 3307-3374), mismo `CONFIG_UPDATED` alert hook.
- **Sin cambios obligatorios**: `DiagnosticEngine`/`alert_config.py`. Un
  espejo de `evalRule()` en backend (para mostrar en UI "esta regla bloquea
  producción ahora" usando el último telemetry conocido) es **opcional y
  futuro** — de implementarse, es de solo lectura/diagnóstico, nunca
  autoritativo.

### Base de datos (PostgreSQL)

```sql
ALTER TABLE devices ADD COLUMN rules JSONB DEFAULT '{}';
```

Mismo patrón que `io_map`/`features`: aditivo, sin migración de filas
existentes, merge-con-defaults al leer.

### MQTT

- **Nuevo** topic retained `fyntek/{device_id}/rules` (config-direction,
  backend→firmware), mismo ciclo de vida que `/iomap`/`/config`. Dato puro —
  consistente con "MQTT es solo transporte".
- **Sin cambios** en contratos existentes (`/process`, `/diag`, `/iomap`,
  `/config`, `/cmd`). Campos opcionales adicionales en `/diag` (resultado de
  evaluación de reglas, para debug en campo) serían aditivos y
  retrocompatibles.

### Flask UI

- Nuevo panel "Reglas" junto a "Mapeo de E/S (avanzado)": por slot, selector
  AND/OR + lista de términos (señal + checkbox "Invertir"), con los defaults
  de la sección 3 pre-cargados y marcados como "default = comportamiento
  actual".
- `CHAMICO_CONEXIONES_CAMPO.md`-style: las hojas de conexiones por
  instalación ganan una sección "Reglas configuradas".

### Grafana

- **Sin impacto.** Las Fases A-C no agregan campos de telemetría por
  default; cualquier campo de diagnóstico de reglas futuro sería aditivo
  (nuevas claves, paneles existentes no se ven afectados).

---

## 8. Documentación existente: conservar / modificar / deprecar

Revisión de los documentos en `docs/` bajo el modelo propuesto. **Nada se
elimina** — esta es una clasificación para revisión; las ediciones quedan
pendientes hasta aprobar este documento.

| Documento | Estado | Acción puntual |
|---|---|---|
| `EQUIPMENT_PROFILES.md` | CONSERVAR (1 sección a modificar) | "Reglas de operación hidráulica (Fase 2+)" (líneas 136-156) queda como *sketch* obsoleto — reemplazar por referencia a este documento para evitar dos fuentes de verdad sobre el mismo mecanismo. El resto (perfiles, `io_map_overrides`, `equipment_profiles`, `profile_id`) sigue válido y es la base de la Fase D (sección 6). |
| `KAIROX_RELEVAMIENTO_TEMPLATE.md` (v3.0) | MODIFICAR | Sección 6 "Matriz de control" pasa a ser el insumo directo para completar `rules` (cada fila ≈ un slot o un término de slot). Sección 5.10 "Casos no cubiertos (Fase 2)" se reduce: varios casos listados ahí se resuelven ahora con reglas y dejan de ser "no cubiertos". Secciones 1-4 y 7-10 sin cambios. |
| `CHAMICO_CONEXIONES_CAMPO.md` | MODIFICAR | Corregir `softener_regenerating` de **D2→D6** (conflicto con `raw_water_available`, ver sección 5). Agregar sección "Reglas configuradas" con los 2 JSON de la sección 5 (`process_permits["ro"]`, `independent_outputs["well_pump"]`). Asignaciones D1/D4/D3/D5/R2/R3/R4/R5/R6 sin cambios. |
| `COMMISSIONING.md` | CONSERVAR | Sin referencias a io_map/features/señales lógicas (verificado) — ortogonal a este documento. |
| `AI_INTEGRATION_SPEC.md` | CONSERVAR | Sin referencias a io_map/features/señales lógicas (verificado) — ortogonal a este documento. |

Ningún documento se marca DEPRECADO: toda la infraestructura de la Fase 1
(`io_catalog`, `io_map`, `features`, perfiles) sigue siendo la base de este
modelo, no se reemplaza.

---

## 9. Fuera de alcance / próximos pasos

- **Nuevo tipo de slot `START_BLOCK`**: bloquear el arranque completo de la
  FSM (no solo el permiso durante `STARTING`/`PRODUCING`) — relevante para
  ciclos CIP/limpieza química. No releva hoy, no se modela en V1.
- **Ejemplo de extensión — proceso CIP** (ilustra la sección 3
  "crecimiento sin rediseño" con un caso concreto; no implementa nada):
  - nuevo módulo FSM `cip.cpp/.h` (estados propios: IDLE/DOSING/SOAKING/
    RINSING/FAULT), independiente de `control.cpp`, mismo estilo no-bloqueante;
  - dos `DerivedSignal` nuevas — `cip_running`
    (`cip.state ∈ {DOSING,SOAKING,RINSING}`) y `cip_fault`
    (`cip.state == FAULT`) — computadas cada loop junto a `ro_producing`/
    `ro_fault`/`ro_flushing` (sección 2, origen "proceso/FSM");
  - una entrada nueva `process_permits["cip"]` (`PROCESS_COUNT: 1→2`,
    `ProcessId::CIP = 1`) para los interlocks de arranque de CIP;
  - `process_permits["ro"]` e `independent_outputs["well_pump"]` pueden
    agregar `cip_running`/`cip_fault` como término nuevo (p. ej. "no producir
    RO mientras CIP corre") **vía configuración**, sin recompilar — porque
    son `DerivedSignal` de primera clase (sección 2);
  - el único gap real es si CIP necesita bloquear el ARRANQUE de RO (no solo
    el permiso durante STARTING/PRODUCING) — eso es el `START_BLOCK` del
    bullet anterior, la única pieza no cubierta por el modelo actual.
- **Espejo de evaluación de reglas en backend**: permitir preview en la UI
  Flask (¿qué haría el equipo con esta config, sin esperar telemetría?) —
  opcional, posterior a Fase C, solo lectura/diagnóstico.
- **Fase D** (`equipment_profiles` + `rule_overrides`): depende de tener ≥2
  instalaciones reales configuradas con reglas, mismo criterio que
  `EQUIPMENT_PROFILES.md`.
- **Nuevas señales lógicas**: el catálogo es append-only, pero agregar
  señales nuevas (más allá de las 12 `LogicalInput`/7 `LogicalOutput`
  existentes) está fuera de alcance — Chamico (sección 5) se resuelve
  completamente con el catálogo actual.

---

## 10. Validación: escenarios reales configurados sin firmware

Para cada escenario: `rules` (JSON, formato de la sección 3) y `io_map` solo
cuando hace falta asignar un GPIO que no es default. Todas las señales son
del catálogo `io_catalog` existente (12 `LogicalInput`/7 `LogicalOutput`,
sección 2) — ninguna se agrega. Correspondencia con los perfiles ya
documentados en `EQUIPMENT_PROFILES.md`: `ro_standard`→10.1,
`ro_well_pump`→10.2, `ro_transfer_pump`/`ro_intermediate_tank`→10.3, Chamico
(`ro_softener` + pozo + transferencia real)→10.4. `ro_industrial_custom` no
es un escenario aparte: es cualquier combinación de 10.1-10.4 con términos
propios — el modelo no distingue "perfil estándar" de "custom".

| # | Escenario | `process_permits["ro"]` | `independent_outputs[]` | `DerivedSignal` | Catálogo nuevo | Firmware |
|---|---|---|---|---|---|---|
| 10.1 | RO simple | `AND(raw_water_available, pressure_ok)` *(default)* | — | — | 0 | sin cambios — `rules={}` |
| 10.2 | RO + pozo | igual que 10.1 | `well_pump = AND(well_low_level[, NOT softener_regenerating])` | — | 0 | sin cambios |
| 10.3 | RO + transferencia | igual que 10.1 | `transfer_pump = AND(NOT permeate_tank_low, NOT final_tank_high)` | — | 0 (ya en catálogo, sin GPIO asignado) | sin cambios |
| 10.4 | Chamico | `AND(pressure_ok, NOT softener_regenerating)` | `well_pump = OR(ro_producing, softener_regenerating)`; `transfer_pump = AND(ro_producing, NOT final_tank_high)` | `ro_producing` | 0 | sin cambios |
| 10.5 | CIP (futuro) | `+ NOT cip_running` | `well_pump` += `OR cip_running`; `+ process_permits["cip"]` | `cip_running`, `ro_producing` | +1 proceso (FSM CIP, sección 9) | **una vez** (módulo nuevo, no por instalación) |

### 10.1 RO simple (`ro_standard`)

Sin pozo, sin transferencia, sin ablandador. `process_permits["ro"]`
reproduce EXACTAMENTE `crudoOK && presionOK` (control.cpp:236).
`independent_outputs[]` vacío ⇒ `well_pump`/`transfer_pump`/`dosing_pump`
quedan en `AND()` = `false` (sin efecto); los 4 outputs gobernados por la FSM
(`low/high_pressure_pump`, `flush_valve`, `inlet_valve`) no cambian.

```json
{
  "process_permits": {
    "ro": {"op": "AND", "terms": [
      {"signal": "raw_water_available"},
      {"signal": "pressure_ok"}
    ]}
  }
}
```

`io_map`: `DEFAULT_IO_MAP`, sin cambios. Este JSON **es** `DEFAULT_RULES`
(sección 7) — un equipo sin `rules` configurado ya está en este escenario.

### 10.2 RO + bomba de pozo (`ro_well_pump`)

**(a) Caso base** — pozo llena la cisterna según `well_low_level`,
independiente del estado de RO. `process_permits["ro"]` igual que 10.1.

```json
{
  "independent_outputs": {
    "well_pump": {"op": "AND", "terms": [{"signal": "well_low_level"}]}
  }
}
```

`io_map`: sin cambios — `well_low_level`→D5 y `well_pump`→R3 ya son default.
Este JSON reproduce EXACTAMENTE `nivelBajo→R3` (control.cpp:337-339): es el
default de `independent_outputs["well_pump"]` (sección 3).

**(b) Variante con interlock de ablandador** (el pozo no debe correr durante
backwash):

```json
{
  "independent_outputs": {
    "well_pump": {"op": "AND", "terms": [
      {"signal": "well_low_level"},
      {"signal": "softener_regenerating", "negate": true}
    ]}
  }
}
```

`io_map`: + asignar `softener_regenerating` a un GPIO libre (mecánico,
sección 2). Firmware: sin cambios en (a) ni (b) — mismo slot, distintos
términos.

### 10.3 RO + transferencia con tanque intermedio (`ro_transfer_pump` / `ro_intermediate_tank`)

`transfer_pump` y `permeate_tank_high/low` están en el catálogo desde el día
1 (`io_catalog.py` líneas 20-21/35) pero sin GPIO asignado por defecto y sin
lógica de control (`CHAMICO_CONEXIONES_CAMPO.md`: "firmware v1.1.5 todavía no
controla esta salida"). La bomba de transferencia corre mientras hay permeado
en el tanque intermedio y el tanque final no está lleno — independiente del
`state` de la RO:

```json
{
  "independent_outputs": {
    "transfer_pump": {"op": "AND", "terms": [
      {"signal": "permeate_tank_low", "negate": true},
      {"signal": "final_tank_high", "negate": true}
    ]}
  }
}
```

`io_map`: asignar GPIO a `permeate_tank_high`, `permeate_tank_low`,
`transfer_pump` (3 señales ya catalogadas, `gpio=null`→GPIO real —
mecánico). Firmware: sin cambios — `transfer_pump` ya tiene su slot en
`independent_outputs[]` por ser parte de `LogicalOutput::COUNT=7` desde
Fase B.

### 10.4 Chamico — pozo + ablandador + transferencia directa (sección 5, ampliado)

Sin tanque intermedio (línea directa permeado→tanque final, ver diagrama en
`CHAMICO_CONEXIONES_CAMPO.md`): `transfer_pump` corre mientras la RO produce
y el tanque final no está lleno — usa `ro_producing` (derivada) en vez de
`permeate_tank_low`. Combina la regla de `process_permits["ro"]`/`well_pump`
de la sección 5 con una regla nueva de `transfer_pump`:

```json
{
  "process_permits": {
    "ro": {"op": "AND", "terms": [
      {"signal": "pressure_ok"},
      {"signal": "softener_regenerating", "negate": true}
    ]}
  },
  "independent_outputs": {
    "well_pump": {"op": "OR", "terms": [
      {"signal": "ro_producing"},
      {"signal": "softener_regenerating"}
    ]},
    "transfer_pump": {"op": "AND", "terms": [
      {"signal": "ro_producing"},
      {"signal": "final_tank_high", "negate": true}
    ]}
  }
}
```

`io_map`: ver sección 5 (pendiente resolver D2/D6, sección 8) + asignar
`transfer_pump`/`final_tank_high`. Firmware: sin cambios — mismas 2 reglas de
la sección 5 más UNA regla nueva (`independent_outputs["transfer_pump"]`),
mismo mecanismo, sin slot adicional.

### 10.5 CIP — único escenario que requiere firmware nuevo (y por qué eso es correcto)

CIP introduce un **proceso** nuevo (sección 1.1: FSM propia + señales
derivadas + entrada en `process_permits[]`). Bajo la definición formal,
"señales + reglas + procesos" incluye agregar un proceso — eso es lo que el
modelo generaliza, no lo que evita. El costo es **una vez** (módulo
`cip.cpp/.h`, sección 9), no por instalación.

Una vez que existen `cip_running`/`cip_fault` (derivadas) y
`process_permits["cip"]`, una instalación con CIP se configura igual que las
anteriores — solo `rules`:

```json
{
  "process_permits": {
    "ro": {"op": "AND", "terms": [
      {"signal": "raw_water_available"},
      {"signal": "pressure_ok"},
      {"signal": "cip_running", "negate": true}
    ]},
    "cip": {"op": "AND", "terms": [
      {"signal": "ro_producing", "negate": true},
      {"signal": "demand", "negate": true}
    ]}
  },
  "independent_outputs": {
    "well_pump": {"op": "OR", "terms": [
      {"signal": "well_low_level"},
      {"signal": "cip_running"}
    ]}
  }
}
```

`process_permits["ro"]` gana un 3er término (bloquea producción durante
CIP); `process_permits["cip"]` exige RO inactiva y sin demanda para arrancar
CIP; `well_pump` sigue corriendo durante CIP (agua de dilución) vía OR. Las 3
reglas son instancias de los MISMOS dos arrays — `PROCESS_COUNT: 1→2` es el
único cambio de tamaño, ya cubierto por el sketch de la sección 7.

### Conclusión de la validación

10.1-10.4 — que cubren TODOS los perfiles de `EQUIPMENT_PROFILES.md` más
Chamico — se resuelven con el catálogo y el motor de reglas de las Fases A-C
**sin tocar una línea de firmware por instalación**, solo `io_map`+`rules`.
10.5 (CIP) confirma el límite correcto del modelo: un PROCESO nuevo es la
única unidad que requiere firmware nuevo, y es una adición de PLATAFORMA (una
vez), no de instalación — la frontera que formalizó la sección 1.1.

---

## 11. Modelo operativo final — Signals / Process Permits / Independent Outputs / Fault Rules / Device Config

Esta sección consolida (sin eliminar) el contenido de las secciones 0-10 en
**cinco categorías explícitas y disjuntas**, que son la referencia directa
para la implementación (Fase B en adelante). Cada categoría responde una
pregunta distinta y tiene un efecto distinto sobre la FSM:

| Categoría | Pregunta que responde | Efecto | ¿Puede generar FAULT? |
|---|---|---|---|
| **11.1 Signals** | "¿qué información hay disponible?" | — (insumo) | — |
| **11.2 Process Permits** | "¿este proceso puede producir ahora?" | `false` → `state=IDLE` (espera) | No |
| **11.3 Independent Outputs** | "¿este actuador debe estar encendido ahora?" | escribe el output, fuera del `switch(state)` | No |
| **11.4 Fault Rules** | "¿esta condición es una falla que debe detener el equipo?" | `state=FAULT` | **Sí** |
| **11.5 Device Config** | "¿cómo se ensamblan 11.1-11.4 para UNA instalación concreta?" | — (configuración) | — |

Dos pipelines independientes resultan de esta separación:

```
Signals → Rules → Process Permits → FSM → Outputs
Signals → Fault Engine → FAULT
```

`Independent Outputs` se evalúa en paralelo, fuera del `switch(state)` —
no pertenece a ninguno de los dos pipelines de arriba, gobierna actuadores
directamente desde `Signals`.

### 11.1 Signals

Sin cambios de formato respecto a la sección 2 — `io_catalog` (`LogicalInput`/
`LogicalOutput`), `io_map` (Pin↔Señal), y `DerivedSignal` (estado de proceso).
Catálogo append-only: esta propuesta agrega **3 `LogicalInput`** (12→15) y
**1 `DerivedSignal`** (`ro_producing`, ya existente como
`Control::isRunning()` — sección 2).

| Señal nueva | Tipo | Origen | Uso |
|---|---|---|---|
| `permeate_tank_demand` | `LogicalInput` | `io_map` (GPIO) | término de `process_permits["ro"]` (Chamico) |
| `final_tank_demand` | `LogicalInput` | `io_map` (GPIO) | término de `independent_outputs["transfer_pump"]` (Chamico) |
| `phase_failure` | `LogicalInput` | `io_map` (GPIO) | término de `fault_rules[]` (Chamico) — **sección 11.4**, nunca de 11.2/11.3 |
| `ro_producing` | `DerivedSignal` | `Control::isRunning()` | término de `independent_outputs["well_pump"]`/`["transfer_pump"]` |

`pressure_ok` (D4, ya en el catálogo) **no es una señal nueva** — sigue
existiendo y mapeada vía `io_map` igual que hoy, pero su único consumidor
sigue siendo la FSM interna de RO (sección 11.2 explica por qué no es
`Signal` de `process_permits`).

### 11.2 Process Permits

`process_permits[]` (uno por proceso con FSM propia — hoy solo `"ro"`)
representa **únicamente condiciones EXTERNAS de operación**: demanda,
interlocks con otros subsistemas, niveles de tanque. Si la regla evalúa
`false` mientras el proceso está en `STARTING`/`PRODUCING`, el proceso pasa a
`IDLE` — es una **espera**, no una falla, y NUNCA produce `state=FAULT`.

**`pressure_ok` queda explícitamente FUERA de `process_permits`.** La presión
de entrada es una condición que la **FSM interna de la RO** evalúa por sí
misma durante `STARTING` (confirmación de arranque, con reintentos) y
`PRODUCING` (pérdida de presión → `IDLE`) — es un permisivo *interno* del
proceso, no un permiso *externo* que otro subsistema pueda otorgar o negar.
Esto no cambia con esta propuesta: `presionOK` (debounced 2s) sigue siendo un
término hardcodeado de la FSM, evaluado en paralelo a `process_permits["ro"]`.

| | Default (RO simple / RO+pozo) | Chamico |
|---|---|---|
| `process_permits["ro"]` | `AND(raw_water_available)` — reproduce exacto `crudoOK` actual | `AND(permeate_tank_demand, NOT softener_regenerating)` |
| `pressure_ok` (FSM interna, sin cambios) | `presionOK` debounced, evaluado en `STARTING` y `PRODUCING` | igual — Chamico también requiere presostato OK para producir |

### 11.3 Independent Outputs

`independent_outputs[]` (uno por `LogicalOutput`, hoy 7 slots) gobierna
actuadores **directamente desde `Signals`**, fuera del `switch(state)` —
generalización del patrón ya existente de la bomba de pozo
(`control.cpp:334-339`). No tienen relación con `FAULT` ni con
`process_permits`.

| | Default | Chamico |
|---|---|---|
| `well_pump` | `OR(well_low_level)` — reproduce exacto `nivelBajo→R3` actual | `OR(ro_producing, softener_regenerating)` |
| `transfer_pump` | sin regla (`term_count=0` ⇒ `false`, sin GPIO asignado) | `AND(final_tank_demand, permeate_tank_low)` |
| resto (`low/high_pressure_pump`, `flush_valve`, `inlet_valve`, `dosing_pump`) | sin regla — siguen 100% gobernados por la FSM (`setOutputs`) | igual |

### 11.4 Fault Rules

Tercera categoría, **explícitamente separada** de 11.2/11.3: condiciones que
SÍ generan `state=FAULT`.

| Mecanismo | Señales | ¿Configurable por instalación? |
|---|---|---|
| Protecciones C++ fijas (`checkMembraneHighPressure()` → `PRESSURE_MEMBRANE_HIGH`, chequeo `FLOW_LOW`, `RECOVERY_LOW`/`HIGH`, `MAX_RETRIES`) | presión de membrana, caudal, recovery, reintentos | parcialmente — vía `*_enabled`/umbrales de `SensorConfig` (`/config`), no vía `fault_rules[]` |
| `fault_rules[]` (NUEVO, `FAULT_RULES_MAX=4`) | señales nuevas por instalación (ej. `phase_failure`) | sí — array completo, por instalación |

Ambos mecanismos son **conceptualmente Fault Rules** (responden la misma
pregunta, ambos producen `state=FAULT`), pero solo `fault_rules[]` es
*configuración*. Las protecciones C++ existentes no se refactorizan en esta
etapa ("no modificar FSM interna") — `fault_rules[]` es el **mecanismo de
extensión** para señales de falla NUEVAS sin agregar código C++ por señal. Si
una instalación no configura `fault_rules[]` (`fault_rule_count=0`, default),
no se evalúa ninguna — "una señal o regla inexistente no participa de la
evaluación", el mismo criterio que ya aplica a `*_enabled=false`.

```cpp
struct FaultRuleConfig {
    RuleConfig  condition;
    FaultReason reason;      // FaultReason::NONE = slot vacío/sin uso
    uint32_t    delay_sec;   // debounce, mismo patrón que pressure_fault_delay_sec
};
```

**Chamico**: `phase_failure` pertenece ÚNICAMENTE a `fault_rules[]` — nunca a
`process_permits` ni a `independent_outputs`. Es una protección propia de la
RO (catalogada como `LogicalInput`, sección 11.1) que detiene la producción:

```json
{
  "condition": {"op": "AND", "terms": [{"signal": "phase_failure", "source": "input", "negate": false}]},
  "reason": "PHASE_FAILURE",
  "delay_sec": 1
}
```

`phase_failure=1` sostenido > `delay_sec` → `faultReason=PHASE_FAILURE`,
`state=FAULT` — sin afectar `process_permits`/`independent_outputs` ni la FSM
de arranque. `high_pressure`/`low_flow` para Chamico (si aplica) se
configuran vía los campos YA EXISTENTES de `SensorConfig`/`/config`
(`pressure_membrane_*`, `min_flow_lpm`/`flow_fault_delay_sec`) — fuera del
array `fault_rules[]`.

### 11.5 Device Config

Una instalación = `{io_map, features, rules}` (sección 4) donde `rules` =
`{process_permits, independent_outputs, fault_rules}` (11.2-11.4). Ejemplo
completo — **Chamico para placa de laboratorio** (ver
`docs/chamico_lab_config.json`):

```json
{
  "io_map": {
    "inputs": {
      "permeate_tank_demand":   {"gpio": 5,  "mode": "pullup", "invert": 0},
      "final_tank_demand":      {"gpio": 15, "mode": "pullup", "invert": 0},
      "phase_failure":          {"gpio": 21, "mode": "pullup", "invert": 0},
      "permeate_tank_low":      {"gpio": 22, "mode": "pullup", "invert": 0},
      "softener_regenerating":  {"gpio": 23, "mode": "pullup", "invert": 0},
      "pressure_ok":            {"gpio": 33, "mode": "pulldown", "invert": 0}
    },
    "outputs": {
      "transfer_pump": {"gpio": 12, "invert": 0}
    }
  },
  "features": {
    "feature_well_pump": true,
    "feature_transfer_pump": true,
    "feature_softener_interlock": true
  },
  "rules": {
    "process_permits": {
      "ro": {"op": "AND", "terms": [
        {"signal": "permeate_tank_demand",  "source": "input", "negate": false},
        {"signal": "softener_regenerating", "source": "input", "negate": true}
      ]}
    },
    "independent_outputs": {
      "well_pump": {"op": "OR", "terms": [
        {"signal": "ro_producing",          "source": "derived", "negate": false},
        {"signal": "softener_regenerating", "source": "input",   "negate": false}
      ]},
      "transfer_pump": {"op": "AND", "terms": [
        {"signal": "final_tank_demand", "source": "input", "negate": false},
        {"signal": "permeate_tank_low", "source": "input", "negate": false}
      ]}
    },
    "fault_rules": [
      {
        "condition": {"op": "AND", "terms": [{"signal": "phase_failure", "source": "input", "negate": false}]},
        "reason": "PHASE_FAILURE",
        "delay_sec": 1
      }
    ]
  }
}
```

**Verificación de genericidad**: Chamico se describe **100% por
configuración** — `io_map` (qué GPIO es cada señal/salida), `features`
(metadata UI), y `rules` (`process_permits`/`independent_outputs`/
`fault_rules`, 11.2-11.4). El firmware no contiene ningún `if (device==...)`
ni rama específica de "Chamico": `evalRule()`/`checkFaultRules()` son
genéricos, operan sobre los mismos arrays para CUALQUIER instalación. La
diferencia de comportamiento entre "RO simple" y "Chamico" proviene
exclusivamente de los valores cargados por MQTT/NVS en `io_map` y `rules`.

### 11.6 Próximos pasos (NO implementado): Process Config — parámetros de FSM de RO

Fuera de alcance de esta propuesta — **documentado para evitar una futura
migración arquitectónica**, sin agregar código ni campos ahora. Los timers
hoy fijos como `#define` (`config.h`/`control.h`) son candidatos a una
**sexta categoría futura**, "Process Config", paralela a `Signals`/
`Process Permits`/`Independent Outputs`/`Fault Rules`/`Device Config`:

| Constante actual (fija) | Futuro parámetro configurable |
|---|---|
| `LOW_PUMP_FILL_TIME` | `pressure_stabilization_delay` |
| `PRESSURE_CHECK_TIME` | `startup_timeout` |
| `FLUSH_TDS_TIME` | `flush_duration` |
| `RETRY_DELAY` | `flush_interval` / intervalo de reintento |
| `FSM_MAX_RETRIES` | `max_retries` |

Diseño previsto (futuro): nuevo struct `ProcessConfig` — mismo patrón que
`SensorConfig`/`RulesConfig` (NVS propio, ej. `kx_proccfg`; tópico MQTT
retained `fyntek/{id}/process_config`; `merge_*`/`validate_*` en backend).
Esta sección NO modifica `control.cpp` ni agrega structs — es un placeholder
para que, cuando se aborde la "segunda etapa" (parametrización de la FSM de
RO), el lugar de cada parámetro ya esté decidido.

---

Este documento es **arquitectura, no implementación parcial**: las secciones
0-10 son el análisis y la validación; la **sección 11** es el modelo
operativo final (Signals / Process Permits / Independent Outputs / Fault
Rules / Device Config) que implementa la Fase B en adelante. El primer paso
ejecutable y de menor riesgo sigue siendo la **Fase A** (sección 6) — refactor
interno (`readSignal`/`writeSignal`), sin nuevos contratos, sin cambio de
comportamiento (`defaultIOMap()` ya es la config actual).
