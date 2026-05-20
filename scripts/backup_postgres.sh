#!/usr/bin/env bash
# =============================================================================
# backup_postgres.sh — KAIROX PostgreSQL daily backup
#
# Flow:
#   pg_dump → temp .sql file → gzip → validate gzip → remove temp
#   → rotate old backups
#
# Usage:
#   ./backup_postgres.sh             # normal run
#   ./backup_postgres.sh --dry-run   # show what would happen, no action
#
# Output: /home/ubuntu/backups/postgres/iot_db_YYYYMMDD_HHMMSS.sql.gz
# Log:    /home/ubuntu/logs/backup.log  (when run via cron)
# =============================================================================

set -euo pipefail

CONTAINER="ro-postgres"
DB_NAME="iot_db"
DB_USER="user"
BACKUP_DIR="/home/ubuntu/backups/postgres"
RETENTION_DAYS=30
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
TEMP_SQL="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql"
FINAL_GZ="${TEMP_SQL}.gz"
DRY_RUN=false

log()  { echo "[BACKUP $(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[BACKUP $(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
         # Clean up temp file if it exists
         [ -f "$TEMP_SQL" ] && rm -f "$TEMP_SQL"
         exit 1; }

for arg in "$@"; do
  [ "$arg" = "--dry-run" ] && DRY_RUN=true
done

# ── Pre-flight ────────────────────────────────────────────────────────────────
log "========================================"
log "Iniciando backup: ${DB_NAME}"
log "Destino: ${FINAL_GZ}"

[ -d "$BACKUP_DIR" ] \
  || fail "Directorio ${BACKUP_DIR} no existe. Ejecutar: mkdir -p ${BACKUP_DIR}"

docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null \
  | grep -q "true" \
  || fail "Container ${CONTAINER} no está corriendo"

if $DRY_RUN; then
  log "DRY RUN — pasos que se ejecutarían:"
  log "  1. pg_dump -U ${DB_USER} -d ${DB_NAME} > ${TEMP_SQL}"
  log "  2. gzip ${TEMP_SQL}  →  ${FINAL_GZ}"
  log "  3. gzip -t ${FINAL_GZ}   (validar integridad)"
  log "  4. rm ${TEMP_SQL}"
  log "  5. find ${BACKUP_DIR} -name '*.sql.gz' -mtime +${RETENTION_DAYS} -delete"
  exit 0
fi

# ── Step 1: pg_dump → temp SQL file ──────────────────────────────────────────
log "Paso 1/4: Ejecutando pg_dump..."
docker exec "$CONTAINER" pg_dump \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-password \
  --format=plain \
  --encoding=UTF8 \
  > "$TEMP_SQL"

# Verify dump is not empty and has expected content
[ -s "$TEMP_SQL" ] || fail "pg_dump generó archivo vacío"
grep -q "PostgreSQL database dump" "$TEMP_SQL" \
  || fail "Archivo SQL no parece un dump válido de PostgreSQL"

SQL_SIZE=$(du -sh "$TEMP_SQL" | cut -f1)
log "Paso 1/4 completado: ${TEMP_SQL} (${SQL_SIZE})"

# ── Step 2: Compress ──────────────────────────────────────────────────────────
log "Paso 2/4: Comprimiendo con gzip..."
gzip "$TEMP_SQL"
# gzip replaces TEMP_SQL with TEMP_SQL.gz (FINAL_GZ)
[ -f "$FINAL_GZ" ] || fail "Archivo comprimido no encontrado: ${FINAL_GZ}"
GZ_SIZE=$(du -sh "$FINAL_GZ" | cut -f1)
log "Paso 2/4 completado: ${FINAL_GZ} (${GZ_SIZE})"

# ── Step 3: Validate gzip ─────────────────────────────────────────────────────
log "Paso 3/4: Validando integridad del gzip..."
gzip -t "$FINAL_GZ" || fail "Validación gzip falló: ${FINAL_GZ}"
log "Paso 3/4 completado: gzip OK"

# temp file was consumed by gzip — nothing to delete

# ── Step 4: Rotate old backups ────────────────────────────────────────────────
log "Paso 4/4: Rotando backups anteriores a ${RETENTION_DAYS} días..."
DELETED=$(find "$BACKUP_DIR" -name "*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
[ "$DELETED" -gt 0 ] \
  && log "Paso 4/4: ${DELETED} backup(s) eliminado(s)" \
  || log "Paso 4/4: Sin backups para rotar"

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL=$(find "$BACKUP_DIR" -name "*.sql.gz" | wc -l)
log "Backup completado correctamente"
log "Archivo: ${FINAL_GZ} (${GZ_SIZE})"
log "Total backups retenidos: ${TOTAL}"
log "========================================"
