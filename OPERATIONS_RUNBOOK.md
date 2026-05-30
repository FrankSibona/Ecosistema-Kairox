# KAIROX — Operations Runbook

**Versión:** 1.0  
**Sistema:** KAIROX IoT Platform — Osmosis inversa industrial  
**Servidor:** `159.112.132.176`  
**Última actualización:** 2026-05-29

---

## 1. Información general del entorno

### Rutas

| Recurso | Ruta |
|---|---|
| Proyecto raíz | `/home/ubuntu/iot-server` |
| Backend Python | `/home/ubuntu/iot-server/python_iot/` |
| Config alertas | `/home/ubuntu/iot-server/python_iot/alert_config.py` |
| Dashboards JSON | `/home/ubuntu/iot-server/dashboards/dashboards/` |
| Config Mosquitto | `/home/ubuntu/iot-server/mosquitto/config/mosquitto.conf` |
| docker-compose | `/home/ubuntu/iot-server/docker-compose.yml` |

### Contenedores Docker

| Nombre | Imagen | Puerto externo |
|---|---|---|
| `ro-python-worker` | `iot-server_iot-python-worker` | `8080` (Flask API) |
| `ro-postgres` | `postgres:15` | `5432` |
| `ro-mosquitto` | `eclipse-mosquitto` | `1883`, `9001` (WS) |
| `ro-grafana` | `grafana/grafana:latest` | `3000` |
| `ro-nginx` | `jc21/nginx-proxy-manager` | `80`, `443` |

### Puertos

| Puerto | Servicio |
|---|---|
| `8080` | Flask API + Panel Admin |
| `5432` | PostgreSQL |
| `1883` | MQTT TCP |
| `9001` | MQTT WebSocket |
| `3000` | Grafana |
| `80/443` | Nginx Proxy Manager |

### Credenciales por defecto

| Servicio | Usuario | Contraseña |
|---|---|---|
| PostgreSQL | `user` | `password` |
| Grafana | `admin` | `admin123` |
| MQTT (backend) | `kairox` | `admin0102` |
| MQTT (broker) | — | anónimo habilitado |

### Variables de entorno configurables (docker-compose.yml)

```yaml
# Backend (ro-python-worker)
DB_HOST=ro-postgres
DB_NAME=iot_db
DB_USER=user
DB_PASS=password
MQTT_BROKER=ro-mosquitto
MQTT_PORT=1883
MQTT_USER=kairox
MQTT_PASS=admin0102

# Panel admin Flask (vacío = sin auth)
ADMIN_PANEL_USER=
ADMIN_PANEL_PASS=

# Telegram
TELEGRAM_TOKEN=
TELEGRAM_ADMIN_CHAT=

# Email (SMTP)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
SMTP_TO=

# IA
AI_ENDPOINT_URL=
AI_POLL_INTERVAL_SEC=60
AI_WINDOW_SECONDS=60
AI_WINDOW_MAX_SAMPLES=120
AI_TIMEOUT_SEC=10
AI_AUTO_COOLDOWN_SEC=300

# Thresholds (ver también alert_config.py)
THRESH_OFFLINE_SEC=90
THRESH_TDS_OUT_WARN=80.0
THRESH_LOW_PRESSURE=2.0
THRESH_HIGH_PRESSURE=9.0
THRESH_LOW_EFFICIENCY=0.85
THRESH_NO_FLOW_SEC=30
THRESH_REMINDER_SEC=3600
```

Para aplicar cambios de env:
```bash
cd /home/ubuntu/iot-server
# editar docker-compose.yml, luego:
docker-compose up -d iot-python-worker
```

### Estructura de topics MQTT

```
fyntek/{device_id}/process   → telemetría de proceso (caudales, presiones, volúmenes)
fyntek/{device_id}/quality   → calidad (TDS in/out, voltajes)
fyntek/{device_id}/state     → estado FSM + heartbeat
fyntek/{device_id}/inputs    → entradas digitales (flotante crudo, etc.)
fyntek/{device_id}/cmd       → comandos enviados al firmware (backend → device)
fyntek/{device_id}/ack       → confirmación de comandos (device → backend)
```

---

## 2. Verificar estado del sistema

### Estado general de contenedores
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Todos deben mostrar `Up`. Si alguno dice `Exited`:
```bash
docker-compose -f /home/ubuntu/iot-server/docker-compose.yml up -d <nombre_servicio>
```

### Verificar backend Flask
```bash
curl -s http://localhost:8080/api/status/ESP32_D0448EC92DF4 | python3 -m json.tool
```
Respuesta esperada: JSON con `state`, `online`, `seconds_since_seen`.

### Verificar PostgreSQL
```bash
docker exec ro-postgres psql -U user -d iot_db -c "SELECT COUNT(*) FROM telemetry_process WHERE time > NOW() - INTERVAL '5 minutes';"
```
Debe devolver un número > 0 si hay un device activo.

### Verificar Mosquitto
```bash
docker exec ro-mosquitto mosquitto_pub -h localhost -t "test/ping" -m "ok"
docker exec ro-mosquitto mosquitto_sub -h localhost -t "test/ping" -C 1 -W 2
```
Debe imprimir `ok`.

### Verificar Grafana
```bash
curl -s -u admin:admin123 http://localhost:3000/api/health | python3 -m json.tool
```
Respuesta esperada: `"database": "ok"`.

### Ver telemetría MQTT en vivo
```bash
docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/#" -v
```
Ctrl+C para salir. Debe aparecer telemetría cada ~1 segundo si hay un device conectado.

### Ver mensajes de un device específico
```bash
docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/ESP32_D0448EC92DF4/#" -v
```

### Ver logs del backend en tiempo real
```bash
docker logs ro-python-worker -f --tail 50
```

### Buscar errores en los logs
```bash
docker logs ro-python-worker --tail 200 2>&1 | grep -E "ERROR|CRITICAL|Exception"
```

### Ver últimos registros en DB
```bash
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT time, device_id, flow_perm_lpm, pressure_membrane_bar \
   FROM telemetry_process ORDER BY time DESC LIMIT 5;"
```

---

## 3. Alta manual de un nuevo dispositivo

