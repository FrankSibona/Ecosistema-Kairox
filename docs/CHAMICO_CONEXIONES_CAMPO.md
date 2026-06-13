# KAIROX — Hoja de Conexiones de Campo — Equipo "Chamico"

**Device ID:** `ESP32_94FFEEABC31C`
**Firmware mínimo:** v1.1.5 (capa de Mapeo de E/S)
**Última revisión:** 2026-06-13

Este documento es una guía de **instalación y comisionamiento en campo**.
No requiere conocimiento de firmware ni de la lógica interna del equipo —
indica únicamente **qué cable va a qué bornera** y **cómo verificarlo**.

---

## ⚠️ Antes de cablear — reglas generales

- Las borneras **D1–D6** (entradas) son de **3.3 V lógica, contacto seco**.
  **NUNCA** conectar 220 VAC, 24 VDC ni ninguna tensión externa a estas
  borneras — se destruye el controlador.
- Las borneras **R1–R6** (salidas) son **contactos secos de relé** (libres de
  tensión del lado del controlador). La tensión de la bomba/válvula la define
  el equipo de campo, no el ESP32.
- Si un sensor/contacto instalado tiene la **polaridad invertida** respecto a
  lo indicado en este documento (por ejemplo, un flotador NA donde se esperaba
  NC), **no es necesario recablear**: en el panel Flask, sección
  **"Mapeo de E/S (avanzado)"**, hay una casilla **"Invertir"** por señal que
  corrige la lógica sin tocar el cableado.

---

## 1. Entradas digitales

| Señal lógica | Función | Tipo de contacto (NA/NC) | Tensión esperada | Bornera física KAIROX | Observaciones |
|---|---|---|---|---|---|
| `demand` | Demanda de agua — arranca producción cuando el sistema "pide" agua | **NC** en reposo (cerrado a GND = sin demanda). Se **abre** cuando hay demanda | 3.3 V lógica ESP32 (pull-up interno), contacto seco a **GND** | **D1** | Ya configurado en el equipo (wiring estándar de fábrica). |
| `pressure_ok` | Presostato de baja presión — confirma presión de alimentación OK antes de habilitar bomba de alta presión | **NA** — cierra a **+3.3 V** cuando la presión está por encima del setpoint | 3.3 V lógica ESP32 (pull-down interno), contacto seco a **+3.3 V** | **D4** | Ya configurado en el equipo (wiring estándar de fábrica). |
| `softener_regenerating` | Interlock ablandador — bloquea/avisa mientras el ablandador está regenerando | **NC** en reposo (cerrado = operación normal). Se **abre** durante regeneración | 3.3 V lógica ESP32 (pull-up interno), contacto seco a **GND** | **D2** *(propuesta)* | Señal nueva. Si la salida del ablandador es NA ("cierra al regenerar"), activar **Invertir** en Mapeo de E/S. |
| `feed_tank_high` | Tanque de alimentación — nivel alto | **NC** en reposo (cerrado = no lleno). Se **abre** cuando el tanque está lleno | 3.3 V lógica ESP32 (pull-up interno), contacto seco a **GND** | **D3** *(propuesta)* | Señal nueva. Si el flotador instalado es NA, activar **Invertir**. |
| `feed_tank_low` | Tanque de alimentación — nivel bajo | **NC** en reposo (cerrado = no vacío). Se **abre** cuando el tanque está vacío | 3.3 V lógica ESP32 (pull-up interno), contacto seco a **GND** | **D5** *(propuesta)* | Señal nueva. Si el flotador instalado es NA, activar **Invertir**. |
| `final_tank_high` | Tanque final — nivel alto (corte por tanque lleno) | **NC** en reposo (cerrado = no lleno). Se **abre** cuando el tanque está lleno | 3.3 V lógica ESP32 (pull-up interno), contacto seco a **GND** | **D6** *(propuesta)* | Señal nueva. Si el flotador instalado es NA, activar **Invertir**. |

**Nota sobre las borneras marcadas "(propuesta)":** corresponden a GPIO libres
en el equipo (D2, D3, D5, D6). Para que el sistema las reconozca con estos
nombres lógicos hay que asignarlas en **"Mapeo de E/S (avanzado)"** del panel
Flask (pendiente de confirmar/aplicar). El cableado físico puede hacerse ya
con esta asignación.

---

## 2. Salidas a relé

| Señal lógica | Función | Tipo de contacto (relé KAIROX) | Tensión esperada | Bornera física KAIROX | Observaciones |
|---|---|---|---|---|---|
| `high_pressure_pump` | Bomba de alta presión (alimenta membrana RO) | Contacto seco **NA** — cierra cuando la bomba debe arrancar | Según módulo de relé (típico 250 VAC / 10 A) — la tensión real la define la bomba instalada. **Confirmar en obra.** | **R2** | Ya configurado en el equipo. |
| `well_pump` | Bomba de pozo (llena tanque de alimentación) | Contacto seco **NA** — cierra cuando la bomba debe arrancar | Según módulo de relé — confirmar en obra | **R3** | Ya configurado en el equipo. |
| `transfer_pump` | Bomba de transferencia (envía permeado al tanque final) | Contacto seco **NA** — cierra cuando la bomba debe arrancar | Según módulo de relé — confirmar en obra | **R4** *(propuesta)* | ⚠️ Salida nueva. El cableado puede hacerse ahora, pero **firmware v1.1.5 todavía no controla esta salida** (sin lógica de FSM asociada) — no esperar funcionamiento automático hasta una próxima actualización. |
| `flush_valve` | Válvula de flush/lavado de membrana | Contacto seco **NA** — cierra durante el ciclo de flush | Según válvula instalada (24 VDC o 220 VAC típico) — confirmar en obra | **R5** | Ya configurado en el equipo. |
| `inlet_valve` | Válvula de entrada a membrana | Contacto seco **NA** — cierra durante producción | Según válvula instalada — confirmar en obra | **R6** | Ya configurado en el equipo. |

