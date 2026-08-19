#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: DATABASE_URL=postgresql://... $0 BACKUP.sql.gz" >&2
    exit 2
fi

backup_file=$1
checksum_file="${backup_file}.sha256"

if [ ! -f "$backup_file" ] || [ ! -f "$checksum_file" ]; then
    echo "Backup or checksum file not found" >&2
    exit 2
fi
if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is required (use a postgresql:// URL for psql)" >&2
    exit 2
fi

(cd "$(dirname "$backup_file")" && sha256sum -c "$(basename "$checksum_file")")
echo "Checksum is valid. Restoring will overwrite database objects." >&2
printf "Type RESTORE to continue: " >&2
read -r confirmation
[ "$confirmation" = "RESTORE" ] || exit 1

gzip -dc "$backup_file" | psql "$DATABASE_URL" --set ON_ERROR_STOP=on
echo "Restore completed"