El firmware se auto-registra al enviar el primer mensaje MQTT. Si se necesita registrar manualmente o configurar antes de que el equipo llegue:

### Paso 1 — Insertar device en DB
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
INSERT INTO devices (device_id, display_name, enabled, ai_mode)
VALUES ('ESP32_NUEVO_ID', 'Nombre del Equipo', TRUE, 'OFF')
ON CONFLICT (device_id) DO UPDATE
  SET display_name = EXCLUDED.display_name,
      enabled      = EXCLUDED.enabled;
"
```

### Paso 2 — Crear configuración inicial
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
INSERT INTO device_config (
  device_id, pump_power_kw, cost_kwh, daily_target_liters,
  flow_factor_1, flow_factor_2, tds_temperature
) VALUES (
  'ESP32_NUEVO_ID', 0.75, 0.12, 500.0,
  450.0, 450.0, 25.0
)
ON CONFLICT (device_id) DO NOTHING;
"
```

### Paso 3 — Verificar que el device existe
```sql
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, display_name, enabled, ai_mode FROM devices WHERE device_id = 'ESP32_NUEVO_ID';"
```

### Paso 4 — Verificar que llega telemetría (una vez conectado el firmware)
```bash
docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/ESP32_NUEVO_ID/#" -v -C 5 -W 10
```

### Paso 5 — Verificar estado en API
```bash
curl -s http://localhost:8080/api/status/ESP32_NUEVO_ID | python3 -m json.tool
```

### Paso 6 — Verificar heartbeat (estado FSM)
```sql
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, state, last_seen, online FROM device_status WHERE device_id = 'ESP32_NUEVO_ID';"
```

### Ejemplo real — Osmosis 01
```sql
-- Device ya existente para referencia:
SELECT device_id, display_name, client_id, enabled FROM devices WHERE device_id = 'ESP32_D0448EC92DF4';
```

---

## 4. Alta manual de un nuevo cliente

Un **cliente** es una empresa u organización que tiene uno o más dispositivos. Cada cliente tiene su propia org en Grafana y solo ve sus dispositivos.

### Paso 1 — Crear organización en Grafana
```bash
curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs \
  -H "Content-Type: application/json" \
  -d '{"name": "NombreCliente"}'
```
Respuesta: `{"message":"Organization created","orgId":4}` → guardar el `orgId`.

### Paso 2 — Crear datasource PostgreSQL en la nueva org
```bash
# Reemplazar X-Grafana-Org-Id con el orgId obtenido en paso 1
curl -s -u admin:admin123 \
  -H "X-Grafana-Org-Id: 4" \
  -X POST http://localhost:3000/api/datasources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "grafana-postgresql-datasource",
    "type": "grafana-postgresql-datasource",
    "access": "proxy",
    "url": "ro-postgres:5432",
    "user": "user",
    "isDefault": true,
    "jsonData": {
      "database": "iot_db",
      "sslmode": "disable",
      "maxOpenConns": 100,
      "maxIdleConns": 100,
      "maxIdleConnsAuto": true,
      "connMaxLifetime": 14400
    },
    "secureJsonData": {"password": "password"}
  }'
```
Respuesta incluye `"uid":"xxxx"` → guardar ese UID para el paso de dashboards.

### Paso 3 — Insertar cliente en DB
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
INSERT INTO clients (name, grafana_org_id, email)
VALUES ('NombreCliente', 4, 'contacto@cliente.com')
RETURNING id, name, grafana_org_id;
"
```
Guardar el `id` retornado.

### Paso 4 — Vincular dispositivos al cliente
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE devices SET client_id = <id_del_cliente>
WHERE device_id = 'ESP32_NUEVO_ID';
"
```

### Paso 5 — Crear usuario Grafana para el cliente
```bash
curl -s -u admin:admin123 -X POST http://localhost:3000/api/admin/users \
  -H "Content-Type: application/json" \
  -d '{
    "name": "NombreCliente",
    "email": "contacto@cliente.com",
    "login": "usuario_cliente",
    "password": "contraseña_segura",
    "OrgId": 4
  }'
```

### Paso 6 — Asignar usuario a la org (rol Viewer)
```bash
# Reemplazar userId con el id devuelto en paso 5
curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs/4/users \
  -H "Content-Type: application/json" \
  -d '{"loginOrEmail":"usuario_cliente","role":"Viewer"}'
```

### Paso 7 — Importar dashboards (ver sección 5)

### Paso 8 — Validar login
Acceder a `http://159.112.132.176:3000` con las credenciales del usuario. El selector de dispositivos debe mostrar solo los devices del cliente.

### Ejemplo real — Aquaser (ya configurado)
```
Org: Aquaser (orgId=3)
Datasource UID: cfnj8qajb7f9ca
Cliente DB: id=2, grafana_org_id=3
Device: ESP32_D0448EC92DF4 (Osmosis 01)
Usuario Grafana: aquaser / aguasegura
```

---

## 5. Importar dashboards en una org nueva

Los dashboards JSON están en `/home/ubuntu/iot-server/dashboards/dashboards/`.

### Paso 1 — Obtener UID del datasource en la nueva org
```bash
curl -s -u admin:admin123 -H "X-Grafana-Org-Id: <ORG_ID>" \
  http://localhost:3000/api/datasources | python3 -c \
  "import json,sys; [print(d['uid']) for d in json.load(sys.stdin)]"
```

### Paso 2 — Obtener UID del datasource de Main Org (el que está en los JSON)
```bash
curl -s -u admin:admin123 -H "X-Grafana-Org-Id: 1" \
  http://localhost:3000/api/datasources | python3 -c \
  "import json,sys; [print(d['uid']) for d in json.load(sys.stdin)]"
# → dfdcy982omdxce  (UID de Main Org — hardcodeado en los JSON)
```