---

## 3. Diagrama de flujo hidráulico simplificado

Esquema conceptual — no representa cañerías reales, solo ubica cada señal en
el proceso para orientar al instalador.

```
                 [well_pump · R3]
   POZO  ───────────►──────────────┐
                                    ▼
                          ┌──────────────────┐
                          │ TANQUE            │── feed_tank_high (D3)
                          │ ALIMENTACIÓN      │── feed_tank_low  (D5)
                          └─────────┬─────────┘
                                     │
                          [softener_regenerating · D2]
                                ABLANDADOR
                                     │
                          [pressure_ok · D4]  (presostato baja presión)
                                     │
                     ┌───────────────────────────┐
                     │   [inlet_valve · R6]       │
                     │            │               │
                     │  [high_pressure_pump · R2] │
                     │            │               │
                     │        MEMBRANA RO         │
                     └──────┬──────────────┬──────┘
                        permeado          rechazo
                            │                │
                  [transfer_pump · R4]  [flush_valve · R5]
                            │                │
                            ▼                ▼
                  ┌───────────────────┐   drenaje
                  │   TANQUE FINAL    │── final_tank_high (D6)
                  └─────────┬─────────┘
                             │
                      [demand · D1]
                       (consumo / arranque de producción)
```

---

## 4. Checklist de puesta en marcha

- [ ] Controlador KAIROX alimentado y arrancando (LED/serial OK).
- [ ] Confirmar versión de firmware ≥ 1.1.5 (panel Admin → datos del equipo).
- [ ] Mapeo de E/S aplicado en **"Mapeo de E/S (avanzado)"** según tablas 1 y 2
      (D2/D3/D5/D6/R4 asignados a las señales correspondientes).
- [ ] Cablear entradas **D1–D6** según Tabla 1 (respetar NA/NC y conexión a
      GND/+3.3 V indicada).
- [ ] Cablear salidas **R2, R3, R5, R6** (y R4 si corresponde) según Tabla 2.
- [ ] Verificar con multímetro, **sin energizar cargas**, que cada bornera de
      entrada mide 3.3 V (reposo, pull-up) o 0 V (reposo, pull-down en D4)
      antes de conectar el sensor.
- [ ] Conectar WiFi y confirmar heartbeat MQTT (`fyntek/ESP32_94FFEEABC31C/heartbeat`).
- [ ] Ejecutar la sección **"Pruebas de señales"** para cada entrada y salida.
- [ ] Probar comandos **START / STOP / FLUSH / RST** desde el panel Admin y
      confirmar respuesta física (bombas/válvulas correctas).
- [ ] Dejar el equipo en estado **IDLE** antes de retirarse.

---

## 5. Pruebas de señales

Para entradas: activar el modo diagnóstico del equipo desde el panel
(`fyntek/ESP32_94FFEEABC31C/diag/ctrl`) y observar los campos `d1`–`d6` del
mensaje `fyntek/ESP32_94FFEEABC31C/diag` (1 = contacto abierto / activo en
D1,D2,D3,D5,D6; 1 = contacto cerrado a +3.3V en D4). Para salidas: emitir
comandos desde el panel Admin y verificar continuidad/clic del relé.

| Señal | Bornera | Acción del instalador | Lectura esperada | Resultado (OK/Falla) | Notas |
|---|---|---|---|---|---|
| `demand` | D1 | Abrir y cerrar el contacto manualmente | `d1` pasa de 0→1 al abrir | | |
| `pressure_ok` | D4 | Simular presión OK (cerrar contacto a +3.3 V) | `d4`=1 con contacto cerrado | | |
| `softener_regenerating` | D2 | Abrir/cerrar contacto del ablandador | `d2` pasa de 0→1 al abrir | | |
| `feed_tank_high` | D3 | Forzar flotador a posición "lleno" | `d3`=1 con tanque lleno (o invertido si flotador NA) | | |
| `feed_tank_low` | D5 | Forzar flotador a posición "vacío" | `d5`=1 con tanque vacío (o invertido si flotador NA) | | |
| `final_tank_high` | D6 | Forzar flotador a posición "lleno" | `d6`=1 con tanque lleno (o invertido si flotador NA) | | |
| `well_pump` | R3 | Comando START desde panel (con `well_low_level` simulando pozo bajo, si aplica) | Relé R3 cierra, bomba de pozo arranca | | |
| `high_pressure_pump` | R2 | Comando START desde panel (con `pressure_ok`=OK) | Relé R2 cierra, bomba de alta presión arranca | | |
| `flush_valve` | R5 | Comando FLUSH desde panel | Relé R5 cierra durante el ciclo de flush | | |
| `inlet_valve` | R6 | Comando START desde panel | Relé R6 cierra al iniciar producción | | |
| `transfer_pump` | R4 | — | ⚠️ Sin lógica de control en firmware v1.1.5. Verificar solo continuidad de cableado, sin esperar accionamiento automático | | |

---

## Pendientes para que este documento quede 100% reflejado en el sistema

1. Aplicar en **"Mapeo de E/S (avanzado)"** del panel Flask las asignaciones
   propuestas: `softener_regenerating`→D2, `feed_tank_high`→D3,
   `feed_tank_low`→D5, `final_tank_high`→D6, `transfer_pump`→R4.
2. Firmware: agregar lectura/control real de estas señales (Fase 2 — fuera de
   alcance de v1.1.5, ver `docs/EQUIPMENT_PROFILES.md`).
