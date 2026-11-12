"""Share links (issue #260, PR 4): `<BASE_URL>/n/<code>` shows a note to anyone who has the address,
read-only or (for an editable link) with its content changeable.

The code is a capability: `secrets.token_urlsafe(16)` (22 characters), never reused, never
editable. A link is **active** while it is not revoked and has not expired; it **works**
(`is_available`) only while its note is also out of the Trash, not locked and not taken down.
Revoking is idempotent and final: a revoked link is never re-activated (make another).

Refusals are `django.core.exceptions.ValidationError`s keyed by field (`__all__` for none). Lock
order in every write, as everywhere in Notes: the owner's `NotesAccount` row, then the note, then
the link.
"""

import base64
import secrets
from datetime import date, datetime, time
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services.timezones import local_datetime, user_tz
from apps.core.services.qr_image import render_png

from ..limits import LINK_LABEL_MAX
from ..models import LinkMode, Note, NotesSettings, ShareLink
from .accounts import get_account, lock_account
from .notes import _error, _locked_note, require_writable

#: Means "leave the expiry as it is" in `update_link` (`None` would mean "no expiry").
UNCHANGED = object()

CODE_BYTES = 16
#: The longest code a public URL is looked up with (a real one is 22 characters).
MAX_CODE_LENGTH = 64
TAKEN_DOWN_SHARE_MESSAGE = 'This note was taken down and cannot be shared.'
REVOKED_MESSAGE = 'This link was revoked and can no longer be changed.'
PAST_DATE_MESSAGE = 'Choose a date that has not passed.'
QR_SCALE = 6
QR_BORDER = 2


def link_path(link: ShareLink) -> str:
    """The site-relative address of a link: `/n/<code>`."""
    return reverse('n:page', args=[link.code])


def link_url(link: ShareLink) -> str:
    """The public address of a link: `<BASE_URL>/n/<code>` (never read from a request)."""
    return f'{settings.BASE_URL.rstrip("/")}{link_path(link)}'


@lru_cache(maxsize=256)
def qr_data_uri(url: str) -> str:
    """The `data:` URI of a black-on-white PNG QR code of `url`, rendered in memory and stored
    nowhere. Cached per URL: a code never changes what it encodes."""
    png = render_png(url, scale=QR_SCALE, border=QR_BORDER)
    return f'data:image/png;base64,{base64.b64encode(png).decode("ascii")}'


# -- Fields --------------------------------------------------------------------------------------


def clean_mode(mode: object) -> str:
    if mode not in LinkMode.values:
        raise _error('mode', 'Choose who can do what with the link.')
    return str(mode)


def clean_label(label: object) -> str:
    cleaned = ' '.join(str(label or '').split())
    if len(cleaned) > LINK_LABEL_MAX:
        raise _error('label', f'Keep the label to {LINK_LABEL_MAX} characters or fewer.')
    return cleaned


def expiry_instant(note: Note, expires_on: date) -> datetime:
    """The end of the day `expires_on` in the owner's zone, as an instant (UTC)."""
    return local_datetime(note.user, expires_on, time(23, 59, 59))


def _clean_expiry(note: Note, expires_on: date | None) -> datetime | None:
    if expires_on is None:
        return None
    instant = expiry_instant(note, expires_on)
    if instant <= timezone.now():
        raise _error('expires_on', PAST_DATE_MESSAGE)
    return instant


def expiry_date(link: ShareLink) -> date | None:
    """The day a link's expiry falls on, in the owner's zone (what the date field shows)."""
    if link.expires_at is None:
        return None
    return link.expires_at.astimezone(user_tz(link.note.user)).date()


def require_shareable(note: Note) -> None:
    """Refuse creating or changing a note's links while it is read-only (in the Trash, locked or
    taken down; a takedown is checked before the lock). Revoking is exempt: see `revoke_link`."""
    if note.is_taken_down:
        raise _error(NON_FIELD_ERRORS, TAKEN_DOWN_SHARE_MESSAGE)
    require_writable(note)


# -- Reading -------------------------------------------------------------------------------------


def active_condition(now: datetime | None = None) -> Q:
    """The `Q` of an active link: not revoked, no expiry or one still ahead."""
    return Q(revoked_at__isnull=True) & (
        Q(expires_at__isnull=True) | Q(expires_at__gt=now or timezone.now())
    )


def active_links(note: Note) -> QuerySet:
    return ShareLink.objects.filter(active_condition(), note=note)


def has_active_link(note: Note) -> bool:
    return active_links(note).exists()


