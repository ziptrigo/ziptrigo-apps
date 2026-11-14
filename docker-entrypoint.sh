#!/bin/sh
# Container entrypoint: optionally apply migrations, then run the command (gunicorn by default).
#
# RUN_MIGRATIONS=1 (set by the deployment's compose service) runs `migrate` before every start.
# Migrations are idempotent, so a restart with nothing new to apply costs a few queries.
# It then seeds World Time's city tables from the file in the image (apps/world_time/data), which
# is idempotent too: it compares the file's hash with the one stored and does nothing (one query)
# unless the file changed. A failed seed must never stop the site from starting (it only feeds one
# search box, which says so and still finds time zones): it is reported and the start goes on; the
# next start, or running the command by hand, seeds it.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py world_time_seed_cities \
        || echo "WARNING: world_time_seed_cities failed; World Time's city search has no cities until it is run again." >&2
fi

exec "$@"