### Paso 3 — Importar los 3 dashboards reemplazando el UID
```bash
OLD_UID="dfdcy982omdxce"
NEW_UID="<uid_datasource_nueva_org>"
ORG_ID="<org_id_nueva>"

for f in /home/ubuntu/iot-server/dashboards/dashboards/*.json; do
  echo "Importando: $(basename $f)"
  python3 -c "
import json
with open('$f') as fp:
    content = fp.read()
content = content.replace('$OLD_UID', '$NEW_UID')
dash = json.loads(content)
dash.pop('id', None)
dash['uid'] = None
print(json.dumps({'dashboard': dash, 'overwrite': True, 'folderId': 0}))
" | curl -s -u admin:admin123 \
    -H "Content-Type: application/json" \
    -H "X-Grafana-Org-Id: $ORG_ID" \
    -X POST http://localhost:3000/api/dashboards/import \
    --data-binary @- | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('title','?'), '→', d.get('url','error'))"
done
```

### Paso 4 — Validar variables del dashboard
En Grafana: entrar a la org → abrir cualquier dashboard → **Settings** (engranaje) → **Variables** → ejecutar query de la variable `device` → debe listar los devices del cliente.

### Paso 5 — Verificar filtrado multi-tenant
```bash
# Desde Main Org (admin) debe ver todos los devices
curl -s -u admin:admin123 -H "X-Grafana-Org-Id: 1" \
  http://localhost:3000/api/datasources/proxy/1/api/ds/query \
  --data-raw '{}' 2>/dev/null || true

# Verificar en DB que el device tiene client_id correcto
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT d.device_id, d.display_name, c.name as cliente, c.grafana_org_id
   FROM devices d LEFT JOIN clients c ON d.client_id = c.id ORDER BY d.device_id;"
```

### UIDs de datasource actuales por org

| Org | ID | UID Datasource |
|---|---|---|
| Main Org. | 1 | `dfdcy982omdxce` |
| LABORATORIO | 2 | `ffmjkdcfxfn5se` |
| Aquaser | 3 | `cfnj8qajb7f9ca` |

---

## 6. Vincular un device a un cliente existente

```sql
-- Ver clientes disponibles
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT id, name, grafana_org_id FROM clients ORDER BY id;"

-- Ver devices sin cliente asignado
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, display_name, client_id FROM devices WHERE client_id IS NULL;"

-- Vincular
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE devices SET client_id = <id_cliente>
WHERE device_id = 'ESP32_XXXXXXXX';
"

-- Verificar
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name, c.name, c.grafana_org_id
FROM devices d JOIN clients c ON d.client_id = c.id
WHERE d.device_id = 'ESP32_XXXXXXXX';
"
```

---

## 7. Mover un dispositivo entre clientes

```sql
-- Ver estado actual
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name, d.client_id, c.name as cliente_actual
FROM devices d LEFT JOIN clients c ON d.client_id = c.id
WHERE d.device_id = 'ESP32_D0448EC92DF4';
"

-- Mover al cliente con id=1 (LABORATORIO)
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE devices SET client_id = 1
WHERE device_id = 'ESP32_D0448EC92DF4';
"

-- Desasignar de cualquier cliente (solo admin lo ve)
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE devices SET client_id = NULL
WHERE device_id = 'ESP32_D0448EC92DF4';
"
```

**Validar después del movimiento:**
1. Entrar a Grafana con el usuario del cliente **anterior** → el device NO debe aparecer en el selector.
2. Entrar con el usuario del cliente **nuevo** → el device SÍ debe aparecer.
3. Main Org (admin) siempre ve todos los devices independientemente del cliente.

```sql
-- Confirmar en DB
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name, c.name, c.grafana_org_id
FROM devices d LEFT JOIN clients c ON d.client_id = c.id ORDER BY d.device_id;
"
```

---

## 8. Verificar que el filtrado multi-tenant funciona

### Verificar query del selector de devices en Grafana
La variable `device` en los dashboards usa:
```sql
SELECT
    d.device_id AS __value,
    COALESCE(d.display_name, d.device_id) AS __text
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE
  AND (${__org.id} = 1 OR c.grafana_org_id = ${__org.id})
ORDER BY COALESCE(d.display_name, d.device_id)
```

- `${__org.id} = 1` → Main Org ve todos los devices.
- `c.grafana_org_id = ${__org.id}` → cada org ve solo sus devices.

### Verificar en DB que el mapeo es correcto
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT
    d.device_id,
    d.display_name,
    d.client_id,
    c.name        AS cliente,
    c.grafana_org_id
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE
ORDER BY c.grafana_org_id, d.display_name;
"
```

### Verificar qué devices ve cada org (simulación de la query)
```sql
-- Simular query de Aquaser (org_id = 3)
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE AND (3 = 1 OR c.grafana_org_id = 3)
ORDER BY d.display_name;
"

-- Simular query de LABORATORIO (org_id = 2)
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE AND (2 = 1 OR c.grafana_org_id = 2)
ORDER BY d.display_name;
"
```

### Problema de acceso cruzado
Un usuario de Aquaser NO debe ver datos de LABORATORIO. El filtro actúa a nivel de la variable Grafana: si el `device_id` no aparece en el selector, no puede seleccionarse.

Si un usuario reporta que ve datos de otro cliente, verificar:
1. Que el `client_id` del device esté asignado correctamente.
2. Que la variable `device` del dashboard esté configurada con la query mostrada arriba.

---

## 9. Alertas

### Ver alertas activas de un dispositivo
```bash
curl -s "http://localhost:8080/api/alerts/ESP32_D0448EC92DF4?active=true&limit=20" | python3 -m json.tool
```

### Ver historial de alertas (activas + resueltas)
```bash
curl -s "http://localhost:8080/api/alerts/ESP32_D0448EC92DF4?active=false&limit=20" | python3 -m json.tool
```

### Ver alertas directamente en DB
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT id, code, severity, active, notification_count,
       created_at AT TIME ZONE 'UTC' AS created_utc,
       resolved_at AT TIME ZONE 'UTC' AS resolved_utc
FROM alerts
WHERE device_id = 'ESP32_D0448EC92DF4'
ORDER BY created_at DESC
LIMIT 20;
"
```

### ACK manual de una alerta (por ID)
```bash
# Obtener el id primero
curl -s "http://localhost:8080/api/alerts/ESP32_D0448EC92DF4?active=true" | python3 -c \
  "import json,sys; [print(a['id'], a['code'], a['severity']) for a in json.load(sys.stdin)]"

# ACK
curl -s -X POST http://localhost:8080/api/alerts/ack/42 | python3 -m json.tool
```