def share_flags(note: Note, has_active: bool) -> dict[str, bool]:
    """What the note page and the share panel show about sharing, given whether the note has an
    active link: `can_share` (create and edit links: the note is not read-only), `links_work` (the
    "Shared" chip: an active link *and* the note readable through it) and `show_share` (the Share
    button: also for a read-only note that still has an active link, so the owner can revoke it)."""
    can_share = not note.is_read_only
    return {
        'can_share': can_share,
        'has_active_link': has_active,
        'links_work': has_active and can_share,
        'show_share': can_share or has_active,
    }


def shared_condition(now: datetime | None = None) -> Q:
    """The `Q` of a note that is **shared**: it has an active link *and* the note is readable
    through it (not locked, not taken down; search leaves the Trash out anyway). The same meaning
    as `share_flags`' `links_work` (the "Shared" chip), for the `shared` filter of a search. An
    `Exists`, not a join, so a note with several links is one row and a list's counts aren't
    multiplied."""
    active = ShareLink.objects.filter(active_condition(now), note=OuterRef('pk')).only('pk')
    return Q(Exists(active)) & Q(locked_at__isnull=True, taken_down_at__isnull=True)


def with_active_link_flag(queryset: QuerySet) -> QuerySet:
    """Annotate each note with `has_active_link` (one subquery, no query per row), which the list
    rows use for their share icon."""
    return queryset.annotate(
        has_active_link=Exists(
            ShareLink.objects.filter(active_condition(), note=OuterRef('pk')).only('pk')
        )
    )


def state(link: ShareLink, now: datetime | None = None) -> str:
    """`revoked`, `expired` or `active`."""
    if link.is_revoked:
        return 'revoked'
    return 'expired' if link.is_expired(now) else 'active'


def list_links(note: Note) -> list[ShareLink]:
    """The note's links for the owner's panel: active ones first (newest first), then the revoked
    and expired ones. Each carries `url`, `state` and `expiry_value` (the ISO date a date field
    is prefilled with), none of which is stored."""
    now = timezone.now()
    links = list(ShareLink.objects.filter(note=note).order_by('-created_at', 'pk'))
    for link in links:
        link.note = note  # already loaded: no query per link
        link.url = link_url(link)
        link.state = state(link, now)
        expires_on = expiry_date(link)
        link.expiry_value = expires_on.isoformat() if expires_on else ''
        link.expires_field_id = f'expires-{link.pk}'
    return sorted(links, key=lambda link: link.state != 'active')


def is_available(link: ShareLink) -> bool:
    """Whether the link works now: it is active and its note is out of the Trash, not locked and
    not taken down. (`link.note` is read: load it with `select_related`.)"""
    note = link.note
    return (
        link.is_active() and not note.is_trashed and not note.is_locked and not note.is_taken_down
    )


def get_available(code: str) -> ShareLink | None:
    """The link with this code with its note (and the note's owner) loaded, if it works now; else
    `None` -- for every reason alike, so a caller can't tell them apart. Codes are case-sensitive."""
    if not code or len(code) > MAX_CODE_LENGTH:
        return None
    link = ShareLink.objects.select_related('note', 'note__user').filter(code=code).first()
    if link is None or not is_available(link):
        return None
    return link


# -- Writing -------------------------------------------------------------------------------------


def _new_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def _check_cap(note: Note, config: NotesSettings, *, exclude: ShareLink | None = None) -> None:
    active = active_links(note)
    if exclude is not None:
        active = active.exclude(pk=exclude.pk)
    if active.count() >= config.max_links_per_note:
        raise _error(
            NON_FIELD_ERRORS, f'A note can have at most {config.max_links_per_note} active links.'
        )


def check_create(
    note: Note, *, mode: object, label: object = '', expires_on: date | None = None
) -> None:
    """The refusals `create_link` would make for the note's state, the fields and the cap, with no
    locks and no writes: the web checks them before it charges the rate limit. (`create_link`
    decides again under the locks.)"""
    require_shareable(note)
    clean_mode(mode)
    clean_label(label)
    _clean_expiry(note, expires_on)
    _check_cap(note, NotesSettings.load())


def check_update(
    link: ShareLink,
    *,
    mode: object = None,
    label: object = None,
    expires_on: date | None | object = UNCHANGED,
    clear_expiry: bool = False,
) -> None:
    """The refusals `update_link` would make for the note's state and the fields, with no locks
    and no writes (see `check_create`)."""
    note = link.note
    require_shareable(note)
    if link.is_revoked:
        raise _error(NON_FIELD_ERRORS, REVOKED_MESSAGE)
    if mode is not None:
        clean_mode(mode)
    if label is not None:
        clean_label(label)
    clearing = clear_expiry or expires_on is None
    changing = not clearing and isinstance(expires_on, date) and expires_on != expiry_date(link)
    if changing:
        assert isinstance(expires_on, date)
        _clean_expiry(note, expires_on)
    if not link.is_active() and (clearing or changing):
        # An expired link made active again counts against the cap (as `update_link` decides).
        _check_cap(note, NotesSettings.load(), exclude=link)


