"""`./manage.py world_time_seed_cities`: fill the World Time city tables from the file in the
repository. Idempotent, and it does nothing (one query) when the tables already hold that file, so
the container's entrypoint runs it after every `migrate`."""

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.world_time.services import cities


class Command(BaseCommand):
    help = (
        'Seed the World Time city tables from apps/world_time/data/cities.tsv.gz. Safe to run '
        'any number of times: it does nothing when the tables already hold that file.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--file', type=Path, default=cities.DATA_FILE, help='Another city file to seed from.'
        )
        parser.add_argument(
            '--force', action='store_true', help='Seed even when the file has not changed.'
        )

    def handle(self, *args, **options):
        result = cities.seed(options['file'], force=options['force'])
        if result.changed:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Seeded {result.cities:,} cities and {result.names:,} other names.'
                )
            )
        else:
            self.stdout.write(f'City data is up to date ({result.cities:,} cities).')
