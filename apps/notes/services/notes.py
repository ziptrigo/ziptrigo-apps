"""Notes: creating, saving (with conflicts), checklists, tags, archive, Trash, restore, duplicate
and manual order.

Refusals are `django.core.exceptions.ValidationError`s keyed by field (`__all__` for none). Lock
order in every write: the user's `NotesAccount` row (`accounts.lock_account`), then the note row.
Nothing is ever refunded.
"""

import logging
from collections.abc import Iterable, Sequence
from datetime import timedelta
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import spend_credits
from apps.core.markdown import count_checklist_items, toggle_checklist_item

from ..limits import CONTENT_MAX, TITLE_MAX
from ..models import EditorKind, LinkMode, Note, NotesSettings, NoteVersion, ShareLink, Tag
from . import notices, versions
from .accounts import get_account, lock_account
from .errors import CostChangedError, LinkUnavailableError, NoteConflictError
from .versions import Editor

logger = logging.getLogger(__name__)

IN_TRASH_MESSAGE = 'This note is in the Trash.'
LOCKED_MESSAGE = 'This note is locked until its renewal is paid.'
TAKEN_DOWN_MESSAGE = 'This note was taken down and can no longer be changed.'
COPY_TAKEN_DOWN_MESSAGE = 'This note was taken down and cannot be copied.'
TOO_MANY_NOTES_MESSAGE = 'You have too many notes. Delete some first.'
COPY_SUFFIX = ' (copy)'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


# -- Fields -------------------------------------------------------------------------------------


def clean_title(title: object) -> str:
    cleaned = str(title or '').strip()
    if not cleaned:
        raise _error('title', 'Give the note a title.')
    if len(cleaned) > TITLE_MAX:
        raise _error('title', f'Keep the title to {TITLE_MAX} characters or fewer.')
    return cleaned


def clean_content(content: object) -> str:
    cleaned = str(content or '').replace('\r\n', '\n').replace('\r', '\n')
    if len(cleaned) > CONTENT_MAX:
        raise _error('content', f'Keep the note to {CONTENT_MAX:,} characters or fewer.')
    return cleaned


def _own_tags(user: User, tag_ids: Iterable[UUID | str]) -> list[Tag]:
    """The user's tags with these ids (each once). Raises when an id isn't one of theirs."""
    wanted = list(dict.fromkeys(str(tag_id) for tag_id in tag_ids))
    if not wanted:
        return []
    try:
        found = list(Tag.objects.filter(user=user, pk__in=wanted))
    except ValueError, ValidationError:
        raise _error('tags', 'Choose your own tags.') from None
    if len(found) != len(wanted):
        raise _error('tags', 'Choose your own tags.')
    return found


def require_writable(note: Note) -> None:
    """Refuse a change to a note that is read-only (in the Trash, locked for an unpaid renewal, or
    taken down by staff)."""
    if note.is_trashed:
        raise _error(NON_FIELD_ERRORS, IN_TRASH_MESSAGE)
    if note.is_locked:
        raise _error(NON_FIELD_ERRORS, LOCKED_MESSAGE)
    if note.is_taken_down:
        raise _error(NON_FIELD_ERRORS, TAKEN_DOWN_MESSAGE)


def check_tags(note: Note, tag_ids: Iterable[UUID | str]) -> None:
    """The refusals `set_tags` would make for the note's state and the tag ids, without locking or
    writing anything: the web checks them before it charges the rate limit. (`set_tags` decides
    again under the lock.)"""
    require_writable(note)
    _own_tags(note.user, tag_ids)


def check_toggle(note: Note, index: int) -> None:
    """The refusals `toggle_checklist` would make (read-only note, no such item), without locking
    or writing anything."""
    require_writable(note)
    try:
        count = count_checklist_items(note.content, profile='extended')
    except ValueError:
        count = 0
    if not 0 <= index < count:
        raise _error('index', 'That checklist item no longer exists.')


def _locked_note(note: Note) -> Note:
    """The note's row, locked. Call inside a transaction, after `lock_account`."""
    return Note.objects.select_for_update().get(pk=note.pk)


