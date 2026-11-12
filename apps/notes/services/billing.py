"""Renewals (issue #260): charging paid notes, locking the ones that can't be paid for, unlocking
them when credits arrive, and warning ahead.

A note created in a free slot is free for good. Any other has `paid_through`, the date it renews
on, and costs `NotesSettings.renewal_price` per `renewal_days`.

- **`renew_due(now)`** (the daily job's first step). Every paid, unlocked note out of the Trash
  whose `paid_through` has passed, oldest first, each in its own transaction with the note locked
  and re-checked: the renewal price is spent (nothing at price 0) and `paid_through` moves on by
  one period (one period per run: a note still due after that is charged again by the next run). A
  balance that can't pay **locks** the note (`locked_at`): read-only for its owner, its share links
  stop, nothing is deleted. The owner gets an in-app notice per note (`note_locked:<id>`) and one
  email per run listing the notes locked in it. A note in the Trash is never charged or locked
  (restoring it pays the renewal it skipped, `services.notes.restore`). Successful renewals are
  silent (a ledger line).
- **`resume(user_id)`** (the `resume_notes` task, queued by the `credits_added` receiver in
  `apps.py` for a user who has a locked note, and run by the job as its second step): charges the
  locked, non-trashed notes oldest `locked_at` first, each `paid_through = now + renewal_days` and
  unlocked, stopping at the first the balance can't cover. The locked days are never billed.
- **`warn_upcoming(now)`** (the job's third step): a user whose balance won't cover the notes
  renewing within `renewal_warning_days` is told once per window (a notice and an email), stamped
  in `NotesAccount.renewal_warned_at`.

The daily job only offers the resume to notes locked **before its own start** (`resume(...,
locked_before=run_start)`), so a note this run locked isn't unlocked again at once with its day
forgiven.

An account that is deactivated or deleted is still charged and its notes still lock (as Receipts'
and file transfer's metering), but gets no in-app notice and no email.

Lock order, as everywhere in Notes: the user's `NotesAccount` row, then the note.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.tasks import task
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits
from apps.core.services.queue import enqueue_after_commit

from ..models import Note, NotesAccount, NotesSettings
from . import emails, notices
from .accounts import lock_account
from .notes import renewal_description
from .notices import SOURCE

logger = logging.getLogger(__name__)


@dataclass
class RenewalResult:
    """What one `renew_due` run did."""

    renewed: int = 0
    #: `{user id: [ids of the notes this run locked]}`.
    locked: dict[UUID, list[UUID]] = field(default_factory=dict)

    @property
    def locked_count(self) -> int:
        return sum(len(ids) for ids in self.locked.values())


# -- Renewing ------------------------------------------------------------------------------------


def renew_due(now=None) -> RenewalResult:
    """Renew every paid note that is due, locking the ones the balance can't cover. See the module
    docstring. One failing note is logged and skipped. Returns what was done; the locked notes'
    each owner's email is queued as soon as that owner's notes are done."""
    now = now or timezone.now()
    config = NotesSettings.load()
    due = list(
        Note.objects.filter(
            is_free=False,
            deleted_at__isnull=True,
            locked_at__isnull=True,
            paid_through__lte=now,
        )
        .order_by('paid_through', 'pk')
        .values_list('pk', 'user_id')
    )
    result = RenewalResult()
    # Grouped by user (in the order of each user's oldest due note) so a user's email can be queued
    # as soon as that user's notes are done: a run that dies later has still told the earlier users.
    by_user: dict[UUID, list[UUID]] = {}
    for note_id, user_id in due:
        by_user.setdefault(user_id, []).append(note_id)
    for user_id, note_ids in by_user.items():
        for note_id in note_ids:
            try:
                outcome = _renew_one(note_id, user_id, now, config)
            except Exception:
                logger.exception('Could not renew note %s', note_id)
                continue
            if outcome == 'renewed':
                result.renewed += 1
            elif outcome == 'locked':
                result.locked.setdefault(user_id, []).append(note_id)
        if user_id in result.locked:
            enqueue_after_commit(
                emails.send_notes_locked_email,
                str(user_id),
                [str(pk) for pk in result.locked[user_id]],
            )
    return result


def _renew_one(note_id: UUID, user_id: UUID, now, config: NotesSettings) -> str:
    """Renew or lock one note, re-checking under the locks. Returns `renewed`, `locked` or
    `skipped` (it changed since the job looked)."""
    with transaction.atomic():
        lock_account(user_id)
        note = (
            Note.objects.select_for_update(of=('self',))
            .select_related('user')
            .filter(pk=note_id)
            .first()
        )
        if (
            note is None
            or note.is_free
            or note.deleted_at is not None
            or note.locked_at is not None
            or note.paid_through is None
            or note.paid_through > now
        ):
            return 'skipped'
        price = config.renewal_price
        if price:
            try:
                spend_credits(
                    note.user,
                    price,
                    description=renewal_description(note.title),
                    source=SOURCE,
                )
            except InsufficientCreditsError:
                note.locked_at = now
                note.save(update_fields=['locked_at'])
                notices.notify_locked(note.user, note.pk, note.title, price, config.renewal_days)
                return 'locked'
        note.paid_through = note.paid_through + timedelta(days=config.renewal_days)
        note.save(update_fields=['paid_through'])
        return 'renewed'


# -- Resuming ------------------------------------------------------------------------------------


@task(queue_name='default')
def resume_notes(user_id: str) -> None:
    resume(user_id)


def resume(user_id: str, *, locked_before=None) -> int:
    """Unlock the user's locked notes (out of the Trash), oldest `locked_at` first, charging each
    the renewal price and starting a new period now; stop at the first the balance can't cover.
    `locked_before`, when given, limits it to the notes locked strictly before that instant (the
    daily job passes its own start, so a note its `renew_due` locked isn't unlocked again at once).
    Returns how many were unlocked. Does nothing for an unknown user."""
    try:
        user = User.objects.get(pk=UUID(str(user_id)))
    except User.DoesNotExist, ValueError:
        return 0
    queryset = Note.objects.filter(user=user, locked_at__isnull=False, deleted_at__isnull=True)
    if locked_before is not None:
        queryset = queryset.filter(locked_at__lt=locked_before)
    candidates = list(queryset.order_by('locked_at', 'pk').values_list('pk', flat=True))
    unlocked = 0
    for note_id in candidates:
        try:
            outcome = _unlock_one(note_id, user)
        except Exception:
            logger.exception('Could not unlock note %s', note_id)
            break
        if outcome == 'unaffordable':
            break
        if outcome == 'unlocked':
            unlocked += 1
    return unlocked


def _unlock_one(note_id: UUID, user: User) -> str:
    """Returns `unlocked`, `unaffordable` (stop) or `skipped` (no longer locked)."""
    with transaction.atomic():
        lock_account(user.pk)
        config = NotesSettings.load()
        note = Note.objects.select_for_update().filter(pk=note_id).first()
        if note is None or note.locked_at is None or note.deleted_at is not None:
            return 'skipped'
        price = config.renewal_price
        if price:
            try:
                spend_credits(
                    user, price, description=renewal_description(note.title), source=SOURCE
                )
            except InsufficientCreditsError:
                return 'unaffordable'
        note.paid_through = timezone.now() + timedelta(days=config.renewal_days)
        note.locked_at = None
        note.save(update_fields=['paid_through', 'locked_at'])
        notices.clear_locked(note.user_id, note.pk)
        return 'unlocked'


def on_credits_added(user: User) -> None:
    """The `billing.credits_added` receiver's work: queue the resume for a user who has a locked
    note. Only queues (the task runs after the commit, with the credits visible)."""
    if Note.objects.filter(user=user, locked_at__isnull=False, deleted_at__isnull=True).exists():
        enqueue_after_commit(resume_notes, str(user.pk))


# -- Warning ahead -------------------------------------------------------------------------------


def warn_upcoming(now=None) -> int:
    """Warn the users whose balance won't cover the renewals coming within `renewal_warning_days`
    (see the module docstring). Per-user `try/except`. Returns how many users were warned."""
    now = now or timezone.now()
    config = NotesSettings.load()
    if not config.renewal_price:
        return 0
    days = config.renewal_warning_days
    horizon = now + timedelta(days=days)
    counts = (
        Note.objects.filter(
            is_free=False,
            deleted_at__isnull=True,
            locked_at__isnull=True,
            paid_through__lte=horizon,
        )
        .values('user_id')
        .annotate(n=Count('pk'))
        .order_by('user_id')
    )
    warned = 0
    for row in list(counts):
        try:
            if _warn_user(row['user_id'], now, config, horizon):
                warned += 1
        except Exception:
            logger.exception('Could not warn user %s about upcoming renewals', row['user_id'])
    return warned


def _warn_user(user_id: UUID, now, config: NotesSettings, horizon) -> bool:
    days = config.renewal_warning_days
    with transaction.atomic():
        NotesAccount.objects.get_or_create(user_id=user_id)
        lock_account(user_id)
        account = NotesAccount.objects.select_related('user').get(pk=user_id)
        if not notices.can_notify(account.user):
            return False
        if account.renewal_warned_at is not None and (
            account.renewal_warned_at >= now - timedelta(days=days)
        ):
            return False
        # Counted again under the lock: a note trashed or renewed since the job's query isn't owed.
        count = Note.objects.filter(
            user_id=user_id,
            is_free=False,
            deleted_at__isnull=True,
            locked_at__isnull=True,
            paid_through__lte=horizon,
        ).count()
        credits = count * config.renewal_price
        balance = get_balance(account.user)
        if not count or credits <= balance:
            return False
        account.renewal_warned_at = now
        account.save(update_fields=['renewal_warned_at'])
        notices.notify_warning(
            account.user, count=count, credits=credits, balance=balance, days=days
        )
        enqueue_after_commit(
            emails.send_renewal_warning_email, str(user_id), count, credits, balance, days
        )
    return True