### ACK manual desde DB
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE alerts
SET active = FALSE, resolved_at = NOW(), updated_at = NOW()
WHERE id = 42 AND active = TRUE;
"
```

### Resolver todas las alertas activas de un device
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE alerts
SET active = FALSE, resolved_at = NOW(), updated_at = NOW()
WHERE device_id = 'ESP32_D0448EC92DF4' AND active = TRUE;
"
```

### Verificar notificaciones Telegram
```bash
docker logs ro-python-worker --tail 100 2>&1 | grep "TELEGRAM"
# Esperar ver: [ALERT] TELEGRAM SENT — ESP32_D0448EC92DF4 HIGH_TDS_OUTPUT
```

### Verificar notificaciones Email
```bash
docker logs ro-python-worker --tail 100 2>&1 | grep "EMAIL"
```

### Ver thresholds actuales
```bash
docker exec ro-python-worker python3 -c "
import alert_config as acfg
print('OFFLINE_SEC:    ', acfg.THRESH_OFFLINE_SEC)
print('TDS_OUT_WARN:   ', acfg.THRESH_TDS_OUT_WARN, 'ppm')
print('TDS_OUT_RESOLVE:', acfg.THRESH_TDS_OUT_RESOLVE, 'ppm')
print('LOW_PRESSURE:   ', acfg.THRESH_LOW_PRESSURE, 'bar')
print('HIGH_PRESSURE:  ', acfg.THRESH_HIGH_PRESSURE, 'bar')
print('LOW_EFFICIENCY: ', acfg.THRESH_LOW_EFFICIENCY)
print('REMINDER_SEC:   ', acfg.THRESH_REMINDER_SEC, 's')
print()
print('RULE_CONFIG:')
for code, cfg in acfg.RULE_CONFIG.items():
    print(f'  {code}: trigger={cfg[\"trigger_seconds\"]}s clear={cfg[\"clear_seconds\"]}s')
"
```

### Cambiar un threshold sin reiniciar
Los thresholds se configuran via env vars en `docker-compose.yml`. Editar y reiniciar:
```bash
# Editar docker-compose.yml y agregar en environment de iot-python-worker:
#   - THRESH_TDS_OUT_WARN=100.0
cd /home/ubuntu/iot-server
docker-compose up -d iot-python-worker
```

### Ver logs de alertas
```bash
docker logs ro-python-worker --tail 200 2>&1 | grep "\[ALERT\]"
```

### Códigos de alerta del sistema MVP

| Código | Severidad | Condición |
|---|---|---|
| `DEVICE_OFFLINE` | CRITICAL | Sin telemetría > 90s |
| `DEVICE_RECONNECTED` | INFO | Reconexión detectada |
| `HIGH_TDS_OUTPUT` | WARNING | TDS salida > 80 ppm (trigger 30s) |
| `LOW_PRESSURE` | WARNING | Presión < 2.0 bar en producción (trigger 30s) |
| `HIGH_PRESSURE` | CRITICAL | Presión > 9.0 bar (inmediato) |
| `NO_PERMEATE_FLOW` | CRITICAL | Sin caudal en PRODUCING > 30s |
| `LOW_EFFICIENCY` | WARNING | Eficiencia < 0.85 (trigger 60s) |
| `SENSOR_INVALID` | WARNING | NaN/Inf o valores fuera de rango físico |

---

## 10. IA

### Verificar estado del motor IA
```bash
docker logs ro-python-worker --tail 50 2>&1 | grep "AI-ENGINE"
```
Si AI_ENDPOINT_URL no está configurado: `[AI-ENGINE] AI_ENDPOINT_URL not set — engine disabled`.

### Ver modo IA de un dispositivo
```bash
curl -s http://localhost:8080/api/device/ESP32_D0448EC92DF4/ai-mode | python3 -m json.tool
```

### Cambiar modo IA de un dispositivo
```bash
# Modos: OFF | VIEWER | AUTO
curl -s -X POST http://localhost:8080/api/device/ESP32_D0448EC92DF4/ai-mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "VIEWER"}'
```

O directamente en DB:
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
UPDATE devices SET ai_mode = 'VIEWER'
WHERE device_id = 'ESP32_D0448EC92DF4';
"
-- Valores válidos: 'OFF', 'VIEWER', 'AUTO'
```

### Ver decisiones IA recientes
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT device_id, ai_mode, decision, confidence, suggested_cmd,
       executed, exec_status, created_at AT TIME ZONE 'UTC'
FROM ai_decisions
WHERE device_id = 'ESP32_D0448EC92DF4'
ORDER BY created_at DESC
LIMIT 10;
"
```

### Ver requests y respuestas IA en logs
```bash
docker logs ro-python-worker --tail 200 2>&1 | grep "AI-ENGINE"
# Buscar:
# [AI-ENGINE] req=abc123… device=ESP32_... mode=VIEWER state=PRODUCING samples=60
# [AI-ENGINE] req=abc123… decision=NONE confidence=0.94
# [AI-ENGINE] VIEWER — logged suggestion: FLUSH
# [AI-ENGINE] AUTO blocked by policy: cooldown active — 245s remaining
```

### Verificar cooldown entre comandos IA
```sql
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT suggested_cmd, executed, exec_status, exec_result,
       created_at AT TIME ZONE 'UTC'
FROM ai_decisions
WHERE device_id = 'ESP32_D0448EC92DF4'
  AND executed = TRUE
ORDER BY created_at DESC
LIMIT 5;
"
```

### Comandos bloqueados por política
En los logs se ve: `[AI-ENGINE] AUTO blocked by policy: <motivo>`

Motivos posibles:
- `cooldown active — Xs remaining` → esperar antes del próximo comando
- `FSM state 'IDLE' does not allow FLUSH` → estado FSM incompatible
- `anti-oscillation: START→STOP too fast` → cambio rápido bloqueado
- `command 'XXX' not in whitelist` → comando no permitido

