#!/bin/sh
set -eu

locale_settings="$(psql -X --no-password --username "$POSTGRES_USER" --dbname postgres --tuples-only --no-align \
    -v ON_ERROR_STOP=1 -c "SELECT pg_encoding_to_char(encoding), datlocprovider, daticulocale
                             FROM pg_database WHERE datname = current_database()")"
if [ "$locale_settings" != "UTF8|i|en-US" ]; then
    echo "EduFlow PostgreSQL requires UTF8 with the en-US ICU locale." >&2
    exit 78
fi

umask 077
printf '%s\n' pg16-alpine-icu > "$PGDATA/.eduflow-storage"
