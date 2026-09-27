#!/bin/sh
# Container entrypoint: optionally apply migrations, then run the command (gunicorn by default).
#
# RUN_MIGRATIONS=1 (set by the deployment's compose service) runs `migrate` before every start.
# Migrations are idempotent, so a restart with nothing new to apply costs a few queries.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
