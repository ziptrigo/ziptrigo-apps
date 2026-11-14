#!/bin/sh
# Container entrypoint: optionally apply migrations, then run the command (gunicorn by default).
#
# RUN_MIGRATIONS=1 (set by the deployment's compose service) runs `migrate` before every start.
# Migrations are idempotent, so a restart with nothing new to apply costs a few queries.
# It then seeds World Time's city tables from the file in the image (apps/world_time/data), which
# is idempotent too: it compares the file's hash with the one stored and does nothing (one query)
# unless the file changed.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py world_time_seed_cities
fi

exec "$@"
