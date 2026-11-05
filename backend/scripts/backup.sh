#!/usr/bin/env bash
# HRSWI logical backup: pg_dump custom format (-Fc) + SHA-256 checksum + row-count manifest.
#
# The row counts are taken inside the SAME snapshot that pg_dump uses (pg_export_snapshot + --snapshot), so
# restore_verify.sh can compare a restore against the source exactly, even while the application is writing.
#
# Usage:  scripts/backup.sh [--url URL] [--dir DIR] [--retention-days N] [--jobs N]
#   URL defaults to $BACKUP_DATABASE_URL, then $HRSWI_DATABASE_URL (the SQLAlchemy "+psycopg" suffix is stripped).
#   DIR defaults to $BACKUP_DIR or ./backups. Retention (default 14 days, 0 = keep all) deletes older dumps in DIR.
# Output: DIR/<db>_<UTC timestamp>.dump, .dump.sha256, .manifest (tsv: table<TAB>rows; plus meta lines)
# Secrets: pass the password via ~/.pgpass or PGPASSWORD in production, not in the URL on a shared host.
set -euo pipefail

URL="${BACKUP_DATABASE_URL:-${HRSWI_DATABASE_URL:-}}"
DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$URL" ]] || { echo "set --url, BACKUP_DATABASE_URL or HRSWI_DATABASE_URL" >&2; exit 2; }
URL="${URL/postgresql+psycopg:/postgresql:}"
DB="$(sed -E 's#^.*/([^/?]+)(\?.*)?$#\1#' <<<"$URL")"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DIR"
BASE="$DIR/${DB}_${TS}"
DUMP="$BASE.dump"
MANIFEST="$BASE.manifest"

COUNT_SQL="SELECT table_name || E'\t' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
  table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"

# Hold a REPEATABLE READ transaction open in a psql coprocess; export its snapshot for pg_dump.
coproc PSQL { psql "$URL" -X -q -A -t -v ON_ERROR_STOP=1 2>&1; }
trap 'kill "${PSQL_PID:-}" 2>/dev/null || true' EXIT
echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();" >&"${PSQL[1]}"
read -r SNAP <&"${PSQL[0]}"
[[ "$SNAP" =~ ^[0-9A-F-]+$ ]] || { echo "could not export snapshot: $SNAP" >&2; exit 1; }

t0=$(date +%s.%N)
pg_dump --format=custom --snapshot="$SNAP" --no-owner --file="$DUMP" "$URL"
t1=$(date +%s.%N)

{
  echo "# db=$DB snapshot=$SNAP created_utc=$TS pg_dump=$(pg_dump --version | awk '{print $3}')"
  printf '%s\n' "$COUNT_SQL" '\echo __END__' >&"${PSQL[1]}"
  while read -r line <&"${PSQL[0]}"; do
    [[ "$line" == "__END__" ]] && break
    echo "$line"
  done
} > "$MANIFEST"
echo "COMMIT;" >&"${PSQL[1]}"
echo '\q' >&"${PSQL[1]}"
wait "$PSQL_PID" 2>/dev/null || true

( cd "$(dirname "$DUMP")" && sha256sum "$(basename "$DUMP")" > "$(basename "$DUMP").sha256" )
SIZE=$(stat -c %s "$DUMP")
DBSIZE=$(psql "$URL" -X -A -t -c "SELECT pg_database_size(current_database())")
echo "# dump_bytes=$SIZE db_bytes=$DBSIZE dump_seconds=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')" >> "$MANIFEST"

if [[ "$RETENTION_DAYS" -gt 0 ]]; then
  find "$DIR" -maxdepth 1 -name "${DB}_*.dump*" -mtime "+$RETENTION_DAYS" -print -delete
  find "$DIR" -maxdepth 1 -name "${DB}_*.manifest" -mtime "+$RETENTION_DAYS" -print -delete
fi
printf 'backup ok: %s (%s bytes, db %s bytes, %.1f s, %s tables)\n' "$DUMP" "$SIZE" "$DBSIZE" \
  "$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')" "$(grep -vc '^#' "$MANIFEST")"
