"""Share links (issue #5): `<BASE_URL>/q/<code>` shows a QR code to anyone who has the address, and
lets them download it. Sharing is free.

The code is a capability: `secrets.token_urlsafe(16)` (22 characters), never reused, never
editable. A link is **active** while it is not revoked and has not expired; it **works**
(`is_available`) only while its QR code is also not deleted or taken down (`services.takedown`) and
its owner's account can still sign in. The link shows the QR code *as it is
now*: editing the code changes what the link shows, deleting it stops every link. Revoking is
idempotent and final: a revoked link is never re-activated (make another).

Refusals are `django.core.exceptions.ValidationError`s keyed by field (`__all__` for none). Lock
order in every write: the `QRCode` row, then the link.
"""

import secrets
from datetime import date, datetime, time
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User as UserModel
from apps.accounts.services.timezones import local_datetime, user_tz

from ..limits import LINK_LABEL_MAX, MAX_ACTIVE_LINKS_PER_CODE
from ..models import QRCode, QRShareLink, QRShareRecipient
from .errors import QRCodeTakenDownError
from .text import clean_line

if TYPE_CHECKING:
    from apps.accounts.models import User

#: Means "leave the expiry as it is" in `update_link` (`None` would mean "no expiry").
UNCHANGED = object()

CODE_BYTES = 16
#: The longest code a public URL is looked up with (a real one is 22 characters).
MAX_CODE_LENGTH = 64
DELETED_MESSAGE = 'This QR code was deleted.'
COPY_TAKEN_DOWN_MESSAGE = 'This QR code was taken down and cannot be copied.'
REVOKED_MESSAGE = 'This link was revoked and can no longer be changed.'
PAST_DATE_MESSAGE = 'Choose a date that has not passed.'
CAP_MESSAGE = f'A QR code can have at most {MAX_ACTIVE_LINKS_PER_CODE} active links.'


def owner_of(qrcode: QRCode) -> 'User':
    """The QR code's owner (`created_by`, typed: a plain foreign key is opaque to `ty`)."""
    return cast('User', qrcode.created_by)


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def link_path(link: QRShareLink) -> str:
    """The site-relative address of a link: `/q/<code>`."""
    return reverse('q:page', args=[link.code])


def link_url(link: QRShareLink) -> str:
    """The public address of a link: `<BASE_URL>/q/<code>` (never read from a request)."""
    return f'{settings.BASE_URL.rstrip("/")}{link_path(link)}'


# -- Fields --------------------------------------------------------------------------------------


def clean_label(label: object) -> str:
    cleaned = clean_line(label)
    if len(cleaned) > LINK_LABEL_MAX:
        raise _error('label', f'Keep the label to {LINK_LABEL_MAX} characters or fewer.')
    return cleaned


def expiry_instant(qrcode: QRCode, expires_on: date) -> datetime:
    """The end of the day `expires_on` in the owner's zone, as an instant (UTC)."""
    return local_datetime(owner_of(qrcode), expires_on, time(23, 59, 59))


def _expiry_instant_of(qrcode: QRCode, value: date | datetime) -> datetime:
    """The instant an expiry given as a date (the end of that day in the owner's zone) or as a
    date-time (that instant; one with no UTC offset is read in the owner's zone) means."""
    if isinstance(value, datetime):
        if timezone.is_naive(value):  # the owner's wall clock, by `local_datetime`'s rule
            return local_datetime(owner_of(qrcode), value.date(), value.time())
        return value
    return expiry_instant(qrcode, value)


def _clean_expiry(qrcode: QRCode, expires_on: date | datetime | None) -> datetime | None:
    if expires_on is None:
        return None
    instant = _expiry_instant_of(qrcode, expires_on)
    if instant <= timezone.now():
        raise _error('expires_on', PAST_DATE_MESSAGE)
    return instant


def expiry_date(link: QRShareLink) -> date | None:
    """The day a link's expiry falls on, in the owner's zone (what the date field shows)."""
    if link.expires_at is None:
        return None
    return link.expires_at.astimezone(user_tz(owner_of(link.qrcode))).date()


def _expiry_changes(link: QRShareLink, expires_on: date | datetime) -> bool:
    """Whether `expires_on` would change the link's expiry: the day it already expires on is no
    change (so an expired link's label can be edited), nor is the same instant."""
    if isinstance(expires_on, datetime):
        return _expiry_instant_of(link.qrcode, expires_on) != link.expires_at
    return expires_on != expiry_date(link)


