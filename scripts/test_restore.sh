#!/usr/bin/env bash
# =============================================================================
# test_restore.sh — KAIROX PostgreSQL restore verification
#
# Takes the latest backup, restores it into a temporary database,
# verifies key tables exist, then drops the temporary database.
# NEVER touches the production database.
#
# Usage:
#   ./test_restore.sh                      # use latest backup
#   ./test_restore.sh /path/to/backup.sql.gz  # use specific backup
#
# Returns: 0 on success, 1 on failure
# =============================================================================

set -euo pipefail

CONTAINER="ro-postgres"
DB_USER="user"
BACKUP_DIR="/home/ubuntu/backups/postgres"
TEST_DB="test_restore_$$"   # unique name using PID

log()  { echo "[RESTORE-TEST $(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[RESTORE-TEST $(date '+%Y-%m-%d %H:%M:%S')] FAIL: $*" >&2; exit 1; }

# Initialized early so the trap can safely reference them even if the script
# exits before these variables are assigned their real values.
TEMP_SQL=""

cleanup() {
  log "--- Iniciando cleanup ---"
  # Always attempt to drop the temp DB; safe because of IF EXISTS
  docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS ${TEST_DB};" \
    --no-password -q 2>/dev/null || true
  log "DB temporal ${TEST_DB} eliminada (o ya no existía)"
  # Remove temp SQL file if it was created
  if [ -n "$TEMP_SQL" ] && [ -f "$TEMP_SQL" ]; then
    rm -f "$TEMP_SQL"
    log "Temp SQL eliminado: ${TEMP_SQL}"
  fi
  log "Cleanup completado"
}
trap cleanup EXIT

# ── Select backup file ────────────────────────────────────────────────────────
if [ -n "${1:-}" ]; then
  BACKUP_FILE="$1"
else
  BACKUP_FILE=$(find "$BACKUP_DIR" -name "*.sql.gz" -printf '%T@ %p\n' \
    | sort -n | tail -1 | cut -d' ' -f2-)
fi

log "========================================"
log "Test de restore iniciado"
log "Backup: ${BACKUP_FILE}"

[ -f "$BACKUP_FILE" ] || fail "Archivo no encontrado: ${BACKUP_FILE}"

docker inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null \
  | grep -q "true" \
  || fail "Container ${CONTAINER} no está corriendo"

# ── Validate gzip ─────────────────────────────────────────────────────────────
log "Paso 1/4: Validando integridad gzip..."
gzip -t "$BACKUP_FILE" || fail "gzip inválido: ${BACKUP_FILE}"
log "Paso 1/4: gzip OK"

# ── Decompress to temp file ────────────────────────────────────────────────────
# Assign the real path now — cleanup trap will delete it on any exit from here on.
TEMP_SQL="/tmp/restore_test_$$.sql"
log "Paso 2/4: Descomprimiendo..."
gunzip -c "$BACKUP_FILE" > "$TEMP_SQL"
[ -s "$TEMP_SQL" ] || fail "Archivo SQL vacío tras descomprimir"
log "Paso 2/4: Descompresión OK ($(du -sh "$TEMP_SQL" | cut -f1))"

# ── Create temp database ───────────────────────────────────────────────────────
log "Paso 3/4: Creando DB temporal: ${TEST_DB}..."
docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres \
  -c "CREATE DATABASE ${TEST_DB};" \
  --no-password -q \
  || fail "No se pudo crear DB temporal"
log "Paso 3/4: DB temporal creada"

# ── Restore ────────────────────────────────────────────────────────────────────
log "Paso 4/4: Restaurando backup en ${TEST_DB}..."
docker exec -i "$CONTAINER" psql \
  -U "$DB_USER" \
  -d "$TEST_DB" \
  --no-password \
  -q \
  < "$TEMP_SQL" \
  && log "Paso 4/4: Restore completado" \
  || fail "Error durante restore"

# ── Verify key tables ──────────────────────────────────────────────────────────
log "Verificando tablas principales..."
EXPECTED_TABLES=(
  "telemetry_process"
  "telemetry_state"
  "device_status"
  "devices"
  "clients"
  "device_commands"
  "business_metrics"
)

ALL_OK=true
for TABLE in "${EXPECTED_TABLES[@]}"; do
  EXISTS=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" \
    --no-password -t -c \
    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='${TABLE}');" \
    | tr -d ' \n')
  if [ "$EXISTS" = "t" ]; then
    log "  ✓ ${TABLE}"
  else
    log "  ✗ ${TABLE} — NO ENCONTRADA"
    ALL_OK=false
  fi
done

# Verify row counts make sense
PROC_ROWS=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" \
  --no-password -t -c "SELECT COUNT(*) FROM telemetry_process;" | tr -d ' \n')
DEV_ROWS=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$TEST_DB" \
  --no-password -t -c "SELECT COUNT(*) FROM devices;" | tr -d ' \n')
log "  telemetry_process rows: ${PROC_ROWS}"
log "  devices rows: ${DEV_ROWS}"

$ALL_OK || fail "Algunas tablas no fueron restauradas correctamente"

# ── Result ─────────────────────────────────────────────────────────────────────
GZ_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
log "========================================"
log "RESULTADO: RESTORE TEST EXITOSO"
log "Backup verificado: ${BACKUP_FILE} (${GZ_SIZE})"
log "Tablas verificadas: ${#EXPECTED_TABLES[@]}"
log "telemetry_process: ${PROC_ROWS} filas"
log "========================================"