def _lock_editing_link(link: ShareLink, note: Note) -> ShareLink:
    """The share link a save came through, locked and re-read: it must still be an active,
    editable link of this note, or `LinkUnavailableError` (a revoke or an expiry that landed
    while the request was on its way). Call inside a transaction, after the note's lock."""
    fresh = ShareLink.objects.select_for_update().filter(pk=link.pk, note_id=note.pk).first()
    if fresh is None or fresh.mode != LinkMode.EDIT or not fresh.is_active():
        raise LinkUnavailableError
    return fresh


def _after_link_edit(note: Note, owner: User, editor: Editor, link: ShareLink) -> None:
    """What an edit through a share link leaves behind: the link's `last_edited_at` and, unless the
    owner made it, the owner's "edited through a shared link" notice."""
    link.last_edited_at = timezone.now()
    link.save(update_fields=['last_edited_at'])
    if editor.kind != EditorKind.OWNER:
        notices.notify_edited(owner, note.pk, note.title)


# -- Free slots and costs ------------------------------------------------------------------------


def free_slots_used(user: User) -> int:
    """How many of the user's free notes are outside the Trash (archived ones count)."""
    return Note.objects.filter(user=user, is_free=True, deleted_at__isnull=True).count()


def _slot_open(user: User, config: NotesSettings) -> bool:
    return free_slots_used(user) < config.free_notes


def quote_create(user: User) -> int:
    """What creating a note costs the user now: 0 when a free slot is open, else the creation
    price."""
    config = NotesSettings.load()
    return 0 if _slot_open(user, config) else config.creation_price


def _copy_title(title: str) -> str:
    if len(title) + len(COPY_SUFFIX) <= TITLE_MAX:
        return f'{title}{COPY_SUFFIX}'
    return f'{title[: TITLE_MAX - len(COPY_SUFFIX)].rstrip()}{COPY_SUFFIX}'


def _description(prefix: str, title: str) -> str:
    return f'{prefix}: "{title[: notices.TITLE_CUT]}"'


def renewal_description(title: str) -> str:
    """The ledger line of a renewal, charged by the daily job, by `resume` and by `restore`."""
    return _description('Notes renewal', title)


# -- Creating ------------------------------------------------------------------------------------


def create_note(
    user: User,
    *,
    title: str,
    content: str = '',
    tags: Iterable[UUID | str] = (),
    expected_cost: int | None = None,
    source: str = 'web',
) -> Note:
    """Create a note (first in the user's manual order) and its version 1.

    A free slot makes a free note; otherwise it costs `creation_price` and is paid through
    `renewal_days` from now. `expected_cost`, when given, is what the caller confirmed: the cost
    is worked out again under the account lock and `CostChangedError` raised (nothing charged or
    created) when it differs. `InsufficientCreditsError` rolls everything back. `source` is the
    channel (`web` or `api`); it is not stored.

    Raises:
        ValidationError: the title, the content, the tags or the cap on notes.
        CostChangedError, apps.billing.services.InsufficientCreditsError
    """
    cleaned_title = clean_title(title)
    cleaned_content = clean_content(content)
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)
        config = NotesSettings.load()  # after the lock: the cost is worked out as late as possible
        chosen = _own_tags(user, tags)
        if Note.objects.filter(user=user, deleted_at__isnull=True).count() >= (
            config.max_notes_per_user
        ):
            raise _error(NON_FIELD_ERRORS, TOO_MANY_NOTES_MESSAGE)
        free = _slot_open(user, config)
        cost = 0 if free else config.creation_price
        if expected_cost is not None and expected_cost != cost:
            raise CostChangedError(cost)
        now = timezone.now()
        if cost:
            spend_credits(
                user, cost, source='notes', description=_description('Notes', cleaned_title)
            )
        lowest = Note.objects.filter(user=user).aggregate(low=Min('position'))['low']
        note = Note.objects.create(
            user=user,
            title=cleaned_title,
            content=cleaned_content,
            position=0 if lowest is None else lowest - 1,
            is_free=free,
            paid_through=None if free else now + timedelta(days=config.renewal_days),
            charged_credits=cost,
            updated_at=now,
        )
        if chosen:
            note.tags.set(chosen)
        versions.record(note, Editor.for_user(note, user))
    return note


