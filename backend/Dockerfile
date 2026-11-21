FROM postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c AS pgtools
RUN mkdir /pg-libs && ldd /usr/lib/postgresql/16/bin/pg_dump | awk '/=> \// {print $3}' | sort -u | xargs -r cp -L -t /pg-libs

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
COPY backend/requirements.lock.txt ./backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt && useradd --uid 10001 --create-home eduflow
COPY --from=pgtools /pg-libs /opt/pg-libs
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
COPY backend/src ./backend/src
COPY backend/alembic ./backend/alembic
COPY backend/alembic.ini ./backend/alembic.ini
COPY frontend/dist ./frontend/dist
COPY backend/tooling.lock.txt ./backend/tooling.lock.txt
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r backend/tooling.lock.txt
ENV PYTHONPATH=/app/backend/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
