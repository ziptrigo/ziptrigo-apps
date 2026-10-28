"""Ingest a local `.eml` file as if SES had just delivered it, without SES or the S3 poller.

    ./manage.py todo_ingest_eml task.eml --to todo+abcd2345@ziptrigo.com

The file is uploaded to the inbound prefix (`todo/inbound/local-<uuid>`, in whatever bucket
`TODO_S3_*` points at -- Floci locally) and the real ingest then runs on it inline: claim,
download, parse, allowlist, store, create the task, delete. The only difference from a delivery by
SES is that `--to` (repeatable) *replaces* the recipient found in the headers, since a saved `.eml`
rarely has the envelope recipient SES would have added; leave `--to` out to use the headers. The
SES headers (`X-SES-Spam-Verdict`, `X-SES-Virus-Verdict` and an `Authentication-Results:
amazonses.com; ...` with the SPF/DKIM/DMARC results) are read from the file as usual, so add them
to test an allowlist or a rejection.
"""

import uuid
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.todo.models import InboundMessage, InboundMessageStatus
from apps.todo.services import inbound
from apps.todo.services.storage import INBOUND_PREFIX, get_storage


class Command(BaseCommand):
    help = 'Ingest a local .eml file through the real email-intake path (no SES, no polling).'

    def add_arguments(self, parser) -> None:
        parser.add_argument('eml', type=Path, help='Path of the .eml file.')
        parser.add_argument(
            '--to',
            action='append',
            dest='recipients',
            metavar='ADDRESS',
            help='Inbox address(es) to deliver to, e.g. todo+abcd2345@ziptrigo.com. Replaces '
            'the recipient in the file; omit to use the headers.',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path = options['eml']
        if not path.is_file():
            raise CommandError(f'{path} is not a file.')
        raw = path.read_bytes()
        if len(raw) > inbound.MAX_RAW_BYTES:
            raise CommandError('The file is over the 40 MB limit SES has.')

        key = f'{INBOUND_PREFIX}local-{uuid.uuid4()}'
        get_storage().put_object(key, raw, content_type='message/rfc822')
        message = InboundMessage.objects.create(s3_key=key)
        inbound.ingest(message.pk, recipients=options['recipients'])
        message.refresh_from_db()

        for email in message.emails.select_related('task').order_by('created_at'):
            created = f', task #{email.task.number}' if email.task is not None else ''
            self.stdout.write(
                f'{email.recipient}: {email.status}'
                + (f' ({email.note})' if email.note else '')
                + created
            )
        if not message.emails.exists():
            self.stdout.write('No recipient matched an active inbox code.')
        if message.status != InboundMessageStatus.DONE:
            raise CommandError(
                f'Ingest {message.status} after {message.attempts} attempt(s): {message.last_error}'
                f' (the object is at {message.failed_key or key})'
            )
        self.stdout.write(self.style.SUCCESS(f'Done ({key}).'))
