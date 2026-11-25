#!/bin/sh
set -eu

case "${1:-postgres}" in
    postgres|-*)
        marker="$PGDATA/.eduflow-storage"
        if [ -s "$PGDATA/PG_VERSION" ] &&
           { [ ! -f "$marker" ] || [ "$(cat "$marker")" != "pg16-alpine-icu" ]; }; then
            echo "Existing PostgreSQL volume requires a logical backup and restore into a fresh EduFlow volume." >&2
            exit 78
        fi
        ;;
esac

exec docker-entrypoint.sh "$@"