def require_shareable(qrcode: QRCode) -> None:
    """Refuse creating or changing a QR code's links while it is deleted (the owner's pages never
    reach a deleted code, but the services are also called with one held across a delete) or
    taken down by staff (`QRCodeTakenDownError`, final). Revoking is never refused."""
    if qrcode.deleted_at is not None:
        raise ValidationError(DELETED_MESSAGE)
    if qrcode.is_taken_down:
        raise QRCodeTakenDownError


# -- Reading -------------------------------------------------------------------------------------


def active_condition(now: datetime | None = None) -> Q:
    """The `Q` of an active link: not revoked, no expiry or one still ahead."""
    return Q(revoked_at__isnull=True) & (
        Q(expires_at__isnull=True) | Q(expires_at__gt=now or timezone.now())
    )


def active_links(qrcode: QRCode) -> QuerySet:
    return QRShareLink.objects.filter(active_condition(), qrcode=qrcode)


def with_active_link_flag(queryset: QuerySet) -> QuerySet:
    """Annotate each QR code with `has_active_link` (one subquery, no query per row), which the
    dashboard uses for its "Shared" icon. A taken-down code's links don't work, so it is not
    "shared"."""
    return queryset.annotate(
        has_active_link=Exists(
            QRShareLink.objects.filter(active_condition(), qrcode=OuterRef('pk')).only('pk')
        )
        & Q(taken_down_at__isnull=True)
    )


def state(link: QRShareLink, now: datetime | None = None) -> str:
    """`revoked`, `expired` or `active`."""
    if link.is_revoked:
        return 'revoked'
    return 'expired' if link.is_expired(now) else 'active'


def list_links(qrcode: QRCode) -> list[QRShareLink]:
    """The QR code's links for the owner's panel: active ones first (newest first), then the
    revoked and expired ones. Each carries `url`, `state` and `expiry_value` (the ISO date a date
    field is prefilled with), none of which is stored. The people each link was emailed to are
    prefetched (`link.recipients.all()`, oldest first): two queries for the whole list."""
    now = timezone.now()
    links = list(
        QRShareLink.objects.filter(qrcode=qrcode)
        .prefetch_related(
            Prefetch('recipients', queryset=QRShareRecipient.objects.order_by('created_at', 'pk'))
        )
        .order_by('-created_at', 'pk')
    )
    for link in links:
        link.qrcode = qrcode  # already loaded: no query per link
        link.url = link_url(link)
        link.state = state(link, now)
        expires_on = expiry_date(link)
        link.expiry_value = expires_on.isoformat() if expires_on else ''
        link.expires_field_id = f'expires-{link.pk}'
    return sorted(links, key=lambda link: link.state != 'active')


def is_available(link: QRShareLink) -> bool:
    """Whether the link works now: it is active, its QR code is not deleted or taken down and the
    owner's account can still sign in (`is_active` and an `ACTIVE` status: deactivating or deleting an
    account leaves `is_active` alone). (`link.qrcode` and its owner are read: load them with
    `select_related('qrcode__created_by')`.)"""
    owner = owner_of(link.qrcode)
    owner_ok = bool(owner.is_active) and owner.status == UserModel.STATUS_ACTIVE
    qrcode = link.qrcode
    code_ok = qrcode.deleted_at is None and not qrcode.is_taken_down
    return link.is_active() and code_ok and owner_ok


def get_available(code: str) -> QRShareLink | None:
    """The link with this code with its QR code (and the code's owner) loaded, if it works now;
    else `None` -- for every reason alike, so a caller can't tell them apart. Codes are
    case-sensitive."""
    if not code or len(code) > MAX_CODE_LENGTH:
        return None
    link = (
        QRShareLink.objects.select_related('qrcode', 'qrcode__created_by').filter(code=code).first()
    )
    if link is None or not is_available(link):
        return None
    return link


# -- Writing -------------------------------------------------------------------------------------


def _new_code() -> str:
    return secrets.token_urlsafe(CODE_BYTES)


def _check_cap(qrcode: QRCode, *, exclude: QRShareLink | None = None) -> None:
    active = active_links(qrcode)
    if exclude is not None:
        active = active.exclude(pk=exclude.pk)
    if active.count() >= MAX_ACTIVE_LINKS_PER_CODE:
        raise _error(NON_FIELD_ERRORS, CAP_MESSAGE)


def check_create(
    qrcode: QRCode, *, label: object = '', expires_on: date | datetime | None = None
) -> None:
    """The refusals `create_link` would make for the code's state, the fields and the cap, with no
    locks and no writes: the web checks them before it charges the rate limit. (`create_link`
    decides again under the locks.)"""
    require_shareable(qrcode)
    clean_label(label)
    _clean_expiry(qrcode, expires_on)
    _check_cap(qrcode)


