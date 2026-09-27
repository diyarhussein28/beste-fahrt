#!/usr/bin/env bash
# نسخ احتياطي يومي لقاعدة البيانات — القسم 9.1: "الاحتفاظ 14 يوماً".
# شغّله من cron على الخادم المضيف (وليس داخل docker-compose):
#   0 3 * * * /path/to/fleet-dispatch/ops/backup.sh >> /var/log/fleet-backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/fleet_dispatch_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

docker compose exec -T db pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "${DEST}"
echo "backup written to ${DEST}"

find "${BACKUP_DIR}" -name 'fleet_dispatch_*.sql.gz' -mtime +"${RETENTION_DAYS}" -delete
echo "pruned backups older than ${RETENTION_DAYS} days"
