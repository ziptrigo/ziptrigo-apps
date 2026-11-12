"""The emails Notes sends, always to the account owner (issue #260): "some of your notes are
locked" and "your notes renew soon".

`django.tasks` tasks on the `default` queue, queued with `enqueue_after_commit` and taking plain
ids and numbers (see `apps/todo/services/emails.py`). Both bodies are rendered from templates
(`notes/emails/*.txt` / `.html`); the HTML one is autoescaped (a note's title is free text, and
without an explicit `html_body` the backend would wrap the text unescaped). Nothing user-controlled
goes into a subject. Dates are the public wording, in UTC. Only a *confirmed* address gets mail:
changing the email through the API un-confirms the account but leaves sessions valid. Links are
absolute (`settings.BASE_URL` + `reverse()`).
"""

import logging
from uuid import UUID

from django.conf import settings
from django.db.models import Min
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import get_balance
from apps.core.datetime_format import utc_date_time
from apps.core.services.email import send_email

from ..models import Note, NotesSettings
from .notices import TITLE_CUT

logger = logging.getLogger(__name__)

LOCKED_SUBJECT = 'Some of your notes are locked'
WARNING_SUBJECT = 'Your notes renew soon'
#: Notes named in the locked email; the rest are counted.
MAX_LISTED = 20


def _absolute(url_name: str, *args: object) -> str:
    return f'{settings.BASE_URL}{reverse(url_name, args=args)}'


def _confirmed_user(user_id: str) -> User | None:
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return None
    if (
        not user.is_active
        or user.status != User.STATUS_ACTIVE
        or not user.email
        or not user.email_confirmed
    ):
        return None
    return user


@task(queue_name='default')
def send_notes_locked_email(user_id: str, note_ids: list[str]) -> None:
    """ "Some of your notes are locked": one email per user per run, listing the notes the run
    locked that are still locked now (a top-up in between leaves nothing to say)."""
    user = _confirmed_user(user_id)
    if user is None:
        return
    try:
        ids = [UUID(value) for value in note_ids]
    except ValueError:
        return
    locked = list(
        Note.objects.filter(
            user=user, pk__in=ids, locked_at__isnull=False, deleted_at__isnull=True
        ).order_by('paid_through', 'pk')
    )
    if not locked:
        return
    price = NotesSettings.load().renewal_price
    # What unlocks them *all*: every locked note of the user (earlier runs' too: a top-up unlocks
    # the oldest lock first), minus what the balance already holds.
    all_locked = Note.objects.filter(
        user=user, locked_at__isnull=False, deleted_at__isnull=True
    ).count()
    missing = max(price * all_locked - get_balance(user), 0)
    listed = [
        {
            'title': note.title[:TITLE_CUT],
            'due': utc_date_time(note.paid_through)[0] if note.paid_through else '',
        }
        for note in locked[:MAX_LISTED]
    ]
    context = {
        'count': len(locked),
        'notes': listed,
        'more': max(len(locked) - MAX_LISTED, 0),
        'renewal_price': price,
        'missing': missing,
        'top_up_url': _absolute('billing:credits-history'),
        'notes_url': _absolute('notes:list'),
    }
    send_email(
        to=user.email,
        subject=LOCKED_SUBJECT,
        text_body=render_to_string('notes/emails/notes_locked.txt', context),
        html_body=render_to_string('notes/emails/notes_locked.html', context),
    )


@task(queue_name='default')
def send_renewal_warning_email(
    user_id: str, count: int, credits: int, balance: int, days: int
) -> None:
    """ "Your notes renew soon": the balance won't cover the `count` notes renewing in the next
    `days` days (`credits` in all)."""
    user = _confirmed_user(user_id)
    if user is None:
        return
    first = Note.objects.filter(
        user=user, is_free=False, deleted_at__isnull=True, locked_at__isnull=True
    ).aggregate(first=Min('paid_through'))['first']
    context = {
        'count': count,
        'credits': credits,
        'balance': balance,
        'days': days,
        'first_due': utc_date_time(first)[0] if first else '',
        'overdue': bool(first and first <= timezone.now()),
        'missing': max(credits - balance, 0),
        'top_up_url': _absolute('billing:credits-history'),
        'notes_url': _absolute('notes:list'),
    }
    send_email(
        to=user.email,
        subject=WARNING_SUBJECT,
        text_body=render_to_string('notes/emails/renewal_warning.txt', context),
        html_body=render_to_string('notes/emails/renewal_warning.html', context),
    )
