# KAIROX — Disaster Recovery Guide

**Sistema:** KAIROX IoT Platform  
**Versión:** 1.0  
**Fecha:** 2026-05-29  
**Objetivo:** Reconstruir la plataforma completa desde cero en un VPS Ubuntu vacío.

> Este documento asume pérdida total del servidor. Al finalizar, la plataforma debe estar 100% operativa con todos sus datos restaurados.

---

## Prerequisitos

| Requisito | Detalle |
|---|---|
| VPS | Ubuntu 22.04 LTS (aarch64 u x86_64) |
| RAM mínima | 2 GB |
| Disco mínimo | 20 GB |
| Acceso | SSH como `ubuntu` o usuario con sudo |
| Backup disponible | Archivo `.sql.gz` del backup PostgreSQL |
| Acceso al repo | SSH key configurada para GitHub o HTTPS token |

---

## Fase 1 — Bootstrap del servidor

### 1.1 Actualizar sistema e instalar paquetes base
```bash
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y \
  curl wget git unzip gnupg ca-certificates \
  lsb-release apt-transport-https \
  python3 python3-pip python3-venv \
  gzip jq net-tools
```

### 1.2 Instalar Docker Engine
```bash
# Agregar repositorio oficial de Docker
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# Agregar usuario actual al grupo docker (sin sudo)
sudo usermod -aG docker $USER
newgrp docker

# Verificar
docker --version
# Esperado: Docker version 29.x.x
```

### 1.3 Instalar Docker Compose v1 (CLI clásico)
```bash
# IMPORTANTE: el proyecto usa docker-compose (v1), NO "docker compose" (v2)
sudo curl -L \
  "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verificar
docker-compose --version
# Esperado: docker-compose version 1.29.2
```

### 1.4 Configurar firewall (ufw)
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP (Nginx Proxy Manager)
sudo ufw allow 443/tcp     # HTTPS (Nginx Proxy Manager)
sudo ufw allow 3000/tcp    # Grafana
sudo ufw allow 8080/tcp    # Flask API
sudo ufw allow 1883/tcp    # MQTT TCP
sudo ufw allow 9001/tcp    # MQTT WebSocket
sudo ufw allow 5432/tcp    # PostgreSQL (solo si acceso externo requerido)
sudo ufw --force enable
sudo ufw status numbered
```

### 1.5 Crear estructura de directorios
```bash
mkdir -p /home/ubuntu/backups/postgres
mkdir -p /home/ubuntu/logs
```

---

## Fase 2 — Restore del proyecto

### 2.1 Clonar repositorio
```bash
cd /home/ubuntu
git clone git@github.com:FrankSibona/Ecosistema-Kairox.git iot-server
cd /home/ubuntu/iot-server
```

Si no hay acceso SSH configurado, usar HTTPS:
```bash
git clone https://github.com/FrankSibona/Ecosistema-Kairox.git iot-server
```

Verificar contenido:
```bash
ls /home/ubuntu/iot-server
# Esperado: docker-compose.yml  python_iot/  dashboards/  mosquitto/  schema.sql  scripts/
```

### 2.2 Restaurar archivo .env (secretos)
El archivo `.env` NO está en el repositorio. Debe restaurarse desde backup seguro o recrearse manualmente.

Copiar desde backup:
```bash
cp /ruta/backup/.env /home/ubuntu/iot-server/.env
chmod 600 /home/ubuntu/iot-server/.env
```

O crear nuevo desde `.env.example`:
```bash
cp /home/ubuntu/iot-server/.env.example /home/ubuntu/iot-server/.env
# Editar con valores reales:
nano /home/ubuntu/iot-server/.env
```

### 2.3 Verificar estructura crítica
```bash
# Estos archivos deben existir:
ls -la /home/ubuntu/iot-server/python_iot/app.py
ls -la /home/ubuntu/iot-server/python_iot/alert_config.py
ls -la /home/ubuntu/iot-server/python_iot/ai_client.py
ls -la /home/ubuntu/iot-server/python_iot/requirements.txt
ls -la /home/ubuntu/iot-server/docker-compose.yml
ls -la /home/ubuntu/iot-server/schema.sql
ls -la /home/ubuntu/iot-server/mosquitto/config/mosquitto.conf
ls -la /home/ubuntu/iot-server/dashboards/dashboards/
```

---

## Fase 3 — Levantar contenedores

### 3.1 Levantar todos los servicios
```bash
cd /home/ubuntu/iot-server
docker-compose up -d
```

Esperar 15 segundos y verificar:
```bash
sleep 15
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Todos deben mostrar `Up`:
```
NAMES              STATUS          PORTS
ro-python-worker   Up X seconds    0.0.0.0:8080->8080/tcp
ro-postgres        Up X seconds    0.0.0.0:5432->5432/tcp
ro-mosquitto       Up X seconds    0.0.0.0:1883->1883/tcp, 0.0.0.0:9001->9001/tcp
ro-grafana         Up X seconds    0.0.0.0:3000->3000/tcp
ro-nginx           Up X seconds    0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
```

