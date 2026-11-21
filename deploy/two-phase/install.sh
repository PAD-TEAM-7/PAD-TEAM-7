#!/bin/sh
set -eu
export PGCONNECT_TIMEOUT=5
# Owners' database credentials are available only to this bootstrap job.
PGPASSWORD="$RESOURCE_DB_PASSWORD" psql -X -h db-router -p 15437 \
    -U resource -d resource_db -f /two-phase/resource.sql
PGPASSWORD="$PLAYER_DB_PASSWORD" psql -X -h db-router -p 15432 \
    -U player -d player_db -f /two-phase/player.sql
