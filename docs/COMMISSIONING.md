# KAIROX — Commissioning Checklist

**Versión firmware:** v1.1.x  
**Última revisión:** 2026-05-22

Checklist para primer despliegue físico o flash de firmware nuevo.  
Ejecutar en orden. Cada bloque puede hacerse de forma independiente si el componente anterior ya fue validado.

---

## 1. Pre-requisitos

- [ ] Backend Flask corriendo (`docker ps` muestra `ro-python-worker` UP)
- [ ] PostgreSQL UP, schema v3.4 aplicado (`device_config.flow_factor_1` existe)
- [ ] Broker MQTT UP y accesible desde la red del dispositivo
- [ ] `MQTT_USER` / `MQTT_PASS` correctos en `.env`
- [ ] `AI_API_KEY` / `ADMIN_API_KEY` configurados si corresponde

---

## 2. Flash del firmware

- [ ] `platformio.ini`: `MQTT_MAX_PACKET_SIZE=512` presente en `build_flags`
- [ ] Flash exitoso (sin errores de compilación ni upload)
- [ ] Monitor serial abierto a 115200 bps
- [ ] Secuencia de boot visible: `[NVS] Totales cargados` + `[CFG] ...`
- [ ] `DEVICE ID: ESP32_XXXXXXXXXXXX` impreso en serial

---

## 3. MQTT — conectividad básica

```bash
# Desde el servidor, verificar que el dispositivo aparece:
mosquitto_sub -h localhost -u kairox -P <pass> -t "fyntek/#" -v
```

- [ ] Heartbeat llega en ≤30s: `fyntek/{device_id}/heartbeat`
- [ ] Mensaje de proceso llega en ≤2s: `fyntek/{device_id}/process`
- [ ] Mensaje de calidad llega en ≤11s: `fyntek/{device_id}/quality`

---

## 4. Payload de telemetría — verificación de campos

**Process payload** debe contener:
```json
{ "flow_perm_lpm": ..., "flow_rechazo_lpm": ...,
  "pressure_membrane_bar": ..., "pressure_brine_bar": ...,
  "volume_perm_l": ..., "volume_rechazo_l": ... }
```

**Quality payload** debe contener los 4 campos:
```json
{ "tds_in_voltage": 0.XXX, "tds_in_ppm": XX.X,
  "tds_out_voltage": 0.XXX, "tds_out_ppm": XX.X }
```

- [ ] `tds_in_voltage` entre 0.0 y 3.1 V (sensor desconectado → ~0 V)
- [ ] `tds_in_ppm` ≥ 0 (agua de red típica: 150–800 ppm)
- [ ] NO aparecen campos `tds_in_raw`, `tds_out_raw`, `tds_in_raw_v` (nomenclatura vieja eliminada)

---

## 5. Configuración remota — MQTT retained config

```bash
# Publicar config de prueba desde el servidor:
mosquitto_pub -h localhost -u kairox -P <pass> -r \
  -t "fyntek/{device_id}/config" \
  -m '{"flow_factor_1":450.0,"flow_factor_2":450.0,"tds_temperature":25.0,"updated_at":1748000000}'
```

- [ ] Serial imprime: `[CFG] APLICADA: ff1=450.0 ff2=450.0 tds_t=25.0 ts=1748000000`
- [ ] Config rechazada si `updated_at` es menor al actual (serial: `[CFG] IGNORADA`)
- [ ] Config rechazada si valores fuera de rango (serial: `[CFG] RECHAZADA`)
- [ ] Config rechazada si JSON malformado (serial: `[CFG] JSON inválido: ...`)

---

## 6. NVS — persistencia tras reboot

1. Aplicar una config con `flow_factor_1: 999.0` (valor inusual pero válido)
2. Resetear el ESP32 (botón RST o power cycle)
3. Observar serial:

- [ ] Boot imprime: `[CFG] Cargado: ff1=999.0 ...`  → NVS funcionando
- [ ] Flow1 calculado con factor 999 inmediatamente tras reboot

---

## 7. NVS — validación de integridad (magic/version)

```bash
# Simular NVS corrupta: publicar reset de config
mosquitto_pub -h localhost -u kairox -P <pass> \
  -t "fyntek/{device_id}/config/reset" -m ""
```

- [ ] Serial imprime: `[CFG] RESET a defaults — NVS kx_cfg limpiado`
- [ ] Reboot → serial imprime: `[CFG] NVS: magic=0x00000000 ver=0 — inválido, usando defaults`
- [ ] Dispositivo arranca normalmente con `ff1=450.0 ff2=450.0 tds_t=25.0`

---

## 8. Caudalímetros — validación de pulsos

Con caudal mínimo conocido (ej. 1 L/min):

- [ ] `flow_perm_lpm` ≈ valor esperado (±10%)
- [ ] Sin caudal → `flow_perm_lpm` = 0.0 o muy cercano
- [ ] Ajustar `flow_factor_1` vía API si hay desvío: `POST /api/config/{device_id}`

```bash
curl -X POST http://backend:8080/api/config/{device_id} \
  -H "Content-Type: application/json" \
  -d '{"flow_factor_1": 475.0}'
```

- [ ] Backend publica MQTT retained con `updated_at` nuevo
- [ ] Serial confirma: `[CFG] APLICADA: ff1=475.0 ...`

---

## 9. Backend — ingesta de telemetría

```bash
# Verificar últimos registros en DB:
docker exec ro-postgres psql -U user iot_db -c \
  "SELECT time, tds_in_voltage, tds_in_ppm, tds_out_ppm FROM telemetry_quality ORDER BY time DESC LIMIT 3;"
```

- [ ] Filas presentes con timestamps recientes
- [ ] `tds_in_ppm` tiene valor numérico (no NULL)
- [ ] Columnas `tds_in_raw` / `tds_out_raw` **no existen** (schema limpio)

```bash
# Verificar config guardada:
curl http://backend:8080/api/config/{device_id}
```

- [ ] Respuesta incluye `flow_factor_1`, `flow_factor_2`, `tds_temperature`

---

## 10. Comportamiento sin MQTT

Desconectar red WiFi del dispositivo:

- [ ] Serial imprime `[COMMS] WiFi reconectando...` periódicamente
- [ ] FSM de control sigue funcionando (estado IDLE/PRODUCING se mantiene)
- [ ] Al reconectar → MQTT recibe config retained y la aplica (si hay una publicada)
- [ ] Dispositivo **nunca** queda inutilizable por falta de MQTT

---

## 11. Grafana — visibilidad de datos

- [ ] Panel TDS muestra `tds_in_ppm` y `tds_out_ppm` en unidades ppm
- [ ] Panel de caudal muestra `flow_perm_lpm` con valor correcto
- [ ] No hay errores `column "tds_in_raw" does not exist` en logs de Grafana

---

## Firma de comisionamiento

| Campo | Valor |
|-------|-------|
| Fecha | |
| Técnico | |
| Device ID | |
| Firmware version | |
| flow_factor_1 aplicado | |
| flow_factor_2 aplicado | |
| tds_temperature | |
| Observaciones | |
