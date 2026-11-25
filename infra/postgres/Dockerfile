FROM postgres:16.16-alpine3.24@sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d

# Keep the upstream initialization flow with Alpine's packaged privilege drop.
RUN set -eu; \
    apk add --no-cache --upgrade 'libxml2=2.13.9-r3' 'su-exec=0.3-r0'; \
    entry=/usr/local/bin/docker-entrypoint.sh; \
    test "$(grep -Fc 'exec gosu postgres "$BASH_SOURCE" "$@"' "$entry")" = 1; \
    rm /usr/local/bin/gosu /usr/local/bin/su-exec; \
    sed -i 's/exec gosu postgres /exec su-exec postgres /' "$entry"; \
    if grep -q 'gosu' "$entry"; then exit 1; fi; \
    test "$(su-exec postgres id -un)" = postgres

ENV POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C --locale-provider=icu --icu-locale=en-US"
LABEL org.eduflow.postgres-storage="pg16-alpine-icu"
COPY --chmod=0755 entrypoint.sh /usr/local/bin/eduflow-postgres-entrypoint
COPY --chmod=0755 mark-storage.sh /docker-entrypoint-initdb.d/00-eduflow-storage.sh
ENTRYPOINT ["eduflow-postgres-entrypoint"]
# A new ENTRYPOINT resets the base image's inherited CMD.
CMD ["postgres"]
