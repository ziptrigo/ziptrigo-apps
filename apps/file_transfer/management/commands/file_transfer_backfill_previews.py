"""Choose the preview type of files that were uploaded before previews existed (issue #311).

    ./manage.py file_transfer_backfill_previews

The download page previews a file only when `TransferFile.preview_type` is set, which happens when
an upload completes; this reads the first bytes of the older, still-stored files and sets it.
Reads one ranged S3 request per file; running it again is harmless.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.file_transfer.services.preview import backfill_previews


class Command(BaseCommand):
    help = 'Set the preview type of already uploaded files that have none (idempotent).'

    def handle(self, *args: Any, **options: Any) -> None:
        looked, found = backfill_previews()
        self.stdout.write(
            self.style.SUCCESS(f'Looked at {looked} file(s); {found} can now be previewed.')
        )