### Variables de configuración IA
```bash
# Ver configuración activa
docker exec ro-python-worker python3 -c "
import os
keys = ['AI_ENDPOINT_URL','AI_POLL_INTERVAL_SEC','AI_WINDOW_SECONDS',
        'AI_WINDOW_MAX_SAMPLES','AI_TIMEOUT_SEC','AI_AUTO_COOLDOWN_SEC']
for k in keys:
    print(f'{k}={os.getenv(k,\"<no configurado>\")}')
"
```

---

## 11. Backup y restore

### Backup completo de PostgreSQL
```bash
docker exec ro-postgres pg_dump -U user iot_db > \
  /home/ubuntu/iot-server/backups/iot_db_$(date +%Y%m%d_%H%M%S).sql
```

### Backup solo estructura (sin datos)
```bash
docker exec ro-postgres pg_dump -U user --schema-only iot_db > \
  /home/ubuntu/iot-server/backups/schema_$(date +%Y%m%d).sql
```

### Restore de PostgreSQL
```bash
# PRECAUCIÓN: elimina datos existentes
docker exec -i ro-postgres psql -U user -d iot_db < /ruta/al/backup.sql
```

### Restore a una DB nueva
```bash
docker exec ro-postgres createdb -U user iot_db_restore
docker exec -i ro-postgres psql -U user -d iot_db_restore < /ruta/al/backup.sql
```

### Backup de dashboards Grafana
Los JSON versionados ya están en el repo:
```bash
ls /home/ubuntu/iot-server/dashboards/dashboards/
# kairox-detail.json  kairox-overview.json  main_dashboard.json

# Exportar dashboard actualizado desde Grafana API
curl -s -u admin:admin123 \
  http://localhost:3000/api/dashboards/uid/a76cc2da-e42c-46c4-9e33-5eda726245fe \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['dashboard'], indent=2))" \
  > /home/ubuntu/iot-server/dashboards/dashboards/kairox_$(date +%Y%m%d).json
```

### Backup del docker-compose y configuración
```bash
cp /home/ubuntu/iot-server/docker-compose.yml \
   /home/ubuntu/iot-server/backups/docker-compose_$(date +%Y%m%d).yml
cp /home/ubuntu/iot-server/python_iot/alert_config.py \
   /home/ubuntu/iot-server/backups/alert_config_$(date +%Y%m%d).py
```

### Script de backup completo
```bash
mkdir -p /home/ubuntu/iot-server/backups
FECHA=$(date +%Y%m%d_%H%M%S)
DEST=/home/ubuntu/iot-server/backups

# DB
docker exec ro-postgres pg_dump -U user iot_db > $DEST/iot_db_$FECHA.sql

# Configs
cp /home/ubuntu/iot-server/docker-compose.yml $DEST/docker-compose_$FECHA.yml
cp /home/ubuntu/iot-server/python_iot/alert_config.py $DEST/alert_config_$FECHA.py

# Dashboards
tar czf $DEST/dashboards_$FECHA.tar.gz \
  /home/ubuntu/iot-server/dashboards/

echo "Backup completado en $DEST"
ls -lh $DEST/*$FECHA*
```

---

## 12. Troubleshooting

---

### Device offline — no llega telemetría

**Síntomas:** Dashboard sin datos, alerta `DEVICE_OFFLINE` activa.

**Diagnóstico:**
```bash
# Ver cuándo fue el último contacto
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, last_seen, online FROM device_status WHERE device_id = 'ESP32_D0448EC92DF4';"

# Ver si llega algo por MQTT
docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/ESP32_D0448EC92DF4/#" -v -C 3 -W 5

# Ver alertas activas
curl -s "http://localhost:8080/api/alerts/ESP32_D0448EC92DF4?active=true" | python3 -m json.tool
```

**Solución:**
- Si MQTT no recibe nada: verificar conexión física del ESP32 y WiFi.
- Si MQTT recibe pero la DB no tiene datos: `docker logs ro-python-worker --tail 50 2>&1 | grep ERROR`.
- Si el backend está caído: `docker-compose -f /home/ubuntu/iot-server/docker-compose.yml up -d iot-python-worker`.

---

### Device no aparece en el selector de Grafana

**Síntomas:** El dropdown del dashboard aparece vacío o sin el device esperado.

**Diagnóstico:**
```sql
-- Verificar client_id asignado
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT d.device_id, d.display_name, d.client_id, d.enabled, c.grafana_org_id
   FROM devices d LEFT JOIN clients c ON d.client_id = c.id
   WHERE d.device_id = 'ESP32_D0448EC92DF4';"
```

**Solución:**
- Si `client_id` es NULL → asignar cliente (ver sección 6).
- Si `grafana_org_id` no coincide con la org del usuario → mover device al cliente correcto (sección 7).
- Si `enabled = FALSE` → `UPDATE devices SET enabled = TRUE WHERE device_id = '...';`

---

### No llegan alertas Telegram / Email

**Síntomas:** Alerta activa en DB pero sin notificación recibida.

**Diagnóstico:**
```bash
# Ver si el worker intentó enviar
docker logs ro-python-worker --tail 200 2>&1 | grep -E "TELEGRAM|EMAIL"

# Verificar que las variables están configuradas
docker exec ro-python-worker env | grep -E "TELEGRAM|SMTP"

# Ver si la alerta tiene notification_count > 0
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT code, severity, notification_count, last_notified_at FROM alerts
   WHERE device_id = 'ESP32_D0448EC92DF4' AND active = TRUE;"
```

**Solución:**
- `TELEGRAM QUEUE FULL` → se descartó por queue llena (poco frecuente).
- `TELEGRAM FAILED` con HTTP error → verificar token y chat_id en docker-compose.yml.
- Variables vacías → agregar `TELEGRAM_TOKEN` y `TELEGRAM_ADMIN_CHAT` en docker-compose.yml y reiniciar.
- `notification_count = 0` → la alerta nunca pasó el filtro `ALERT_CODES`. Verificar que el `code` esté en `alert_config.py → ALERT_CODES`.

---

### IA no responde / no hace ciclos

**Síntomas:** No se ven logs `[AI-ENGINE]` en el backend.

**Diagnóstico:**
```bash
docker logs ro-python-worker --tail 100 2>&1 | grep "AI-ENGINE"

# Ver si AI_ENDPOINT_URL está configurada
docker exec ro-python-worker env | grep AI_ENDPOINT_URL
```

