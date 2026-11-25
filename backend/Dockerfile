FROM postgres:16-trixie@sha256:40852951a80d9dfaf52a1eb1f75d477f3c379ee8b148a1f40b9fa14c56b6e8cc AS pgtools
RUN mkdir /pg-libs && ldd /usr/lib/postgresql/16/bin/pg_dump /usr/lib/postgresql/16/bin/pg_restore | awk '/=> \// {print $3}' | sort -u | xargs -r cp -L -t /pg-libs

FROM python:3.12-slim-trixie@sha256:37fc60d303cb9294d3718b5b48998a0210bb1c73611c1399ef5eb7964c0536e5 AS python-base
FROM python-base AS dependencies
COPY backend/tooling.lock.txt backend/requirements.lock.txt /build/
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /build/tooling.lock.txt \
    && python -m venv --without-pip /opt/venv \
    && python -m pip --python /opt/venv install --no-cache-dir --only-binary=:all: -r /build/requirements.lock.txt

FROM python-base
WORKDIR /app
RUN python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.12/ensurepip \
    && useradd --uid 10001 --create-home eduflow
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=pgtools /pg-libs /opt/pg-libs
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
COPY backend/src ./backend/src
COPY backend/alembic ./backend/alembic
COPY backend/alembic.ini ./backend/alembic.ini
COPY frontend/dist ./frontend/dist
ENV PATH="/opt/venv/bin:${PATH}" PYTHONPATH=/app/backend/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
