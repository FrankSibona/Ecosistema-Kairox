# KAIROX — Recovery Runbook

## Backup automático

El backup corre todos los días a las 03:00 AM.
Ubicación: `/home/ubuntu/backups/postgres/`
Retención: 30 días
Log: `/home/ubuntu/logs/backup.log`

---

## Listar backups disponibles

```bash
ls -lh /home/ubuntu/backups/postgres/
```

---

## Ejecutar backup manual

```bash
bash /home/ubuntu/iot-server/scripts/backup_postgres.sh
```

---

## Verificar integridad de un backup

```bash
gzip -t /home/ubuntu/backups/postgres/<archivo>.sql.gz && echo "OK"
```

---

## Test de restore (sin tocar producción)

Toma el último backup, lo restaura en una DB temporal, verifica tablas y la elimina.

```bash
bash /home/ubuntu/iot-server/scripts/test_restore.sh

# O con un backup específico:
bash /home/ubuntu/iot-server/scripts/test_restore.sh /home/ubuntu/backups/postgres/iot_db_20260519_203539.sql.gz
```

---

## Restaurar en producción

> ⚠️ DESTRUCTIVO E IRREVERSIBLE.
> El procedimiento borra y recrea la base de datos completa.
> Ejecutar **únicamente** ante pérdida de datos confirmada.
> Hacer un backup del estado actual antes de proceder.

### Por qué DROP + CREATE y no restore directo

Restaurar sobre la DB existente (`psql -d iot_db`) no garantiza consistencia:
los datos anteriores quedan mezclados con los del backup si hay conflictos de PK.
El ciclo DROP → CREATE → restore garantiza un estado limpio y reproducible.

```bash
# 0. Backup de seguridad del estado actual (por si el restore falla)
bash /home/ubuntu/iot-server/scripts/backup_postgres.sh

# 1. Detener el backend para evitar escrituras durante el restore
docker stop ro-python-worker

# 2. Seleccionar el backup a restaurar
BACKUP=/home/ubuntu/backups/postgres/iot_db_YYYYMMDD_HHMMSS.sql.gz

# 3. Verificar integridad del backup antes de continuar
gzip -t "$BACKUP" && echo "gzip OK"

# 4. Eliminar la DB y recrearla limpia
docker exec ro-postgres psql -U user -d postgres \
  -c "DROP DATABASE IF EXISTS iot_db;"
docker exec ro-postgres psql -U user -d postgres \
  -c "CREATE DATABASE iot_db OWNER user;"

# 5. Restaurar el backup
gunzip -c "$BACKUP" | docker exec -i ro-postgres psql -U user -d iot_db

# 6. Verificar que las tablas principales existen
docker exec ro-postgres psql -U user -d iot_db \
  -c "\dt" | grep -E "devices|telemetry|device_status"

# 7. Reiniciar el backend
docker start ro-python-worker

# 8. Verificar que el sistema responde
sleep 5
curl -s http://localhost:8080/api/status/ESP32_ECBA88C92DF4 | python3 -m json.tool
```

---

## Verificar que el cron de backup está activo

El script existe pero el cron puede no estar corriendo. Verificar siempre ambas cosas:

```bash
# 1. Verificar que el servicio cron está activo
systemctl status cron

# 2. Verificar que la entrada existe en crontab
crontab -l | grep backup

# Salida esperada:
# 0 3 * * * /home/ubuntu/iot-server/scripts/backup_postgres.sh >> /home/ubuntu/logs/backup.log 2>&1

# 3. Verificar que el log tiene ejecuciones recientes
tail -20 /home/ubuntu/logs/backup.log

# 4. Si el cron no está instalado, instalarlo:
(crontab -l 2>/dev/null | grep -v "backup_postgres.sh"; \
 echo "0 3 * * * /home/ubuntu/iot-server/scripts/backup_postgres.sh >> /home/ubuntu/logs/backup.log 2>&1") \
| crontab -
```