**Solución:**
- `AI_ENDPOINT_URL not set` → configurar la variable en docker-compose.yml y reiniciar.
- `timeout after 10s` → el endpoint IA no responde. Verificar conectividad al servicio externo.
- `validation failed` → la respuesta de la IA tiene campos incorrectos. Ver sección 10 para validaciones.
- `ai_mode = OFF` → cambiar a VIEWER o AUTO (ver sección 10).

---

### Datasource roto / dashboards vacíos

**Síntomas:** Grafana muestra "No data" en todos los paneles o error de datasource.

**Diagnóstico:**
```bash
# Verificar que PostgreSQL está levantado
docker ps | grep ro-postgres

# Test de conexión desde Grafana
curl -s -u admin:admin123 -H "X-Grafana-Org-Id: 3" \
  http://localhost:3000/api/datasources | python3 -m json.tool | grep -E '"uid"|"name"'
```

En Grafana UI: **Connections → Data Sources → grafana-postgresql-datasource → Save & test**.

**Solución:**
- Si PostgreSQL está caído: `docker-compose -f /home/ubuntu/iot-server/docker-compose.yml up -d db`
- Si el datasource apunta al UID equivocado: reimportar dashboards (sección 5).
- Si la contraseña cambió: editar el datasource en Grafana → actualizar `secureJsonData.password`.

---

### Dashboards vacíos (datasource OK, DB con datos)

**Síntomas:** Test del datasource OK pero los paneles no muestran datos.

**Diagnóstico:**
```sql
-- Verificar que hay datos en el período del dashboard
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT MAX(time) FROM telemetry_process WHERE device_id = 'ESP32_D0448EC92DF4';"
```

**Solución:**
- Verificar rango de tiempo en Grafana (esquina superior derecha) — puede estar en el futuro.
- Verificar que la variable `device` tiene el device_id correcto seleccionado.
- Verificar que el device está generando datos: sección 2 → "Ver telemetría MQTT en vivo".

---

### MQTT desconectado

**Síntomas:** Backend no recibe mensajes, logs muestran reconexiones.

**Diagnóstico:**
```bash
docker logs ro-python-worker --tail 50 2>&1 | grep -i "mqtt\|connect"
docker ps | grep ro-mosquitto
```

**Solución:**
```bash
# Reiniciar Mosquitto
docker-compose -f /home/ubuntu/iot-server/docker-compose.yml restart mosquitto

# Reiniciar backend (reconectará automáticamente)
docker-compose -f /home/ubuntu/iot-server/docker-compose.yml restart iot-python-worker
```

---

### Grafana org sin devices en el selector

**Síntomas:** Usuario entra a su org y el selector aparece vacío.

**Diagnóstico:**
```sql
-- Ver mapeo completo
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name, d.enabled, c.name, c.grafana_org_id
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
ORDER BY c.grafana_org_id, d.display_name;
"
```

**Solución:**
1. Verificar que el device tiene `client_id` asignado → sección 6.
2. Verificar que el `grafana_org_id` del cliente coincide con la org en Grafana → sección 4 paso 3.
3. Verificar que el datasource en esa org está correctamente configurado → sección 5 paso 1.
4. Verificar que los dashboards usan el UID de datasource correcto → sección 5.

---

## 13. Checklist de onboarding de un nuevo cliente

```
NUEVO CLIENTE: ___________________
DISPOSITIVO:   ___________________
FECHA:         ___________________

[ ] 1. DEVICE — Insertar en DB
      docker exec ro-postgres psql -U user -d iot_db -c "
      INSERT INTO devices (device_id, display_name, enabled, ai_mode)
      VALUES ('<DEVICE_ID>', '<NOMBRE_EQUIPO>', TRUE, 'OFF')
      ON CONFLICT (device_id) DO UPDATE SET display_name=EXCLUDED.display_name;"

[ ] 2. DEVICE — Crear config inicial
      docker exec ro-postgres psql -U user -d iot_db -c "
      INSERT INTO device_config (device_id, pump_power_kw, cost_kwh, daily_target_liters)
      VALUES ('<DEVICE_ID>', 0.75, 0.12, 500.0) ON CONFLICT (device_id) DO NOTHING;"

[ ] 3. GRAFANA — Crear organización y anotar orgId
      curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs \
        -H "Content-Type: application/json" -d '{"name": "<NOMBRE_CLIENTE>"}'
      # → orgId: ____

[ ] 4. GRAFANA — Crear datasource en nueva org y anotar UID
      curl -s -u admin:admin123 -H "X-Grafana-Org-Id: <ORG_ID>" \
        -X POST http://localhost:3000/api/datasources \
        -H "Content-Type: application/json" \
        -d '{"name":"grafana-postgresql-datasource","type":"grafana-postgresql-datasource",
             "access":"proxy","url":"ro-postgres:5432","user":"user","isDefault":true,
             "jsonData":{"database":"iot_db","sslmode":"disable","maxOpenConns":100,
             "maxIdleConns":100,"maxIdleConnsAuto":true,"connMaxLifetime":14400},
             "secureJsonData":{"password":"password"}}'
      # → uid: ____

[ ] 5. DB — Crear cliente y vincular device
      docker exec ro-postgres psql -U user -d iot_db -c "
      INSERT INTO clients (name, grafana_org_id, email)
      VALUES ('<NOMBRE_CLIENTE>', <ORG_ID>, '<EMAIL>') RETURNING id;"
      # → client id: ____

      docker exec ro-postgres psql -U user -d iot_db -c "
      UPDATE devices SET client_id = <CLIENT_ID> WHERE device_id = '<DEVICE_ID>';"

[ ] 6. GRAFANA — Importar los 3 dashboards con UID correcto
      OLD_UID="dfdcy982omdxce"; NEW_UID="<UID_DATASOURCE>"; ORG_ID="<ORG_ID>"
      for f in /home/ubuntu/iot-server/dashboards/dashboards/*.json; do
        python3 -c "
      import json
      with open('$f') as fp: content=fp.read()
      content=content.replace('$OLD_UID','$NEW_UID')
      dash=json.loads(content); dash.pop('id',None); dash['uid']=None
      print(json.dumps({'dashboard':dash,'overwrite':True,'folderId':0}))
      " | curl -s -u admin:admin123 -H "Content-Type: application/json" \
          -H "X-Grafana-Org-Id: $ORG_ID" \
          -X POST http://localhost:3000/api/dashboards/import --data-binary @-
      done

[ ] 7. GRAFANA — Crear usuario y asignar a org como Viewer
      curl -s -u admin:admin123 -X POST http://localhost:3000/api/admin/users \
        -H "Content-Type: application/json" \
        -d '{"name":"<NOMBRE>","email":"<EMAIL>","login":"<LOGIN>",
             "password":"<PASSWORD>","OrgId":<ORG_ID>}'

      curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs/<ORG_ID>/users \
        -H "Content-Type: application/json" \
        -d '{"loginOrEmail":"<LOGIN>","role":"Viewer"}'

[ ] 8. VALIDAR — Device recibe telemetría
      docker exec ro-mosquitto mosquitto_sub -h localhost \
        -t "fyntek/<DEVICE_ID>/#" -v -C 3 -W 10

[ ] 9. VALIDAR — Device aparece en selector Grafana
      Entrar como <LOGIN> → abrir dashboard → verificar dropdown

[ ] 10. VALIDAR — No hay acceso cruzado
       Entrar como <LOGIN> → verificar que solo ve SUS devices

[ ] 11. VALIDAR — Alertas funcionan
       curl -s "http://localhost:8080/api/alerts/<DEVICE_ID>?active=false&limit=5" \
         | python3 -m json.tool

[ ] ONBOARDING COMPLETADO ✓
```

