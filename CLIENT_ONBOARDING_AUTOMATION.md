# KAIROX — Client Onboarding Automation Design

**Estado:** Propuesta de diseño — pendiente de implementación  
**Versión:** 1.0  
**Fecha:** 2026-05-29

> Este documento describe el diseño del script `create_client.sh`. No contiene implementación. El objetivo es que cualquier miembro del equipo pueda implementarlo siguiendo este contrato.

---

## Uso esperado

```bash
./scripts/create_client.sh \
  --client    "Aquaser" \
  --device    "ESP32_D0448EC92DF4" \
  --login     "aquaser" \
  --password  "aguasegura" \
  --email     "contacto@aquaser.com"
```

Salida esperada:
```
[ONBOARDING 2026-05-29 10:00:01] === Iniciando onboarding: Aquaser ===
[ONBOARDING 2026-05-29 10:00:01] Paso 1/7: Verificando pre-condiciones...
[ONBOARDING 2026-05-29 10:00:01]   ✓ Container ro-postgres corriendo
[ONBOARDING 2026-05-29 10:00:01]   ✓ Container ro-grafana corriendo
[ONBOARDING 2026-05-29 10:00:01]   ✓ Device ESP32_D0448EC92DF4 existe en DB
[ONBOARDING 2026-05-29 10:00:02] Paso 2/7: Creando organización Grafana...
[ONBOARDING 2026-05-29 10:00:02]   ✓ Org creada: Aquaser (orgId=3)
[ONBOARDING 2026-05-29 10:00:02] Paso 3/7: Creando datasource PostgreSQL...
[ONBOARDING 2026-05-29 10:00:02]   ✓ Datasource creado (uid=cfnj8qajb7f9ca)
[ONBOARDING 2026-05-29 10:00:02] Paso 4/7: Insertando cliente en DB...
[ONBOARDING 2026-05-29 10:00:02]   ✓ Cliente creado (id=2)
[ONBOARDING 2026-05-29 10:00:02] Paso 5/7: Vinculando device al cliente...
[ONBOARDING 2026-05-29 10:00:02]   ✓ ESP32_D0448EC92DF4 → cliente Aquaser
[ONBOARDING 2026-05-29 10:00:03] Paso 6/7: Importando dashboards...
[ONBOARDING 2026-05-29 10:00:03]   ✓ Kairox
[ONBOARDING 2026-05-29 10:00:03]   ✓ KAIROX Detail
[ONBOARDING 2026-05-29 10:00:03]   ✓ KAIROX Overview
[ONBOARDING 2026-05-29 10:00:03] Paso 7/7: Creando usuario Grafana...
[ONBOARDING 2026-05-29 10:00:03]   ✓ Usuario aquaser creado y asignado como Viewer
[ONBOARDING 2026-05-29 10:00:03] === Onboarding completado ===
[ONBOARDING 2026-05-29 10:00:03] URL:      http://159.112.132.176:3000
[ONBOARDING 2026-05-29 10:00:03] Org:      Aquaser (id=3)
[ONBOARDING 2026-05-29 10:00:03] Usuario:  aquaser
[ONBOARDING 2026-05-29 10:00:03] Device:   ESP32_D0448EC92DF4 (Osmosis 01)
```

---

## Inputs requeridos

| Parámetro | Flag | Tipo | Requerido | Descripción |
|---|---|---|---|---|
| Nombre del cliente | `--client` | string | Sí | Nombre de la org en Grafana y en la tabla `clients` |
| Device ID | `--device` | string | Sí | `device_id` existente en la tabla `devices` |
| Login Grafana | `--login` | string | Sí | Username para el usuario Grafana |
| Password Grafana | `--password` | string | Sí | Password del usuario Grafana |
| Email | `--email` | string | Sí | Email del cliente (DB + Grafana) |
| Display name (opcional) | `--display-name` | string | No | Si se omite, usa `--client` |
| Dry-run | `--dry-run` | flag | No | Mostrar qué haría sin ejecutar nada |

---

## Outputs esperados

Al finalizar exitosamente, el script debe imprimir un resumen en formato legible:

```
=== RESUMEN DE ONBOARDING ===
Cliente:          Aquaser
Grafana Org ID:   3
DB Client ID:     2
Datasource UID:   cfnj8qajb7f9ca
Devices:          ESP32_D0448EC92DF4 (Osmosis 01)
Usuario Grafana:  aquaser
URL acceso:       http://159.112.132.176:3000
=============================
```

Y opcionalmente escribir un log en `/home/ubuntu/logs/onboarding.log`.

---

## Pasos internos del script

### Paso 1 — Pre-condiciones

Antes de ejecutar cualquier cambio, verificar:

