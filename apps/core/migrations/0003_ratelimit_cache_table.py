# Issue #53: create the `'ratelimit'` cache alias's table (`config/settings.py`'s `CACHES`)
# when it's the `DatabaseCache` backend (the dev/prod default; not used under pytest, and not
# used when `CACHE_URL` points at Redis instead) -- so `python manage.py migrate`, already run on
# every deploy (`docker-entrypoint.sh`), is all that's needed; no separate `createcachetable` step
# in the entrypoint or compose files.
#
# `createcachetable` is safe to call for a cache alias that isn't `DatabaseCache` (it just creates
# an unused table) and safe to call twice (`CREATE TABLE IF NOT EXISTS`), so this doesn't need to
# branch on which backend is actually configured.

from django.core.management import call_command
from django.db import migrations


def create_ratelimit_cache_table(apps, schema_editor):
    call_command('createcachetable', 'ratelimit_cache', verbosity=0)


def drop_ratelimit_cache_table(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP TABLE IF EXISTS ratelimit_cache')


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0002_coresettings_emailverification'),
    ]

    operations = [
        migrations.RunPython(create_ratelimit_cache_table, drop_ratelimit_cache_table),
    ]