def check_can_create(
    user: User, *, title: str, content: str = '', tags: Iterable[UUID | str] = ()
) -> None:
    """The refusals `create_note` would make for the fields and the cap, without writing or
    locking anything: the web checks them before asking the person to confirm a cost, so credits are
    never asked for a note that can't be made. (A race for the last slot is still decided by
    `create_note` under the lock.)"""
    clean_title(title)
    clean_content(content)
    _own_tags(user, tags)
    config = NotesSettings.load()
    if Note.objects.filter(user=user, deleted_at__isnull=True).count() >= config.max_notes_per_user:
        raise _error(NON_FIELD_ERRORS, TOO_MANY_NOTES_MESSAGE)


def check_duplicable(note: Note) -> None:
    """Refuse copying a note that was taken down (read afresh: a takedown is final, but the
    instance may predate it)."""
    if note.is_taken_down or Note.objects.filter(pk=note.pk, taken_down_at__isnull=False).exists():
        raise _error(NON_FIELD_ERRORS, COPY_TAKEN_DOWN_MESSAGE)


def duplicate(
    note: Note, user: User, *, expected_cost: int | None = None, keep_title: bool = False
) -> Note:
    """A new note in `user`'s account with `note`'s content and no tags, share links or history. It
    is a normal note (free if a slot is open, else the creation price). The title is the source's
    when `keep_title` (a visitor's copy of a shared note), else `<title> (copy)`. A note staff took
    down can't be copied."""
    check_duplicable(note)
    title = note.title if keep_title else _copy_title(note.title)
    return create_note(user, title=title, content=note.content, expected_cost=expected_cost)


# -- Saving --------------------------------------------------------------------------------------


def save_note(
    note: Note,
    *,
    editor: Editor,
    title: str | None = None,
    content: str | None = None,
    base_version: int,
    link: ShareLink | None = None,
    coalesce: bool = True,
) -> Note:
    """Change the title and/or content: the one function that does, for the owner and for share
    link editors. `base_version` is the `Note.version` the editor opened; a different stored one is
    a `NoteConflictError` (nothing written). Nothing changed is not a save: the note is returned
    as it is. Returns the saved note (re-read under the lock). `coalesce=False` always records a new
    version (see `versions.record`); the web passes it for "Save mine anyway", so the text it
    overwrote stays in the history.

    `link` is the share link the save came through (an editable one): it is re-checked under the
    locks (`LinkUnavailableError`), its `last_edited_at` is set and, unless the owner made the
    change, the owner gets the "edited through a shared link" notice.

    Raises:
        ValidationError: the note is read-only, or a field is invalid.
        NoteConflictError, LinkUnavailableError
    """
    if link is not None and editor.link is None:
        editor = Editor(editor.kind, editor.user, link)
    new_title = clean_title(title) if title is not None else None
    new_content = clean_content(content) if content is not None else None
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        require_writable(locked)
        used_link = _lock_editing_link(editor.link, locked) if editor.link is not None else None
        next_title = locked.title if new_title is None else new_title
        next_content = locked.content if new_content is None else new_content
        if next_title == locked.title and next_content == locked.content:
            return locked
        if base_version != locked.version:
            raise NoteConflictError(
                current_version=locked.version,
                current_title=locked.title,
                current_content=locked.content,
            )
        before = (locked.title, locked.content)
        locked.title = next_title
        locked.content = next_content
        locked.version += 1
        locked.updated_at = timezone.now()
        locked.save(update_fields=['title', 'content', 'version', 'updated_at'])
        versions.record(locked, editor, coalesce=coalesce, before=before)
        if used_link is not None:
            _after_link_edit(locked, note.user, editor, used_link)
    return locked


def toggle_checklist(
    note: Note, index: int, checked: bool, *, editor: Editor, link: ShareLink | None = None
) -> Note:
    """Tick or untick the `index`th checklist box of the content as it is now (read under the row
    lock, so two boxes ticked at once both survive; no `base_version`). Records a version like a
    save; `link` works as in `save_note`. Returns the note."""
    if link is not None and editor.link is None:
        editor = Editor(editor.kind, editor.user, link)
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        require_writable(locked)
        used_link = _lock_editing_link(editor.link, locked) if editor.link is not None else None
        try:
            content = toggle_checklist_item(locked.content, index, checked, profile='extended')
        except IndexError:
            raise _error('index', 'That checklist item no longer exists.') from None
        if content == locked.content:
            return locked
        before = (locked.title, locked.content)
        locked.content = content
        locked.version += 1
        locked.updated_at = timezone.now()
        locked.save(update_fields=['content', 'version', 'updated_at'])
        versions.record(locked, editor, before=before)
        if used_link is not None:
            _after_link_edit(locked, note.user, editor, used_link)
    return locked