```bash
# 1a. Containers necesarios están corriendo
docker inspect ro-postgres --format '{{.State.Running}}' | grep -q true
docker inspect ro-grafana  --format '{{.State.Running}}' | grep -q true

# 1b. Grafana responde
curl -s -u admin:$GRAFANA_PASS http://localhost:3000/api/health | grep -q '"database":"ok"'

# 1c. El device existe en DB
docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -t -c \
  "SELECT 1 FROM devices WHERE device_id = '$DEVICE_ID';" | grep -q 1

# 1d. El cliente no existe ya (idempotencia: si existe, re-usar)
EXISTING_CLIENT=$(docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -t -c \
  "SELECT id FROM clients WHERE name = '$CLIENT_NAME';" | tr -d ' \n')
```

Si cualquier pre-condición falla → abortar con mensaje claro antes de modificar nada.

### Paso 2 — Crear organización Grafana

```bash
RESPONSE=$(curl -s -u admin:$GRAFANA_PASS \
  -X POST http://localhost:3000/api/orgs \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$CLIENT_NAME\"}")
ORG_ID=$(echo $RESPONSE | python3 -c "import json,sys; print(json.load(sys.stdin)['orgId'])")
```

**Idempotencia:** Si la org ya existe, obtener su ID con:
```bash
ORG_ID=$(curl -s -u admin:$GRAFANA_PASS \
  "http://localhost:3000/api/orgs/name/$CLIENT_NAME" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
```

### Paso 3 — Crear datasource en la nueva org

```bash
DS_RESPONSE=$(curl -s -u admin:$GRAFANA_PASS \
  -H "X-Grafana-Org-Id: $ORG_ID" \
  -X POST http://localhost:3000/api/datasources \
  -H "Content-Type: application/json" \
  -d '{ ... }')
DS_UID=$(echo $DS_RESPONSE | python3 -c "import json,sys; print(json.load(sys.stdin)['datasource']['uid'])")
```

**Idempotencia:** Verificar si ya existe datasource en la org antes de crear.

### Paso 4 — Insertar cliente en DB

```bash
CLIENT_ID=$(docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -t -c \
  "INSERT INTO clients (name, grafana_org_id, email)
   VALUES ('$CLIENT_NAME', $ORG_ID, '$EMAIL')
   ON CONFLICT (name) DO UPDATE SET grafana_org_id=EXCLUDED.grafana_org_id
   RETURNING id;" | tr -d ' \n')
```

**Idempotencia:** `ON CONFLICT (name) DO UPDATE` garantiza que re-ejecutar no crea duplicados.  
Nota: requiere `UNIQUE` constraint en `clients.name`. Si no existe, verificar primero.

### Paso 5 — Vincular device al cliente

```bash
docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -c \
  "UPDATE devices SET client_id = $CLIENT_ID WHERE device_id = '$DEVICE_ID';"
```

**Idempotencia:** `UPDATE` es idempotente si el valor ya es correcto.

### Paso 6 — Importar dashboards

```bash
MAIN_UID=$(curl -s -u admin:$GRAFANA_PASS -H "X-Grafana-Org-Id: 1" \
  http://localhost:3000/api/datasources | \
  python3 -c "import json,sys; print(json.load(sys.stdin)[0]['uid'])")

for f in /home/ubuntu/iot-server/dashboards/dashboards/*.json; do
  python3 -c "
import json
with open('$f') as fp: content = fp.read()
content = content.replace('$MAIN_UID', '$DS_UID')
dash = json.loads(content)
dash.pop('id', None); dash['uid'] = None
print(json.dumps({'dashboard': dash, 'overwrite': True, 'folderId': 0}))
" | curl -s -u admin:$GRAFANA_PASS \
    -H "Content-Type: application/json" \
    -H "X-Grafana-Org-Id: $ORG_ID" \
    -X POST http://localhost:3000/api/dashboards/import \
    --data-binary @-
done
```

**Idempotencia:** `overwrite: true` garantiza que reimportar no crea duplicados.

### Paso 7 — Crear usuario Grafana

```bash
# Crear usuario
USER_RESPONSE=$(curl -s -u admin:$GRAFANA_PASS \
  -X POST http://localhost:3000/api/admin/users \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$CLIENT_NAME\",\"email\":\"$EMAIL\",\"login\":\"$LOGIN\",\"password\":\"$PASSWORD\",\"OrgId\":$ORG_ID}")
USER_ID=$(echo $USER_RESPONSE | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")

# Asignar como Viewer
curl -s -u admin:$GRAFANA_PASS \
  -X POST http://localhost:3000/api/orgs/$ORG_ID/users \
  -H "Content-Type: application/json" \
  -d "{\"loginOrEmail\":\"$LOGIN\",\"role\":\"Viewer\"}"
```

