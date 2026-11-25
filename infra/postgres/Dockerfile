FROM postgres:16-trixie@sha256:40852951a80d9dfaf52a1eb1f75d477f3c379ee8b148a1f40b9fa14c56b6e8cc

# Keep the upstream init/upgrade path. Its one privilege drop can use Debian's setpriv.
RUN set -eu; \
    entry=/usr/local/bin/docker-entrypoint.sh; \
    test "$(grep -Fc 'exec gosu postgres "$BASH_SOURCE" "$@"' "$entry")" = 1; \
    command -v setpriv; \
    sed -i 's/exec gosu postgres /exec setpriv --reuid=postgres --regid=postgres --init-groups -- /' "$entry"; \
    ! grep -q 'gosu' "$entry"; \
    rm /usr/local/bin/gosu; \
    test "$(setpriv --reuid=postgres --regid=postgres --init-groups -- id -un)" = postgres
