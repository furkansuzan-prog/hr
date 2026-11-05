#!/usr/bin/env bash
# HRSWI restore drill: verify checksum -> restore the dump into a FRESH temporary database -> compare exact row
# counts of every table with the backup manifest (snapshot-consistent source counts) and, with --live, with the
# current source database -> check alembic revision -> drop the temporary database (always, via trap).
#
# Usage:  scripts/restore_verify.sh DUMP_FILE [--url SOURCE_URL] [--jobs N] [--live] [--keep]
#   SOURCE_URL defaults to $BACKUP_DATABASE_URL, then $HRSWI_DATABASE_URL; it names the server to restore on
#   (the temp DB is created next to it) and, with --live, the database to compare against.
#   --keep leaves the temporary database in place (for inspection); default is to drop it.
# Exit code 0 only if checksum and all row counts match.
set -euo pipefail
export LC_ALL=C  # sort/join must agree on collation

DUMP="${1:?usage: restore_verify.sh DUMP_FILE [--url URL] [--jobs N] [--live] [--keep]}"; shift
URL="${BACKUP_DATABASE_URL:-${HRSWI_DATABASE_URL:-}}"
JOBS=2; LIVE=0; KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --live) LIVE=1; shift ;;
    --keep) KEEP=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$URL" ]] || { echo "set --url, BACKUP_DATABASE_URL or HRSWI_DATABASE_URL" >&2; exit 2; }
URL="${URL/postgresql+psycopg:/postgresql:}"
MANIFEST="${DUMP%.dump}.manifest"
SRC_DB="$(sed -E 's#^.*/([^/?]+)(\?.*)?$#\1#' <<<"$URL")"
TMP_DB="hrswi_restore_$(date -u +%Y%m%d%H%M%S)_$$"
ADMIN_URL="$(sed -E "s#/${SRC_DB}(\?|\$)#/postgres\1#" <<<"$URL")"
TMP_URL="$(sed -E "s#/${SRC_DB}(\?|\$)#/${TMP_DB}\1#" <<<"$URL")"
now() { date +%s.%N; }
secs() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
COUNT_SQL="SELECT table_name || E'\t' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
  table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"

WORK="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK"
  if [[ "$KEEP" == 0 ]]; then
    psql "$ADMIN_URL" -X -q -c "DROP DATABASE IF EXISTS \"$TMP_DB\" WITH (FORCE)" >/dev/null && echo "dropped temp db $TMP_DB"
  else
    echo "kept temp db $TMP_DB"
  fi
}

trap 'rm -rf "$WORK"' EXIT
T0=$(now)
echo "== 1/4 checksum"
( cd "$(dirname "$DUMP")" && sha256sum --check --status "$(basename "$DUMP").sha256" ) \
  || { echo "CHECKSUM MISMATCH for $DUMP" >&2; exit 1; }
echo "checksum ok ($(stat -c %s "$DUMP") bytes)"
T1=$(now)

echo "== 2/4 restore into $TMP_DB (jobs=$JOBS)"
psql "$ADMIN_URL" -X -q -c "CREATE DATABASE \"$TMP_DB\"" >/dev/null
trap cleanup EXIT
pg_restore --no-owner --exit-on-error --jobs="$JOBS" --dbname="$TMP_URL" "$DUMP"
psql "$TMP_URL" -X -q -c "ANALYZE" >/dev/null
T2=$(now)

echo "== 3/4 row counts"
psql "$TMP_URL" -X -A -t -c "$COUNT_SQL" | sort > "$WORK/restored"
FAIL=0
compare() {  # $1 = label, $2 = reference file
  local diffs
  diffs=$(join -t $'\t' -a1 -a2 -e MISSING -o 0,1.2,2.2 "$2" "$WORK/restored" | awk -F'\t' '$2 != $3')
  if [[ -n "$diffs" ]]; then
    echo "MISMATCH vs $1 (table / expected / restored):"; echo "$diffs"; FAIL=1
  else
    echo "row counts match $1: $(wc -l < "$2") tables, $(awk -F'\t' '{s+=$2} END{print s}' "$2") rows"
  fi
}
if [[ -f "$MANIFEST" ]]; then
  grep -v '^#' "$MANIFEST" | sort > "$WORK/manifest"
  compare "backup manifest (source snapshot)" "$WORK/manifest"
else
  echo "no manifest next to dump; use --live to compare with the source database"
fi
if [[ "$LIVE" == 1 ]]; then
  psql "$URL" -X -A -t -c "$COUNT_SQL" | sort > "$WORK/live"
  compare "live source $SRC_DB" "$WORK/live"
fi
REV_SRC=$(psql "$URL" -X -A -t -c "SELECT version_num FROM alembic_version" 2>/dev/null || echo "?")
REV_TMP=$(psql "$TMP_URL" -X -A -t -c "SELECT version_num FROM alembic_version")
echo "alembic revision: restored=$REV_TMP source=$REV_SRC"
T3=$(now)

echo "== 4/4 summary"
echo "timings: checksum=$(secs "$T0" "$T1")s restore+analyze=$(secs "$T1" "$T2")s verify=$(secs "$T2" "$T3")s total=$(secs "$T0" "$T3")s"
echo "restored db size: $(psql "$TMP_URL" -X -A -t -c "SELECT pg_size_pretty(pg_database_size(current_database()))")"
if [[ "$FAIL" == 0 ]]; then echo "RESTORE VERIFY: PASS"; else echo "RESTORE VERIFY: FAIL"; exit 1; fi
