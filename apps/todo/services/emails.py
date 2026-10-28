"""The emails To-do sends, always to the account owner (issue #184): "storage paused".

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
from django.tasks import task
from django.template.defaultfilters import filesizeformat
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.core.services.email import send_email

from ..models import TodoAccount

logger = logging.getLogger(__name__)

STORAGE_PAUSED_SUBJECT = 'Your to-do file storage is paused'


def _absolute(url_name: str) -> str:
    return f'{settings.BASE_URL}{reverse(url_name)}'


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
        'top_up_url': _absolute('billing:credits-history'),
        'todo_url': _absolute('todo:settings'),
    }
    send_email(
        to=user.email,
        subject=STORAGE_PAUSED_SUBJECT,
        text_body=render_to_string('todo/emails/storage_paused.txt', context),
        html_body=render_to_string('todo/emails/storage_paused.html', context),
    )