### 3.2 Verificar restart policies
```bash
docker inspect ro-python-worker --format '{{.HostConfig.RestartPolicy.Name}}'
docker inspect ro-postgres       --format '{{.HostConfig.RestartPolicy.Name}}'
docker inspect ro-grafana        --format '{{.HostConfig.RestartPolicy.Name}}'
# Todos deben mostrar: always
```

### 3.3 Verificar puertos accesibles
```bash
curl -s http://localhost:8080/api/status/nonexistent | head -1
# Esperado: respuesta JSON (incluso si es 404)

curl -s -u admin:admin123 http://localhost:3000/api/health
# Esperado: {"commit":"...","database":"ok",...}
```

---

## Fase 4 — Restore de PostgreSQL

> **IMPORTANTE:** Hacer esto ANTES de que el backend reciba telemetría real, para evitar conflictos con datos frescos.

### 4.1 Detener backend temporalmente
```bash
docker stop ro-python-worker
```

### 4.2 Aplicar schema base
```bash
docker exec -i ro-postgres psql -U user -d iot_db < /home/ubuntu/iot-server/schema.sql
```

Verificar que no hay errores:
```bash
docker exec ro-postgres psql -U user -d iot_db -c "\dt" | wc -l
# Esperado: ~20 líneas (18 tablas + encabezados)
```

### 4.3 Restaurar backup de datos
```bash
# Listar backups disponibles
ls -lht /home/ubuntu/backups/postgres/*.sql.gz 2>/dev/null

# Restaurar el más reciente (o el específico deseado)
BACKUP_FILE=$(ls -t /home/ubuntu/backups/postgres/*.sql.gz | head -1)
echo "Restaurando: $BACKUP_FILE"

# PRECAUCIÓN: esto sobrescribe todos los datos actuales
gunzip -c "$BACKUP_FILE" | docker exec -i ro-postgres psql -U user -d iot_db -q
```

### 4.4 Verificar restore
```bash
# Verificar tablas
docker exec ro-postgres psql -U user -d iot_db -c "\dt"

# Verificar devices
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, display_name, client_id, enabled FROM devices;"

# Verificar clientes
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT id, name, grafana_org_id FROM clients ORDER BY id;"

# Verificar última telemetría
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT device_id, MAX(time) AS ultima_telemetria FROM telemetry_process GROUP BY device_id;"

# Verificar alertas activas
docker exec ro-postgres psql -U user -d iot_db -c \
  "SELECT code, severity, active, device_id FROM alerts WHERE active = TRUE;"
```

### 4.5 Aplicar migraciones pendientes
```bash
# La migración notification_count se aplica automáticamente al iniciar el backend
# Pero verificar manualmente:
docker exec ro-postgres psql -U user -d iot_db -c \
  "\d alerts" | grep notification_count
# Esperado: una línea con notification_count
```

### 4.6 Reiniciar backend
```bash
docker start ro-python-worker
sleep 5
docker logs ro-python-worker --tail 20
# Esperado: "✅ Schema migrations aplicadas", "✅ MQTT conectado → fyntek/#"
```

---

## Fase 5 — Restore de Grafana

Grafana almacena su estado en el volumen `iot-server_grafana_data`. Si el volumen se perdió, hay que recrear manualmente orgs, datasources, dashboards y usuarios.

### 5.1 Verificar que Grafana está accesible
```bash
curl -s -u admin:admin123 http://localhost:3000/api/health
```

### 5.2 Recrear organizaciones

**LABORATORIO (org 2):**
```bash
curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs \
  -H "Content-Type: application/json" \
  -d '{"name": "LABORATORIO"}'
# → anotar orgId (debería ser 2)
```

**Aquaser (org 3):**
```bash
curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs \
  -H "Content-Type: application/json" \
  -d '{"name": "Aquaser"}'
# → anotar orgId (debería ser 3)
```

Verificar:
```bash
curl -s -u admin:admin123 http://localhost:3000/api/orgs | python3 -m json.tool
```

### 5.3 Recrear datasources en cada org

Ejecutar para cada org (reemplazar ORG_ID con 1, 2 y 3):
```bash
for ORG_ID in 1 2 3; do
  echo "Creando datasource en org $ORG_ID..."
  curl -s -u admin:admin123 \
    -H "X-Grafana-Org-Id: $ORG_ID" \
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
    }' | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Org {$ORG_ID} → uid={d[\"datasource\"][\"uid\"]}')"
done
```

Anotar los UIDs generados — son necesarios para importar dashboards.

