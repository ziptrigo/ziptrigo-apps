"""The emails To-do sends, always to the account owner: "storage paused" (issue #184) and "your
export is ready" (issue #326).

A `django.tasks` task on the `default` queue -- call `<name>.enqueue(...)`, never
`<name>.call(...)` -- taking a plain id and re-reading the rows (see
`apps/file_transfer/services/emails.py`). Both bodies are rendered from templates
(`todo/emails/storage_paused.txt` / `.html`); the HTML one is autoescaped, since without an explicit
`html_body` `SesEmailBackend` wraps the text in an unescaped `<pre>`. Nothing user-controlled goes
into the subject.

The mail is only sent to a *confirmed* address: changing the email through the API un-confirms the
account but leaves sessions valid. Links are absolute (`settings.BASE_URL` + `reverse()`).
"""

import logging
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.tasks import task
from django.template.defaultfilters import filesizeformat
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.billing.services import credits_url
from apps.core.datetime_format import format_utc
from apps.core.services.email import send_email

from ..models import ExportStatus, TodoAccount, TodoExport

logger = logging.getLogger(__name__)

STORAGE_PAUSED_SUBJECT = 'Your to-do file storage is paused'


def _absolute(url_name: str, *args: object) -> str:
    return f'{settings.BASE_URL}{reverse(url_name, args=args)}'


@task(queue_name='default')
def send_storage_paused_email(user_id: str) -> None:
    """ "Storage is paused" -- to the owner, when the daily storage charge couldn't be paid."""
    from .metering import daily_storage_cost  # metering imports this module

    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return
    if not user.email or not user.email_confirmed:
        return
    account = TodoAccount.objects.filter(user=user).first()
    stored = account.stored_bytes if account is not None else 0
    context = {
        'stored': filesizeformat(stored),
        'daily_cost': daily_storage_cost(stored),
        'top_up_url': credits_url('history', absolute=True),
        'todo_url': _absolute('todo:settings'),
    }
    send_email(
        to=user.email,
        subject=STORAGE_PAUSED_SUBJECT,
        text_body=render_to_string('todo/emails/storage_paused.txt', context),
        html_body=render_to_string('todo/emails/storage_paused.html', context),
    )


EXPORT_READY_SUBJECT = 'Your to-do export is ready'


@task(queue_name='default')
def send_export_ready_email(export_id: str) -> None:
    """ "Your to-do export is ready" -- to the owner, with a link to the export's page (never a
    link to a file: the page is behind sign-in), to a confirmed address of an active account only.
    Rendered from `todo/emails/export_ready.{txt,html}`; the HTML is autoescaped. Nothing in the
    subject."""
    try:
        export = TodoExport.objects.select_related('user').get(pk=export_id)
    except TodoExport.DoesNotExist, ValidationError:
        return
    user = export.user
    if export.status != ExportStatus.READY or not user.email or not user.email_confirmed:
        return
    if not user.is_active or user.status != User.STATUS_ACTIVE:
        return
    context = {
        'file_count': export.file_count - export.skipped_count,
        'part_count': export.part_count,
        'expires': format_utc(export.expires_at) if export.expires_at else '',
        'skipped_count': export.skipped_count,
        'refunded': export.refunded_credits,
        'export_url': _absolute('todo:export-detail', export.pk),
    }
    send_email(
        to=user.email,
        subject=EXPORT_READY_SUBJECT,
        text_body=render_to_string('todo/emails/export_ready.txt', context),
        html_body=render_to_string('todo/emails/export_ready.html', context),
    )