def create_link(
    note: Note, *, mode: object, label: object = '', expires_on: date | None = None
) -> ShareLink:
    """Make a new share link for the note.

    `expires_on` is a date: the link stops working at the end of that day in the owner's zone, and
    must not be in the past. At most `max_links_per_note` links are active at once.

    Raises:
        ValidationError: the note is read-only, a field is invalid or the cap is reached.
    """
    get_account(note.user)
    with transaction.atomic():
        lock_account(note.user_id)
        locked = _locked_note(note)
        locked.user = note.user
        require_shareable(locked)
        cleaned_mode = clean_mode(mode)
        cleaned_label = clean_label(label)
        expires_at = _clean_expiry(locked, expires_on)
        _check_cap(locked, NotesSettings.load())
        for _ in range(5):
            try:
                with transaction.atomic():
                    return ShareLink.objects.create(
                        note=locked,
                        code=_new_code(),
                        mode=cleaned_mode,
                        label=cleaned_label,
                        expires_at=expires_at,
                    )
            except IntegrityError:  # a clash of two random 128-bit codes: try another
                continue
    raise RuntimeError('Could not generate a free share link code')  # pragma: no cover


def _locked_link(link: ShareLink) -> tuple[Note, ShareLink]:
    """The link's note and the link, locked in that order (call after `lock_account`)."""
    note = _locked_note(link.note)
    note.user = link.note.user
    fresh = ShareLink.objects.select_for_update().get(pk=link.pk)
    fresh.note = note
    return note, fresh


def update_link(
    link: ShareLink,
    *,
    mode: object = None,
    label: object = None,
    expires_on: date | None | object = UNCHANGED,
    clear_expiry: bool = False,
) -> ShareLink:
    """Change a link's mode, label or expiry (`None` leaves a field as it is; `clear_expiry` removes
    the expiry; `expires_on` is a date as in `create_link`, and the date the link already expires on
    is no change, so an expired link's label can be edited). A revoked link can't be changed;
    extending an expired one makes it active again and counts against the cap.

    Raises:
        ValidationError: the note is read-only, the link was revoked, a field is invalid or the cap
            is reached.
    """
    get_account(link.note.user)
    with transaction.atomic():
        lock_account(link.note.user_id)
        note, fresh = _locked_link(link)
        require_shareable(note)
        if fresh.is_revoked:
            raise _error(NON_FIELD_ERRORS, REVOKED_MESSAGE)
        fields: list[str] = []
        if mode is not None:
            cleaned_mode = clean_mode(mode)
            if cleaned_mode != fresh.mode:
                fresh.mode = cleaned_mode
                fields.append('mode')
        if label is not None:
            cleaned_label = clean_label(label)
            if cleaned_label != fresh.label:
                fresh.label = cleaned_label
                fields.append('label')
        was_active = fresh.is_active()
        if clear_expiry or expires_on is None:
            if fresh.expires_at is not None:
                fresh.expires_at = None
                fields.append('expires_at')
        elif expires_on is not UNCHANGED:
            assert isinstance(expires_on, date)
            if expires_on != expiry_date(fresh):
                fresh.expires_at = _clean_expiry(note, expires_on)
                fields.append('expires_at')
        if not was_active and fresh.is_active():
            _check_cap(note, NotesSettings.load(), exclude=fresh)
        if fields:
            fresh.save(update_fields=fields)
    return fresh


def revoke_link(link: ShareLink) -> ShareLink:
    """Revoke the link: it stops working for good (idempotent; a revoked link is never
    re-activated). **Always allowed, whatever the note's state** (locked, in the Trash, taken
    down): revoking only reduces what is exposed, so an owner who learns that a link leaked can
    kill it even while the note is read-only. (Creating and changing links keep their refusals.)
    """
    get_account(link.note.user)
    with transaction.atomic():
        lock_account(link.note.user_id)
        note, fresh = _locked_link(link)
        if fresh.revoked_at is None:
            fresh.revoked_at = timezone.now()
            fresh.save(update_fields=['revoked_at'])
    return fresh


__all__ = [
    'MAX_CODE_LENGTH',
    'UNCHANGED',
    'active_condition',
    'active_links',
    'check_create',
    'check_update',
    'clean_label',
    'clean_mode',
    'create_link',
    'expiry_date',
    'expiry_instant',
    'get_available',
    'has_active_link',
    'is_available',
    'link_path',
    'link_url',
    'list_links',
    'qr_data_uri',
    'require_shareable',
    'revoke_link',
    'share_flags',
    'shared_condition',
    'state',
    'update_link',
    'with_active_link_flag',
]
