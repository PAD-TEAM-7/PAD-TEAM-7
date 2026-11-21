#!/bin/sh
set -eu

if [ -e "$PGDATA/.fenced" ]; then
    echo 'Database is fenced after failover. Rebuild it as a standby before reuse.' >&2
    exit 1
fi

if [ -n "${FOLLOW_HOST:-}" ] && [ ! -s "$PGDATA/PG_VERSION" ]; then
    # Use a sibling staging directory on the same volume for atomic publication.
    backup="$(mktemp -d "${PGDATA%/*}/.seed.XXXXXX")"
    trap 'if [ -d "$backup" ]; then rm -rf "$backup"; fi' EXIT
    chown postgres:postgres "$backup"
    export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
    until gosu postgres pg_basebackup -h "$FOLLOW_HOST" -p "$FOLLOW_PORT" \
        -U "$POSTGRES_USER" -D "$backup" -X stream --no-password; do
        rm -rf "$backup"
        mkdir "$backup"
        chown postgres:postgres "$backup"
        sleep 3
    done
    touch "$backup/standby.signal"
    mv -T "$backup" "$PGDATA"
fi

if [ -f "$PGDATA/standby.signal" ]; then
    # A private libpq passfile keeps the password out of postgres command arguments.
    umask 077
    escaped="$(printf '%s' "$POSTGRES_PASSWORD" | sed 's/[\\:]/\\&/g')"
    printf '%s:%s:replication:%s:%s\n' "$FOLLOW_HOST" "$FOLLOW_PORT" "$POSTGRES_USER" "$escaped" > /tmp/pad-replication.pass
    chown postgres:postgres /tmp/pad-replication.pass
    set -- "$@" -c "primary_conninfo=host=$FOLLOW_HOST port=$FOLLOW_PORT user=$POSTGRES_USER passfile=/tmp/pad-replication.pass connect_timeout=5"
fi
exec docker-entrypoint.sh postgres "$@" -c hba_file=/db-ha/pg_hba.conf -c wal_keep_size=256MB