def check_update(
    link: QRShareLink,
    *,
    label: object = None,
    expires_on: date | datetime | None | object = UNCHANGED,
    clear_expiry: bool = False,
) -> None:
    """The refusals `update_link` would make for the code's state and the fields, with no locks
    and no writes (see `check_create`)."""
    qrcode = link.qrcode
    require_shareable(qrcode)
    if link.is_revoked:
        raise _error(NON_FIELD_ERRORS, REVOKED_MESSAGE)
    if label is not None:
        clean_label(label)
    clearing = clear_expiry or expires_on is None
    changing = not clearing and isinstance(expires_on, date) and _expiry_changes(link, expires_on)
    if changing:
        assert isinstance(expires_on, date)
        _clean_expiry(qrcode, expires_on)
    if not link.is_active() and (clearing or changing):
        # An expired link made active again counts against the cap (as `update_link` decides).
        _check_cap(qrcode, exclude=link)


def _locked_qrcode(qrcode: QRCode) -> QRCode:
    """The QR code row, locked (`select_for_update`), with its owner as given."""
    locked = QRCode.objects.select_for_update().get(pk=qrcode.pk)
    locked.created_by = qrcode.created_by
    return locked


def create_link(
    qrcode: QRCode, *, label: object = '', expires_on: date | datetime | None = None
) -> QRShareLink:
    """Make a new share link for the QR code.

    `expires_on` is a date (the link stops working at the end of that day in the owner's zone) or,
    for the API, a date-time (that instant; with no UTC offset it is read in the owner's zone); it
    must not be in the past. At most `MAX_ACTIVE_LINKS_PER_CODE` links are active at once.

    Raises:
        ValidationError: the QR code was deleted, a field is invalid or the cap is reached.
    """
    with transaction.atomic():
        locked = _locked_qrcode(qrcode)
        require_shareable(locked)
        cleaned_label = clean_label(label)
        expires_at = _clean_expiry(locked, expires_on)
        _check_cap(locked)
        for _ in range(5):
            try:
                with transaction.atomic():
                    return QRShareLink.objects.create(
                        qrcode=locked,
                        code=_new_code(),
                        label=cleaned_label,
                        expires_at=expires_at,
                    )
            except IntegrityError:  # a clash of two random 128-bit codes: try another
                continue
    raise RuntimeError('Could not generate a free share link code')  # pragma: no cover


def locked_link(link: QRShareLink) -> tuple[QRCode, QRShareLink]:
    """The link's QR code and the link, locked in that order."""
    qrcode = _locked_qrcode(link.qrcode)
    fresh = QRShareLink.objects.select_for_update().get(pk=link.pk)
    fresh.qrcode = qrcode
    return qrcode, fresh


def update_link(
    link: QRShareLink,
    *,
    label: object = None,
    expires_on: date | datetime | None | object = UNCHANGED,
    clear_expiry: bool = False,
) -> QRShareLink:
    """Change a link's label or expiry (`None` leaves the label as it is; `clear_expiry` removes
    the expiry; `expires_on` is a date as in `create_link`, and the date the link already expires
    on is no change, so an expired link's label can be edited). A revoked link can't be changed;
    extending an expired one makes it active again and counts against the cap.

    Raises:
        ValidationError: the QR code was deleted, the link was revoked, a field is invalid or the
            cap is reached.
    """
    with transaction.atomic():
        qrcode, fresh = locked_link(link)
        require_shareable(qrcode)
        if fresh.is_revoked:
            raise _error(NON_FIELD_ERRORS, REVOKED_MESSAGE)
        fields: list[str] = []
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
            if _expiry_changes(fresh, expires_on):
                fresh.expires_at = _clean_expiry(qrcode, expires_on)
                fields.append('expires_at')
        if not was_active and fresh.is_active():
            _check_cap(qrcode, exclude=fresh)
        if fields:
            fresh.save(update_fields=fields)
    return fresh


def revoke_link(link: QRShareLink) -> QRShareLink:
    """Revoke the link: it stops working for good (idempotent; a revoked link is never
    re-activated). **Always allowed, whatever the QR code's state** (later also when it is taken
    down), and never rate limited: revoking only reduces what is exposed, so an owner who learns
    that a link leaked can kill it. (Creating and changing links keep their refusals.)"""
    with transaction.atomic():
        _, fresh = locked_link(link)
        if fresh.revoked_at is None:
            fresh.revoked_at = timezone.now()
            fresh.save(update_fields=['revoked_at'])
    return fresh