---

## 14. Automated Backups

### Estructura de directorios

```
/home/ubuntu/
├── backups/
│   └── postgres/
│       ├── iot_db_20260529_020000.sql.gz
│       ├── iot_db_20260530_020000.sql.gz
│       └── ...  (retención 30 días)
├── logs/
│   └── backup.log
└── iot-server/
    └── scripts/
        ├── backup_postgres.sh
        └── test_restore.sh
```

### Crear directorios (una sola vez)
```bash
mkdir -p /home/ubuntu/backups/postgres
mkdir -p /home/ubuntu/logs
chmod +x /home/ubuntu/iot-server/scripts/backup_postgres.sh
chmod +x /home/ubuntu/iot-server/scripts/test_restore.sh
```

### Ejecutar backup manual
```bash
/home/ubuntu/iot-server/scripts/backup_postgres.sh
```

Salida esperada:
```
[BACKUP 2026-05-29 02:00:01] Iniciando backup: iot_db
[BACKUP 2026-05-29 02:00:01] Paso 1/4: Ejecutando pg_dump...
[BACKUP 2026-05-29 02:00:03] Paso 1/4 completado: .../iot_db_20260529_020001.sql (4.2M)
[BACKUP 2026-05-29 02:00:03] Paso 2/4: Comprimiendo con gzip...
[BACKUP 2026-05-29 02:00:03] Paso 2/4 completado: .../iot_db_20260529_020001.sql.gz (512K)
[BACKUP 2026-05-29 02:00:03] Paso 3/4: Validando integridad del gzip...
[BACKUP 2026-05-29 02:00:03] Paso 3/4 completado: gzip OK
[BACKUP 2026-05-29 02:00:03] Paso 4/4: Rotando backups anteriores a 30 días...
[BACKUP 2026-05-29 02:00:03] Backup completado correctamente
```

### Dry-run (sin ejecutar)
```bash
/home/ubuntu/iot-server/scripts/backup_postgres.sh --dry-run
```

### Verificar integridad del backup
```bash
/home/ubuntu/iot-server/scripts/test_restore.sh
# Usa el backup más reciente, lo restaura en una DB temporal, verifica tablas, limpia.
```

Verificar un backup específico:
```bash
/home/ubuntu/iot-server/scripts/test_restore.sh /home/ubuntu/backups/postgres/iot_db_20260529_020001.sql.gz
```

### Configurar cron — backup diario a las 2:00 AM

```bash
crontab -e
```

Agregar línea:
```
0 2 * * * /home/ubuntu/iot-server/scripts/backup_postgres.sh >> /home/ubuntu/logs/backup.log 2>&1
```

Verificar que quedó guardado:
```bash
crontab -l | grep backup
```

### Configurar cron — test de restore semanal (domingos 3:00 AM)

```bash
crontab -e
```

Agregar línea:
```
0 3 * * 0 /home/ubuntu/iot-server/scripts/test_restore.sh >> /home/ubuntu/logs/backup.log 2>&1
```

### Ver logs de backup
```bash
tail -50 /home/ubuntu/logs/backup.log
```

### Ver backups disponibles
```bash
ls -lht /home/ubuntu/backups/postgres/*.sql.gz
# Mostrar tamaños y fechas, más reciente primero
```

### Política de retención
El script elimina automáticamente backups con más de **30 días** de antigüedad en cada ejecución. Para cambiar la retención, editar `RETENTION_DAYS` en `backup_postgres.sh`:

```bash
# En /home/ubuntu/iot-server/scripts/backup_postgres.sh, línea ~18:
RETENTION_DAYS=30   # cambiar a 7, 14, 60, etc.
```

### Restore manual desde un backup específico
```bash
# PRECAUCIÓN: sobrescribe la DB de producción
BACKUP_FILE="/home/ubuntu/backups/postgres/iot_db_20260529_020001.sql.gz"

# Detener backend para evitar escrituras durante restore
docker stop ro-python-worker

# Restaurar
gunzip -c "$BACKUP_FILE" | docker exec -i ro-postgres psql -U user -d iot_db -q

# Verificar
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, MAX(time) FROM telemetry_process GROUP BY device_id;"

# Reiniciar backend
docker start ro-python-worker
```

---

## 15. Known Architectural Limitations and Future Improvements

### Limitaciones actuales

#### Aislamiento multi-tenant basado en Grafana (no en DB)
El filtrado de dispositivos por cliente vive en la query de la variable Grafana. Si alguien con acceso a la DB o a la API Flask conoce un `device_id`, puede consultar sus datos directamente via SQL o API sin restricción. **La DB no impone Row Level Security.**