**Idempotencia:** Verificar si el usuario ya existe antes de crear.

---

## Estrategia de idempotencia

El script debe poder ejecutarse múltiples veces con el mismo input sin crear duplicados ni errores.

| Recurso | Check antes de crear | Acción si ya existe |
|---|---|---|
| Grafana org | `GET /api/orgs/name/{name}` | Usar ID existente, continuar |
| Grafana datasource | `GET /api/datasources` en esa org | Skip creación, usar UID existente |
| DB client | `SELECT id FROM clients WHERE name=?` | Usar ID existente (UPDATE org_id si cambió) |
| device.client_id | Siempre UPDATE (idempotente) | — |
| Grafana dashboards | Import con `overwrite: true` | Sobreescribir |
| Grafana user | `GET /api/users/lookup?loginOrEmail=?` | Skip creación si existe |

---

## Manejo de errores y rollback

El script debe implementar un patrón de rollback parcial:

```bash
# Variables de estado para rollback
ORG_CREATED=false
DS_CREATED=false
CLIENT_CREATED=false
DEVICE_LINKED=false

cleanup_on_failure() {
  log "ERROR en paso $CURRENT_STEP — iniciando rollback..."

  if $DEVICE_LINKED; then
    log "  Rollback: desvinculando device..."
    docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -c \
      "UPDATE devices SET client_id = NULL WHERE device_id = '$DEVICE_ID' AND client_id = $CLIENT_ID;"
  fi

  if $CLIENT_CREATED; then
    log "  Rollback: eliminando cliente de DB..."
    docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -c \
      "DELETE FROM clients WHERE id = $CLIENT_ID;"
  fi

  if $ORG_CREATED; then
    log "  Rollback: eliminando org de Grafana..."
    curl -s -u admin:$GRAFANA_PASS -X DELETE http://localhost:3000/api/orgs/$ORG_ID
  fi

  log "Rollback completado. Sin cambios persistentes."
  exit 1
}

trap cleanup_on_failure ERR
```

**Limitación conocida:** Si el script falla a mitad del paso 7 (usuario ya creado pero no asignado), el rollback elimina la org pero el usuario puede quedar huérfano en Grafana. Limpiar manualmente con:
```bash
curl -s -u admin:admin123 -X DELETE http://localhost:3000/api/admin/users/$USER_ID
```

---

## Validación post-ejecución

Al finalizar, el script ejecuta automáticamente:

```bash
# 1. Device aparece en el selector de la org nueva
docker exec ro-postgres psql -U $DB_USER -d $DB_NAME -t -c "
SELECT d.device_id, d.display_name
FROM devices d LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE AND c.grafana_org_id = $ORG_ID;" | grep -q "$DEVICE_ID"

# 2. Datasource responde (test via Grafana API)
curl -s -u admin:$GRAFANA_PASS \
  -H "X-Grafana-Org-Id: $ORG_ID" \
  http://localhost:3000/api/datasources | python3 -c \
  "import json,sys; print('OK' if json.load(sys.stdin) else 'FAIL')"

# 3. Dashboards importados
DASH_COUNT=$(curl -s -u admin:$GRAFANA_PASS \
  -H "X-Grafana-Org-Id: $ORG_ID" \
  "http://localhost:3000/api/search?type=dash-db" | \
  python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
[ "$DASH_COUNT" -ge 3 ] || log "WARNING: Solo $DASH_COUNT dashboards (esperados 3)"
```

---

## Ubicación del script

```
/home/ubuntu/iot-server/scripts/create_client.sh
```

---

## Variables de entorno requeridas por el script

El script leerá estas variables del entorno o del archivo `.env`:

```bash
GRAFANA_URL=http://localhost:3000
GRAFANA_USER=admin
GRAFANA_PASS=<from env>
DB_USER=user
DB_PASS=<from env>
DB_NAME=iot_db
DASHBOARD_DIR=/home/ubuntu/iot-server/dashboards/dashboards
LOG_DIR=/home/ubuntu/logs
```

---

## Casos borde documentados

| Caso | Comportamiento esperado |
|---|---|
| Cliente ya existe en DB | Re-usa el ID existente, no falla |
| Org ya existe en Grafana | Re-usa el orgId, no falla |
| Device no existe en DB | Falla en paso 1 con mensaje claro |
| Device ya tiene client_id | Advierte y procede (UPDATE) |
| Login Grafana ya existe | Falla en paso 7, hace rollback de pasos 2-6 |
| Grafana no responde | Falla en paso 1, sin cambios |
| DB no responde | Falla en paso 1, sin cambios |

---

*KAIROX Client Onboarding Automation Design v1.0 — 2026-05-29*
