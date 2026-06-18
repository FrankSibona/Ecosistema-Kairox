# Mejoras pendientes — KAIROX/FYNTEK

Hallazgos de la auditoría de confiabilidad (2026-06-18) que requieren evaluación futura.

---

## DB-1: GPIO 2 (boot-strap pin) usado como output

- **Archivo:** `config.h:11` — `#define PIN_R6 2` (inlet_valve)
- **Riesgo:** GPIO 2 es pin de bootstrap del ESP32. Si el relé de inlet_valve está energizado durante el boot, puede forzar al ESP32 a modo UART download e impedir el arranque.
- **Estado actual:** En Chamico el R6 no está en uso. En placas que usen inlet_valve con PIN_R6=GPIO2, el riesgo existe.
- **Acción requerida:** Reasignar PIN_R6 a un GPIO seguro (ej. GPIO 13, 14, o 21) en config.h cuando se diseñe la próxima revisión de PCB. Agregar validación en `validOutputEntry()` que excluya GPIOs de bootstrap (0, 2, 12, 15) para outputs.
- **Prioridad:** Evaluar antes de producción de nuevas placas.

---

## M-5: Timestamp = 0 durante ventana de reconexión NTP

- **Archivo:** `comms.cpp:609,628-631`
- **Riesgo:** Cuando WiFi se desconecta, `ntpConfigured = false`. Al reconectar, si NTP no sincronizó aún, `getTimestamp()` retorna 0. Mensajes MQTT se publican con `ts:0`.
- **Consecuencia:** Backend inserta telemetría con timestamp inválido (epoch 1970).
- **Acción requerida:** No publicar telemetría hasta que `getTimestamp() > 1600000000` (post-2020), o cachear último timestamp válido como fallback.
- **Prioridad:** Baja. El heartbeat cada 10s limita la ventana a <30s en el peor caso.

---

## B-1: Estado STOPPING reservado para uso futuro

- **Archivo:** `control.h:14`, `control.cpp:278-280`
- **Estado:** Definido en enum y switch/case pero ninguna transición lo alcanza actualmente.
- **Propósito reservado:** Secuencia de apagado controlado (ej. despresurizar antes de detener bombas, cerrar válvulas en orden). Actualmente el STOP command va directo a FLUSHING o IDLE.
- **Acción requerida:** Cuando se implemente secuencia de shutdown, usar este estado.

---

## B-6: Evaluación de heap con portal WiFi abierto

- **Contexto:** El portal WiFi fallback se mantiene abierto indefinidamente (decisión operativa). WiFiManager en AP+STA consume ~20-30KB adicionales de heap.
- **Acción requerida:** Medir heap real en campo con portal abierto durante 1h, 12h, 24h. Si no hay fuga apreciable, no se requiere timeout.
- **Instrumentación:** Agregar reporte de `ESP.getFreeHeap()` vía MQTT cada 10 minutos (ya existe log cada 30min por serial en comms.cpp:647). Crear panel Grafana para monitorear tendencia.
- **Prioridad:** Baja. El portal se mantiene abierto por default.