Impacto real: operadores con acceso directo a PostgreSQL o a los endpoints `/api/status/<device_id>` pueden acceder a cualquier dispositivo independientemente del cliente asignado.

#### Onboarding manual
Crear un nuevo cliente requiere ejecutar ~6 comandos manuales (DB + 4 llamadas a Grafana API). Sin automatización, es propenso a errores de consistencia entre DB y Grafana.

#### Sin gestor de migraciones de schema
Los cambios de schema se aplican manualmente via `ALTER TABLE IF NOT EXISTS` en el arranque del backend (`main()`) o ejecutando `schema.sql` directamente. No hay historial de versiones ni rollback automático.

#### Secretos en docker-compose.yml
Las credenciales (DB password, MQTT password, Grafana admin) viven hardcodeadas en `docker-compose.yml` en lugar de en un archivo `.env` separado o un gestor de secretos. Cualquiera con acceso al repositorio las ve.

#### Backup sin offsite
Los backups se generan en `/home/ubuntu/backups/postgres/` en el mismo servidor. Si el VPS se destruye completamente, los backups se pierden junto con los datos.

#### Sin monitoring del proceso de backup
El cron ejecuta el script pero no hay alerta si el backup falla. Los errores solo quedan en `/home/ubuntu/logs/backup.log`.

#### Volúmenes Docker sin backup automático
Los volúmenes `iot-server_grafana_data` y `iot-server_postgres_data` no tienen snapshot automatizado. El backup de Grafana (orgs, usuarios, dashboards) requiere reconstrucción manual vía API.

#### Escalabilidad: un solo proceso Python
El backend es un proceso Python único con threads. No escala horizontalmente. Una sola instancia maneja todos los devices.

#### Sin health checks en docker-compose
Los contenedores tienen `restart: always` pero sin `healthcheck`. Docker no sabe si el proceso dentro del container está funcionando correctamente (vs. simplemente corriendo).

---

### Mejoras propuestas

#### Corto plazo (operacional)

**1. Mover secretos a `.env`**

Modificar `docker-compose.yml` para consumir variables desde `.env`:
```yaml
services:
  iot-python-worker:
    env_file:
      - .env
  db:
    env_file:
      - .env
  grafana:
    env_file:
      - .env
```
El archivo `.env` nunca entra al repositorio. `.env.example` documenta qué variables se necesitan.

**2. Backup offsite automático**

Extender `backup_postgres.sh` para subir a S3, Backblaze B2 o similar:
```bash
# Al final del script, después de validar el gzip:
aws s3 cp "$FINAL_GZ" "s3://kairox-backups/postgres/$(basename $FINAL_GZ)"
# o con rclone para cualquier proveedor de storage
```

**3. Alerta si backup falla**

Agregar notificación Telegram al script si hay error:
```bash
fail() {
  MSG="[KAIROX BACKUP] ERROR en servidor $(hostname): $*"
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_ADMIN_CHAT}&text=${MSG}" > /dev/null
  exit 1
}
```

**4. Health checks en docker-compose**

```yaml
services:
  iot-python-worker:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/status/healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 3
  db:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d iot_db"]
      interval: 10s
      retries: 5
```

#### Mediano plazo (arquitectura)

**5. Alembic para migraciones de schema**

Reemplazar los `ALTER TABLE IF NOT EXISTS` en `main()` por migraciones versionadas:
```
alembic/
├── env.py
├── versions/
│   ├── 001_initial_schema.py
│   ├── 002_add_notification_count.py
│   └── 003_add_installed_at.py
```
Permite rollback, historial de cambios, y despliegues seguros.

**6. PostgreSQL Row Level Security (RLS)**

Agregar políticas RLS para aislar datos por `client_id` a nivel de DB:
```sql
ALTER TABLE telemetry_process ENABLE ROW LEVEL SECURITY;

CREATE POLICY client_isolation ON telemetry_process
  USING (device_id IN (
    SELECT d.device_id FROM devices d
    JOIN clients c ON d.client_id = c.id
    WHERE c.grafana_org_id = current_setting('app.grafana_org_id')::int
  ));
```
Esto hace que el aislamiento sea real e independiente de la capa de presentación.

**7. Script de onboarding automatizado**

Ver `CLIENT_ONBOARDING_AUTOMATION.md` para el diseño completo del script `create_client.sh`.

**8. Backup de volumen Grafana**

```bash
# Snapshot del volumen grafana_data
docker run --rm \
  -v iot-server_grafana_data:/source:ro \
  -v /home/ubuntu/backups/grafana:/backup \
  alpine tar czf /backup/grafana_$(date +%Y%m%d).tar.gz -C /source .
```

#### Largo plazo (madurez operacional)

**9. Gestión centralizada de secretos**

Migrar credenciales a HashiCorp Vault, AWS SSM Parameter Store, o Doppler. El backend lee secretos al arrancar en lugar de consumirlos del ambiente.

**10. Infraestructura como código**

Definir el servidor completo con Terraform (VPS + firewall + DNS) y Ansible (bootstrap + deploy). Permite recrear la infraestructura en minutos desde cero.

**11. CI/CD**

Pipeline GitHub Actions:
- Tests en PR
- Build de imagen Docker en merge a main
- Push a registry privado (ghcr.io o ECR)
- Deploy automático vía SSH o Watchtower

**12. Stack de observabilidad**

Para producción con múltiples clientes:
- **Prometheus** + exporters para métricas de sistema y aplicación
- **Grafana Loki** para logs centralizados (reemplaza `docker logs`)
- **Alertmanager** para alertas de infraestructura (disco lleno, CPU alta, container caído)

Esto es independiente del sistema de alertas operativas de KAIROX (que monitorea el proceso de osmosis).

**13. Escalabilidad horizontal**

Si se suman muchos devices (>50), el modelo de un solo proceso Python se convierte en un cuello de botella. La migración natural es:
- **Redis** como message bus interno entre workers
- **Celery** o workers separados por tenant
- **TimescaleDB** para compresión y queries eficientes sobre series temporales de alta densidad

---

*KAIROX Operations Runbook v1.1 — 2026-05-29*  
*Servidor: 159.112.132.176 — Proyecto: /home/ubuntu/iot-server*
