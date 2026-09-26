"""Management command for force-logging-out all users.

This deletes all session records from the configured session store.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    """Delete all sessions from the session store."""

    help = 'Deletes all sessions (forces logout for all users).'

    def handle(self, *args: object, **options: object) -> None:
        from django.contrib.sessions.models import Session

        with transaction.atomic():
            deleted_count, _ = Session.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted_count} session(s).'))