def restore_version(note: Note, version: NoteVersion) -> Note:
    """Make an earlier version the note's content again (owner only: a new save, so the history
    keeps what it replaced). Returns the note."""
    if version.note_id != note.pk:
        raise _error(NON_FIELD_ERRORS, 'That version does not belong to this note.')
    current = Note.objects.values_list('version', flat=True).get(pk=note.pk)
    return save_note(
        note,
        editor=Editor.for_user(note, note.user),
        title=version.title,
        content=version.content,
        base_version=current,
        coalesce=False,
    )


# -- Tags, archive, Trash ------------------------------------------------------------------------


def set_tags(note: Note, tag_ids: Iterable[UUID | str]) -> Note:
    """Replace the note's tags with these (the user's own). Doesn't move `updated_at`."""
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        require_writable(locked)
        locked.tags.set(_own_tags(note.user, tag_ids))
    return locked


def archive(note: Note) -> Note:
    """Hide the note from the main list (its links, if any, keep working). Idempotent."""
    return _set_archived(note, timezone.now())


def unarchive(note: Note) -> Note:
    return _set_archived(note, None)


def _set_archived(note: Note, value) -> Note:
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        require_writable(locked)
        if (locked.archived_at is None) != (value is None):
            locked.archived_at = value
            locked.save(update_fields=['archived_at'])
    return locked


def trash(note: Note) -> Note:
    """Move the note to the Trash (always allowed, even locked). Trashing a free note frees its
    slot. Idempotent."""
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        if locked.deleted_at is None:
            locked.deleted_at = timezone.now()
            locked.save(update_fields=['deleted_at'])
    return locked


def _restore_plan(
    note: Note, config: NotesSettings, slot_open: bool | None = None
) -> tuple[int, bool, bool]:
    """`(cost, becomes_paid, renews)` of restoring `note` now: a free note with no free slot open
    becomes paid and costs the creation price; a paid note whose renewal date has passed is renewed
    for the renewal price; else it costs nothing. `slot_open` (whether the owner has a free slot
    open) is looked up when not given; a list of many notes passes it once."""
    now = timezone.now()
    if note.is_free:
        if slot_open is None:
            slot_open = _slot_open(note.user, config)
        if slot_open:
            return 0, False, False
        return config.creation_price, True, False
    if note.paid_through is not None and note.paid_through <= now:
        return config.renewal_price, False, True
    return 0, False, False


def quote_restore(note: Note) -> int:
    """What restoring the trashed `note` would cost now (see `_restore_plan`)."""
    return _restore_plan(note, NotesSettings.load())[0]


def free_slot_open(user: User, config: NotesSettings) -> bool:
    """Whether the user has a free slot open (one query)."""
    return _slot_open(user, config)


def restore_cost_for(note: Note, config: NotesSettings, slot_open: bool) -> int:
    """What restoring the trashed `note` costs, given the settings and whether its owner has a free
    slot open (`free_slot_open`): `quote_restore` without its queries, for a list of notes."""
    return _restore_plan(note, config, slot_open)[0]