### 5.4 Obtener UIDs de datasource recién creados
```bash
for ORG_ID in 1 2 3; do
  UID=$(curl -s -u admin:admin123 \
    -H "X-Grafana-Org-Id: $ORG_ID" \
    http://localhost:3000/api/datasources | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['uid'] if d else 'NONE')")
  echo "Org $ORG_ID → uid=$UID"
done
```

### 5.5 Importar dashboards

Los dashboards JSON tienen hardcodeado el UID del datasource de Main Org. Para cada org, reemplazarlo con el UID correcto.

```bash
# UID de Main Org (hardcodeado en los JSON exportados):
MAIN_UID=$(curl -s -u admin:admin123 -H "X-Grafana-Org-Id: 1" \
  http://localhost:3000/api/datasources | \
  python3 -c "import json,sys; print(json.load(sys.stdin)[0]['uid'])")

echo "Main Org datasource UID: $MAIN_UID"

# Importar en cada org
for ORG_ID in 1 2 3; do
  NEW_UID=$(curl -s -u admin:admin123 \
    -H "X-Grafana-Org-Id: $ORG_ID" \
    http://localhost:3000/api/datasources | \
    python3 -c "import json,sys; print(json.load(sys.stdin)[0]['uid'])")

  echo "=== Importando en org $ORG_ID (uid=$NEW_UID) ==="
  for f in /home/ubuntu/iot-server/dashboards/dashboards/*.json; do
    python3 -c "
import json
with open('$f') as fp:
    content = fp.read()
content = content.replace('$MAIN_UID', '$NEW_UID')
dash = json.loads(content)
dash.pop('id', None)
dash['uid'] = None
print(json.dumps({'dashboard': dash, 'overwrite': True, 'folderId': 0}))
" | curl -s -u admin:admin123 \
      -H "Content-Type: application/json" \
      -H "X-Grafana-Org-Id: $ORG_ID" \
      -X POST http://localhost:3000/api/dashboards/import \
      --data-binary @- | \
      python3 -c "import json,sys; d=json.load(sys.stdin); print('  ✓', d.get('title','?'))"
  done
done
```

### 5.6 Recrear usuarios Grafana

**Usuario LABORATORIO:**
```bash
# Obtener orgId de LABORATORIO
LAB_ORG=$(curl -s -u admin:admin123 http://localhost:3000/api/orgs | \
  python3 -c "import json,sys; orgs=json.load(sys.stdin); print([o['id'] for o in orgs if o['name']=='LABORATORIO'][0])")

curl -s -u admin:admin123 -X POST http://localhost:3000/api/admin/users \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"LABORATORIO\",\"email\":\"admin@laboratorio.com\",\"login\":\"laboratorio\",\"password\":\"<CONTRASEÑA>\",\"OrgId\":$LAB_ORG}"

curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs/$LAB_ORG/users \
  -H "Content-Type: application/json" \
  -d '{"loginOrEmail":"laboratorio","role":"Viewer"}'
```

**Usuario Aquaser:**
```bash
AQ_ORG=$(curl -s -u admin:admin123 http://localhost:3000/api/orgs | \
  python3 -c "import json,sys; orgs=json.load(sys.stdin); print([o['id'] for o in orgs if o['name']=='Aquaser'][0])")

curl -s -u admin:admin123 -X POST http://localhost:3000/api/admin/users \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Aquaser\",\"email\":\"aquaser@aquaser.com\",\"login\":\"aquaser\",\"password\":\"aguasegura\",\"OrgId\":$AQ_ORG}"

curl -s -u admin:admin123 -X POST http://localhost:3000/api/orgs/$AQ_ORG/users \
  -H "Content-Type: application/json" \
  -d '{"loginOrEmail":"aquaser","role":"Viewer"}'
```

### 5.7 Validar filtrado multi-tenant
```bash
# Simulación: Aquaser (org 3) debe ver solo Osmosis 01
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE AND (3 = 1 OR c.grafana_org_id = 3);"

# Simulación: LABORATORIO (org 2) debe ver solo LAB - EQUIPO 1
docker exec ro-postgres psql -U user -d iot_db -c "
SELECT d.device_id, d.display_name
FROM devices d
LEFT JOIN clients c ON d.client_id = c.id
WHERE d.enabled = TRUE AND (2 = 1 OR c.grafana_org_id = 2);"
```

---

## Fase 6 — Verify Mosquitto

### 6.1 Verificar config
```bash
cat /home/ubuntu/iot-server/mosquitto/config/mosquitto.conf
# Esperado: allow_anonymous true, listener 1883
```

