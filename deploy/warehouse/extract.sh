#!/bin/sh
set -eu

stage="$(mktemp -d)"
trap 'rm -f "$stage/batch.sql"; rmdir "$stage"' EXIT
export PGCONNECT_TIMEOUT=5

extract() {
    service="$1"
    password="$2"
    table="$3"
    host="$service-db"
    port=5432
    if [ -n "${ETL_DB_ROUTER:-}" ]; then
        host="$ETL_DB_ROUTER"
        name="$(printf '%s' "$service" | tr '[:lower:]' '[:upper:]')"
        port="$(printenv "ETL_${name}_PORT")"
    fi
    # All service and table names are fixed below. Only counts cross DB boundaries.
    count="$(PGPASSWORD="$password" PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=15000' \
        psql -X -Aqt -v ON_ERROR_STOP=1 -h "$host" -p "$port" -U "$service" -d "${service}_db" \
        -c "SELECT count(*) FROM public.$table")"
    case "$count" in
        ''|*[!0-9]*) echo "Invalid aggregate from $service" >&2; exit 1 ;;
    esac
    printf "INSERT INTO analytics.snapshots VALUES (transaction_timestamp(), '%s', '%s', %s);\n" \
        "$service" "$table" "$count" >> "$stage/batch.sql"
}

# Finish all source reads before opening the warehouse transaction.
extract player "$PLAYER_DB_PASSWORD" players
extract game "$GAME_DB_PASSWORD" lobbies
extract exam "$EXAM_DB_PASSWORD" exams
extract world "$WORLD_DB_PASSWORD" worlds
extract zombie "$ZOMBIE_DB_PASSWORD" zombie_instances
extract resource "$RESOURCE_DB_PASSWORD" transactions
extract base "$BASE_DB_PASSWORD" bases
extract crafting "$CRAFTING_DB_PASSWORD" craft_jobs

PGPASSWORD="$WAREHOUSE_PASSWORD" psql -X -v ON_ERROR_STOP=1 --single-transaction \
    -h warehouse -U warehouse -d pad_warehouse -f /warehouse/schema.sql -f "$stage/batch.sql"
echo 'Loaded one warehouse snapshot with eight service aggregates.'