def restore(note: Note, expected_cost: int | None = None) -> Note:
    """Take the note out of the Trash. A free note with no free slot left becomes a paid note
    (creation price, paid through `renewal_days`); a paid note whose renewal date passed while it
    was in the Trash is renewed (renewal price) and unlocked. `expected_cost` works as in
    `create_note`. The cap on notes applies. Returns the note.

    Raises:
        ValidationError, CostChangedError, apps.billing.services.InsufficientCreditsError
    """
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        config = NotesSettings.load()
        locked = _locked_note(note)
        if locked.deleted_at is None:
            return locked
        if Note.objects.filter(user=note.user, deleted_at__isnull=True).count() >= (
            config.max_notes_per_user
        ):
            raise _error(NON_FIELD_ERRORS, TOO_MANY_NOTES_MESSAGE)
        cost, becomes_paid, renews = _restore_plan(locked, config)
        if expected_cost is not None and expected_cost != cost:
            raise CostChangedError(cost)
        was_locked = locked.locked_at is not None
        now = timezone.now()
        if cost:
            description = (
                renewal_description(locked.title) if renews else _description('Notes', locked.title)
            )
            spend_credits(note.user, cost, source='notes', description=description)
        fields = ['deleted_at']
        if becomes_paid or renews:
            if becomes_paid:
                locked.is_free = False
            locked.paid_through = now + timedelta(days=config.renewal_days)
            locked.locked_at = None
            fields += ['is_free', 'paid_through', 'locked_at']
            if becomes_paid:
                locked.charged_credits += cost
                fields.append('charged_credits')
        locked.deleted_at = None
        locked.save(update_fields=fields)
        if was_locked and renews:
            # The renewal it skipped is paid: the "locked" notice is stale.
            notices.clear_locked(note.user_id, locked.pk)
    return locked


def delete_forever(note: Note) -> None:
    """Delete a note that is in the Trash, with its versions. Nothing is refunded."""
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        if locked.deleted_at is None:
            raise _error(NON_FIELD_ERRORS, 'Only a note in the Trash can be deleted for good.')
        was_locked = locked.locked_at is not None
        note_pk, user_id = locked.pk, locked.user_id
        locked.delete()
        if was_locked:
            notices.clear_locked(user_id, note_pk)


def purge_trash() -> int:
    """Delete the notes trashed more than `NotesSettings.trash_days` ago (the daily job), in
    batches of 200. One bad row is logged and skipped. Returns how many were deleted."""
    cutoff = timezone.now() - timedelta(days=NotesSettings.load().trash_days)
    deleted = 0
    failed: set[UUID] = set()
    while True:
        batch = list(
            Note.objects.filter(deleted_at__lt=cutoff)
            .exclude(pk__in=failed)
            .order_by('deleted_at')
            .values_list('pk', 'user_id', 'locked_at')[:200]
        )
        if not batch:
            return deleted
        for pk, user_id, locked_at in batch:
            try:
                with transaction.atomic():
                    lock_account(user_id)
                    # Re-checked under the lock: a note restored meanwhile stays.
                    count, _ = Note.objects.filter(pk=pk, deleted_at__lt=cutoff).delete()
                    deleted += 1 if count else 0
                    if count and locked_at is not None:
                        notices.clear_locked(user_id, pk)
            except Exception:
                failed.add(pk)
                logger.exception('Could not purge note %s', pk)


# -- Manual order --------------------------------------------------------------------------------


def _renumber(user: User) -> None:
    """Give all the user's notes (the Trash's too: a restored note keeps its place) the distinct
    positions 0, 1, 2, ... in their current manual order. Called inside the lock when notes share a
    position, which would make a dragged order impossible to write."""
    notes = list(Note.objects.filter(user=user).order_by('position', '-created_at', 'pk'))
    changed = []
    for index, note in enumerate(notes):
        if note.position != index:
            note.position = index
            changed.append(note)
    Note.objects.bulk_update(changed, ['position'], batch_size=500)


def reorder(user: User, ids: Sequence[UUID]) -> int:
    """Put the user's notes with these ids in the order given: of the ids that are the user's
    non-trashed notes, take their current positions, sort them and hand them out in the order sent
    (so they never move onto a position a note outside the request holds). Other ids are ignored
    (the notes not listed keep their places). If the listed notes share a position, all the user's
    notes are renumbered first (same order, distinct positions). Returns how many were
    positioned."""
    wanted = list(dict.fromkeys(ids))
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)

        def load() -> list[Note]:
            own = {
                note.pk: note
                for note in Note.objects.filter(user=user, pk__in=wanted, deleted_at__isnull=True)
            }
            return [own[pk] for pk in wanted if pk in own]

        ordered = load()
        positions = sorted(note.position for note in ordered)
        if len(set(positions)) != len(positions):
            _renumber(user)
            ordered = load()
            positions = sorted(note.position for note in ordered)
        changed = []
        for note, position in zip(ordered, positions, strict=True):
            if note.position != position:
                note.position = position
                changed.append(note)
        Note.objects.bulk_update(changed, ['position'])
    return len(ordered)