> ⚠️ Si `systemctl status cron` falla, el daemon no está corriendo y los backups automáticos NO están funcionando aunque el crontab esté configurado.

---

## Reiniciar todos los containers

Este servidor usa **Compose v1 legacy** (`docker-compose`).
Si en el futuro se migra a Docker Engine con plugin v2 nativo, usar `docker compose` (sin guión).

```bash
cd /home/ubuntu/iot-server

# Compose v1 (instalado actualmente en este servidor)
docker-compose up -d

# Compose v2 (plugin nativo, si estuviera disponible)
# docker compose up -d

# Para verificar cuál está disponible:
docker-compose version 2>/dev/null || echo "v1 no disponible"
docker compose version 2>/dev/null || echo "v2 no disponible"
```

## Reiniciar un container específico

```bash
docker restart ro-python-worker
docker restart ro-grafana
docker restart ro-nginx
docker restart ro-mosquitto
docker restart ro-postgres
```

---

## Verificar estado de containers

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Todos deben estar `Up`. Si alguno está `Restarting`, ver logs:

```bash
docker logs <nombre_container> --tail 30
```

---

## Ver logs en tiempo real

```bash
# Backend Flask
docker logs ro-python-worker -f

# MQTT Mosquitto
docker logs ro-mosquitto -f

# Nginx Proxy Manager
docker logs ro-nginx -f

# Log de backups
tail -f /home/ubuntu/logs/backup.log
```

---

## Limitaciones actuales del disaster recovery

Esta sección documenta explícitamente lo que **no** está protegido.
Es aceptable para un piloto controlado, pero debe resolverse antes de escalar a producción madura.

### Los backups están en el mismo VPS

Los archivos `.sql.gz` se guardan en `/home/ubuntu/backups/postgres/` — en el mismo servidor
donde corre la base de datos. Una falla catastrófica del VPS (disco corrupto, pérdida del
servidor, borrado accidental) implica la **pérdida simultánea de la DB y de todos los backups**.

**No existe backup remoto todavía.**

### Consecuencias de pérdida total del servidor

Si el VPS se pierde completamente:
- Se pierde toda la telemetría histórica
- Se pierden los dashboards de Grafana y su configuración
- Se pierden las Organizations y usuarios de Grafana
- Se pierde la configuración de Nginx Proxy Manager
- El firmware de los ESP32 continúa funcionando (es independiente)
- Los dispositivos se reconectan solos al nuevo servidor con la misma configuración de firmware

### Qué NO está implementado aún

- Backup remoto (S3, rsync a otro servidor, etc.)
- Alertas si el backup falla
- Backup de la configuración de Grafana (DB SQLite)
- Backup de los certificados SSL de Let's Encrypt
- Backup de la configuración de Nginx Proxy Manager

### Aceptabilidad para piloto

Para la etapa piloto actual, con un único cliente y datos de campo de baja criticidad,
esta limitación es aceptable. Antes de incorporar clientes productivos o datos regulatorios,
se debe implementar al menos un backup remoto diario (rsync + servidor secundario o almacenamiento
de objetos).

---

## Riesgos conocidos

| Riesgo | Impacto | Mitigación actual |
|--------|---------|-------------------|
| Pérdida total del VPS implica pérdida de DB y backups | CRÍTICO | Ninguna — ver sección Limitaciones |
| Restore sin DROP/CREATE deja estado inconsistente | CRÍTICO | Procedimiento corregido con ciclo DROP→CREATE→restore |
| Cron no está corriendo → backups automáticos inactivos | ALTO | Verificar con `systemctl status cron` + `crontab -l` |
| Disco lleno: ~109MB/backup × 30 = ~3.3GB mínimo | MEDIO | `df -h /home/ubuntu/backups` antes de cada operación |
| DB temporal de test_restore queda si SIGKILL abrupto | BAJO | `trap cleanup EXIT` garantiza cleanup en condiciones normales |
