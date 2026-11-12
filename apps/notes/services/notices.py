"""The in-app notices Notes writes about renewals: "a note is locked" and "not enough credits for
the renewals coming up". Each runs in a savepoint of its own and logs rather than raises, so a
failing notice can never undo a lock, a resume or a warning stamp (the way Receipts' and To-do's
freeze notices work).
"""

import logging
from uuid import UUID

from django.db import transaction
from django.urls import reverse

from apps.core.services import notifications

logger = logging.getLogger(__name__)

SOURCE = 'notes'
#: `Notification.key` of a note's "locked" notice is this plus the note's id.
LOCKED_KEY_PREFIX = 'note_locked:'
#: One renewal warning per user, replaced by the latest.
WARNING_KEY = 'notes_renewal_warning'
#: The part of a note's title that goes into a notice, an email or a ledger line.
TITLE_CUT = 60


def locked_key(note_id: UUID | str) -> str:
    return f'{LOCKED_KEY_PREFIX}{note_id}'


def notify_locked(user_id: UUID, note_id: UUID, title: str, renewal_price: int) -> None:
    """Tell the owner the note is locked (key `note_locked:<note id>`; the url is the note's page)."""
    try:
        with transaction.atomic():
            key = locked_key(note_id)
            notifications.delete_for_key(user_id, key, source=SOURCE)
            notifications.notify(
                user_id,
                source=SOURCE,
                title=f'Your note "{title[:TITLE_CUT]}" is locked',
                body=(
                    f'Its yearly renewal of {renewal_price} credits could not be paid. '
                    'Top up to unlock it.'
                ),
                url=reverse('notes:note', args=[note_id]),
                key=key,
            )
    except Exception:
        logger.exception('Could not notify user %s that note %s is locked', user_id, note_id)


def clear_locked(user_id: UUID, note_id: UUID) -> None:
    """Delete the note's "locked" notice (it was unlocked)."""
    try:
        with transaction.atomic():
            notifications.delete_for_key(user_id, locked_key(note_id), source=SOURCE)
    except Exception:
        logger.exception('Could not clear the locked notice of note %s', note_id)


def notify_warning(user_id: UUID, *, count: int, credits: int, balance: int, days: int) -> None:
    """The renewal warning: written as `delete_for_key` then `notify`, so there is one, the latest."""
    notes = 'note renews' if count == 1 else 'notes renew'
    try:
        with transaction.atomic():
            notifications.delete_for_key(user_id, WARNING_KEY, source=SOURCE)
            notifications.notify(
                user_id,
                source=SOURCE,
                title='Notes renewal: not enough credits',
                body=(
                    f'{count} {notes} in the next {days} days for {credits} credits '
                    f'and your balance is {balance}.'
                ),
                url=reverse('billing:credits-history'),
                key=WARNING_KEY,
            )
    except Exception:
        logger.exception('Could not write the renewal warning of user %s', user_id)