### 6.2 Test de pub/sub
```bash
# Terminal 1 — suscribirse
docker exec ro-mosquitto mosquitto_sub -h localhost -t "test/recovery" -C 1 -W 5 &
# Terminal 2 — publicar
docker exec ro-mosquitto mosquitto_pub -h localhost -t "test/recovery" -m "OK"
# Debe imprimir: OK
```

### 6.3 Verificar telemetría (una vez conectado un device)
```bash
docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/#" -v -C 5 -W 15
# Debe aparecer JSON de telemetría si hay un device activo
```

---

## Fase 7 — Verificar scripts de backup

```bash
# Crear directorios si no existen
mkdir -p /home/ubuntu/backups/postgres
mkdir -p /home/ubuntu/logs

# Dar permisos de ejecución
chmod +x /home/ubuntu/iot-server/scripts/backup_postgres.sh
chmod +x /home/ubuntu/iot-server/scripts/test_restore.sh

# Dry-run
/home/ubuntu/iot-server/scripts/backup_postgres.sh --dry-run

# Backup real de prueba
/home/ubuntu/iot-server/scripts/backup_postgres.sh

# Verificar backup creado
ls -lh /home/ubuntu/backups/postgres/

# Test de restore
/home/ubuntu/iot-server/scripts/test_restore.sh
```

---

## Checklist de verificación post-recovery

Ejecutar en orden. Cada ítem debe pasar antes de continuar.

```
[ ] 1. DOCKER — Todos los contenedores en estado "Up"
        docker ps --format "table {{.Names}}\t{{.Status}}"

[ ] 2. POSTGRESQL — Conexión y tablas
        docker exec ro-postgres psql -U user -d iot_db -c "\dt" | wc -l
        # Esperado: ~20 líneas

[ ] 3. POSTGRESQL — Devices y clientes presentes
        docker exec ro-postgres psql -U user -d iot_db -c \
          "SELECT device_id, display_name, client_id FROM devices;"

[ ] 4. FLASK API — Respondiendo
        curl -s http://localhost:8080/api/status/ESP32_D0448EC92DF4 | python3 -m json.tool
        # Esperado: JSON con state, online, seconds_since_seen

[ ] 5. GRAFANA — Health OK
        curl -s -u admin:admin123 http://localhost:3000/api/health | grep '"database":"ok"'

[ ] 6. GRAFANA — Orgs presentes
        curl -s -u admin:admin123 http://localhost:3000/api/orgs | python3 -m json.tool
        # Esperado: Main Org (1), LABORATORIO (2), Aquaser (3)

[ ] 7. GRAFANA — Datasources en cada org
        for i in 1 2 3; do
          echo "Org $i:"; curl -s -u admin:admin123 \
            -H "X-Grafana-Org-Id: $i" http://localhost:3000/api/datasources | \
            python3 -c "import json,sys; d=json.load(sys.stdin); print(' ', d[0]['name'] if d else 'NONE')"
        done

[ ] 8. GRAFANA — Dashboards en Aquaser
        curl -s -u admin:admin123 -H "X-Grafana-Org-Id: 3" \
          "http://localhost:3000/api/search?type=dash-db" | \
          python3 -c "import json,sys; [print(' -',d['title']) for d in json.load(sys.stdin)]"
        # Esperado: Kairox, KAIROX Detail, KAIROX Overview

[ ] 9. MQTT — Broker respondiendo
        docker exec ro-mosquitto mosquitto_pub -h localhost -t "test/ping" -m "ok"
        docker exec ro-mosquitto mosquitto_sub -h localhost -t "test/ping" -C 1 -W 2
        # Esperado: ok

[ ] 10. MQTT — Telemetría llegando (si hay device conectado)
         docker exec ro-mosquitto mosquitto_sub -h localhost -t "fyntek/#" -v -C 3 -W 10

[ ] 11. BACKEND — Sin errores críticos en logs
         docker logs ro-python-worker --tail 50 2>&1 | grep -E "ERROR|CRITICAL" | wc -l
         # Esperado: 0

[ ] 12. ALERTAS — Sistema funcionando
         curl -s "http://localhost:8080/api/alerts/ESP32_D0448EC92DF4?active=false&limit=5" | \
           python3 -m json.tool

[ ] 13. BACKUP — Script funcional
         /home/ubuntu/iot-server/scripts/backup_postgres.sh --dry-run

[ ] 14. MULTI-TENANT — Filtrado correcto
         docker exec ro-postgres psql -U user -d iot_db -c "
         SELECT d.device_id, c.name, c.grafana_org_id
         FROM devices d LEFT JOIN clients c ON d.client_id = c.id
         WHERE d.enabled = TRUE ORDER BY c.grafana_org_id;"

[ ] RECOVERY COMPLETADO ✓
    Plataforma operativa. Documentar fecha/hora y backup utilizado.
```

---

*KAIROX Disaster Recovery Guide v1.0 — 2026-05-29*
