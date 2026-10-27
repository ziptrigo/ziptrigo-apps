"""Creating, changing and ending short links.

The web views and (later) the JWT API call these, so both enforce the same rules. Every function
raises Django's `ValidationError` (see `errors`), and `InsufficientCreditsError` from
`apps.billing.services` when an extra can't be paid for; nothing is left half done in either case
(each runs in one transaction).

**Cost confirmation.** `create_link` and `set_enabled` take `expected_cost`, the total the caller
showed the user and had confirmed. The cost is worked out again inside the transaction, after the
owner's row is locked, and `CostChangedError` (nothing charged, nothing changed) is raised when it
differs: another request may have used up the free allowance in between, or a price changed.
`None` means "accept whatever it costs", for a caller with no one to confirm with (the API).
"""

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.queue import enqueue_after_commit

from ..models import LinkStatus, ShortLink, ShortUrlSettings
from . import billing, blocklist, codes, limits, passwords
from .destinations import apply_utm, destination_host, validate_destination
from .errors import (
    AliasTakenError,
    CostChangedError,
    LinkHeldError,
    OwnerInactiveError,
    field_error,
)
from .titles import fetch_title

MAX_TAGS = 20
MAX_TAG_LENGTH = 32
MAX_TITLE_LENGTH = 255
MAX_CLICKS_LIMIT = 2_000_000_000

_TAG = re.compile(r'[a-z0-9][a-z0-9_-]*')


class _Unset:
    """Marks a field `update_link` was not given (None is a real value for an expiry)."""

    def __repr__(self) -> str:
        return 'UNSET'


UNSET: Any = _Unset()


def short_url(code: str) -> str:
    """The public address of a link: `<BASE_URL>/s/<code>`."""
    return f'{settings.BASE_URL.rstrip("/")}{settings.SHORT_URL_PATH}{code}'


# -- validation of the free-form fields ------------------------------------------------------


def normalize_tags(tags: str | Iterable[str] | None) -> list[str]:
    """Tags as stored: lower-cased, spaces turned into hyphens, duplicates dropped, order kept.

    A string is split on commas. A tag is ASCII letters, digits, hyphens and underscores, up to
    32 characters, and at most 20 are kept per link; anything else is a `ValidationError`.
    """
    if tags is None:
        return []
    items = tags.split(',') if isinstance(tags, str) else list(tags)
    result: list[str] = []
    for item in items:
        tag = re.sub(r'\s+', '-', str(item).strip().lower())
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH or not _TAG.fullmatch(tag):
            raise field_error(
                'tags',
                f'"{str(item).strip()[:40]}" is not a valid tag: use up to {MAX_TAG_LENGTH} '
                'letters, digits, hyphens or underscores.',
            )
        if tag not in result:
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise field_error('tags', f'Use at most {MAX_TAGS} tags.')
    return result


def validate_title(title: str | None) -> str:
    title = ' '.join((title or '').split())
    if len(title) > MAX_TITLE_LENGTH:
        raise field_error(
            'title', f'The title is too long (at most {MAX_TITLE_LENGTH} characters).'
        )
    return title


def validate_max_clicks(max_clicks: int | None) -> int | None:
    if max_clicks is None:
        return None
    if not 1 <= max_clicks <= MAX_CLICKS_LIMIT:
        raise field_error('max_clicks', f'Enter a number between 1 and {MAX_CLICKS_LIMIT:,}.')
    return max_clicks


def validate_expiry(expires_at: datetime | None, *, now: datetime | None = None) -> datetime | None:
    if expires_at is None:
        return None
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at)
    if expires_at <= (now or timezone.now()):
        raise field_error('expires_at', 'The expiry must be in the future.')
    return expires_at


def _destination_with_utm(
    destination: str, utm: dict[str, str] | None, *, current: str | None = None
) -> str:
    """Merge the UTM parameters first, then validate the result as a whole, and refuse a host on
    the block list (`BlockedDomainError`).

    `current` is the destination the link already has, when editing: a destination that is
    unchanged is not checked against the block list again, so an owner can still change the title
    of a link whose destination was blocked after the fact (the link itself is already refused at
    every public request, `is_available`). Pointing it somewhere new is checked.
    """
    result = validate_destination(apply_utm(validate_destination(destination), **(utm or {})))
    if result != current:
        blocklist.check_not_blocked(destination_host(result))
    return result


# -- state -----------------------------------------------------------------------------------


def is_available(link: ShortLink, *, now: datetime | None = None) -> bool:
    """Whether `/s/<code>` works right now: active, not held for review, not expired, not past
    its click cap and not pointing at a blocked host. A password doesn't make a link unavailable:
    the visitor is asked for it first (`views.password`). Every public view asks this one
    function and nothing else, so each of those states renders the same neutral page.

    The block list is a database query, so it is asked last, only of a link that passed every
    other check. That is one indexed query on the hottest path of the app, deliberately uncached
    for now (a cache would need invalidating on every `BlockedDomain` save and delete, or a block
    would not take effect at once). If redirect volume makes it matter, cache the active block set
    (or the answer per host) for a few seconds and clear it from `BlockedDomain.save`/`delete`."""
    now = now or timezone.now()
    if link.status != LinkStatus.ACTIVE or link.held_for_review_at is not None:
        return False
    if link.expires_at is not None and link.expires_at <= now:
        return False
    if link.max_clicks is not None and link.click_count >= link.max_clicks:
        return False
    return not blocklist.is_blocked(destination_host(link.destination))


def is_destination_blocked(link: ShortLink) -> bool:
    """Whether the link's destination host is on the block list (what the owner's page says when
    the link doesn't work for that reason)."""
    return blocklist.is_blocked(destination_host(link.destination))


def display_state(
    link: ShortLink, *, now: datetime | None = None, destination_blocked: bool = False
) -> str:
    """The state shown to the owner, most specific first: `taken_down`, `deleted`, `disabled`,
    `held`, `expired`, `capped`, `blocked` or `active`.

    `blocked` (the destination's host is on the block list) needs a database query, so it is
    reported only when the caller passes `destination_blocked` (the link's own page asks per link;
    the dashboard asks once per page, `blocklist.blocked_hosts`; the dashboard *tabs* are database
    filters and still count such a link under its stored status). A disabled link whose
    destination is blocked is `blocked` too, so its owner sees why before paying to enable it."""
    now = now or timezone.now()
    if link.status in (LinkStatus.TAKEN_DOWN, LinkStatus.DELETED):
        return str(link.status)
    if link.status == LinkStatus.DISABLED:
        return 'blocked' if destination_blocked else 'disabled'
    if link.held_for_review_at is not None:
        return 'held'
    if link.expires_at is not None and link.expires_at <= now:
        return 'expired'
    if link.max_clicks is not None and link.click_count >= link.max_clicks:
        return 'capped'
    if destination_blocked:
        return 'blocked'
    return 'active'


# -- create ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkInput:
    """The fields of a new link, as the web form and (later) the API collect them."""

    destination: str
    alias: str = ''
    title: str = ''
    tags: str | Iterable[str] | None = None
    expires_at: datetime | None = None
    max_clicks: int | None = None
    utm: dict[str, str] | None = None
    #: A password visitors must enter first (empty: none); a paid extra.
    password: str = ''
    #: Whether clicks from bots and link previews are left out of the counts (see `clicks`).
    filter_bots: bool = True


def _create_extras(
    owner: User, alias: str | None, settings_row: ShortUrlSettings, *, has_password: bool = False
) -> list[billing.Extra]:
    extras: list[billing.Extra] = []
    if alias and alias.strip():
        extras.append(billing.Extra.ALIAS)
    if has_password:
        extras.append(billing.Extra.PASSWORD)
    if limits.is_extra_link(owner, settings=settings_row):
        extras.append(billing.Extra.EXTRA_LINK)
    return extras


def quote_create(owner: User, alias: str | None, *, has_password: bool = False) -> billing.Quote:
    """What creating a link (with `alias` and a password, if any) would charge, to show before
    it is."""
    settings_row = ShortUrlSettings.load()
    extras = _create_extras(owner, alias, settings_row, has_password=has_password)
    return billing.quote(extras, settings_row)


def _lock_owner(owner: User) -> None:
    """Serialize one user's create/enable calls, so the active-link cap can't be raced past (a
    row lock on Postgres; SQLite has one writer at a time anyway). Also refuses an owner whose
    account is not active (`OwnerInactiveError`), read from the locked row."""
    status = User.objects.select_for_update().filter(pk=owner.pk).values_list('status', flat=True)
    if status.first() != User.STATUS_ACTIVE:
        raise OwnerInactiveError()


def _require_active_owner(owner_id: Any) -> None:
    """`OwnerInactiveError` unless the owner's account is active, read fresh and without a lock
    (for the paths that already hold the link's row lock: the owner's lock must come first)."""
    status = User.objects.filter(pk=owner_id).values_list('status', flat=True).first()
    if status != User.STATUS_ACTIVE:
        raise OwnerInactiveError()


def create_link(owner: User, data: LinkInput, *, expected_cost: int | None = None) -> ShortLink:
    """Create a link for `owner`: validate, pay for its extras and save it, in one transaction.

    `expected_cost` is the total the caller confirmed (see the module docstring); `None` accepts
    any cost. Raises `ValidationError` (a bad field, `AliasTakenError`, `LimitReachedError`,
    `CostChangedError`) or `InsufficientCreditsError`. A link made without a title gets one
    fetched in the background.
    """
    destination = _destination_with_utm(data.destination, data.utm)
    title = validate_title(data.title)
    tags = normalize_tags(data.tags)
    expires_at = validate_expiry(data.expires_at)
    max_clicks = validate_max_clicks(data.max_clicks)
    alias = (data.alias or '').strip()
    if alias:
        codes.validate_alias(alias)
    # Hashed before the transaction: a slow hash must not hold the owner's lock.
    password_hash = passwords.hash_password(data.password) if data.password else None

    with transaction.atomic():
        _lock_owner(owner)
        settings_row = ShortUrlSettings.load()
        limits.check_can_create(owner, settings=settings_row)
        extras = _create_extras(owner, alias, settings_row, has_password=bool(password_hash))
        if expected_cost is not None:
            cost = billing.quote(extras, settings_row)
            if cost.total != expected_cost:
                raise CostChangedError(cost)
        if alias and codes.is_retired(alias):
            raise AliasTakenError()

        code = alias or codes.generate_code()
        link = ShortLink(
            owner=owner,
            code=code,
            code_key=codes.normalize_key(code),
            is_alias=bool(alias),
            destination=destination,
            title=title,
            tags=tags,
            expires_at=expires_at,
            max_clicks=max_clicks,
            password_hash=password_hash,
            filter_bots=data.filter_bots,
        )
        # Not `objects.create`: two requests for the same alias race on the unique key, and the
        # loser must say "not available", not 500.
        try:
            with transaction.atomic():
                link.save(force_insert=True)
        except IntegrityError:
            if not alias:
                raise
            raise AliasTakenError() from None

        if alias:
            billing.charge_extra(link, billing.Extra.ALIAS, settings=settings_row)
        if password_hash:
            billing.charge_extra(link, billing.Extra.PASSWORD, settings=settings_row)
        if billing.Extra.EXTRA_LINK in extras:
            billing.charge_extra(link, billing.Extra.EXTRA_LINK, settings=settings_row)
        if not title:
            enqueue_after_commit(fetch_title, str(link.pk))
    return link


# -- change ----------------------------------------------------------------------------------


def _require_editable(link: ShortLink, *, allow_held: bool = False) -> None:
    """Refuse a link that is taken down or deleted, and (unless `allow_held`) one held for abuse
    review: a hold freezes every owner action except disabling it (`services.hold`)."""
    if link.status not in (LinkStatus.ACTIVE, LinkStatus.DISABLED):
        raise ValidationError('This link can no longer be changed.')
    if link.held_for_review_at is not None and not allow_held:
        raise LinkHeldError()


def update_link(
    link: ShortLink,
    *,
    destination: str = UNSET,
    utm: dict[str, str] | None = None,
    title: str = UNSET,
    tags: str | Iterable[str] | None = UNSET,
    expires_at: datetime | None = UNSET,
    max_clicks: int | None = UNSET,
    password: str | None = UNSET,
    filter_bots: bool = UNSET,
    expected_cost: int | None = None,
) -> ShortLink:
    """Change the given fields of `link` (a field left out is untouched; `None` clears an expiry,
    a click cap or the password), all or nothing. The code and the alias never change: that would
    free the old code for someone else. `utm` is merged into the destination (given or current).

    Only the fields that really differ are written (a new password always does: hashes are
    salted). Setting a password the link has never had pays the `password` extra, once: this is
    the only charge here, so `expected_cost` (see the module docstring; `None` accepts any) is
    that price or 0. Raises `ValidationError` (`CostChangedError` among them) or
    `InsufficientCreditsError`.
    """
    new_hash: str | None = UNSET
    if password is not UNSET:
        # Hashed before the transaction: a slow hash must not hold the link's row lock.
        new_hash = passwords.hash_password(password) if password is not None else None
    with transaction.atomic():
        _require_active_owner(link.owner_id)
        locked = ShortLink.objects.select_for_update().get(pk=link.pk)
        _require_editable(locked)
        # Diffed against the locked row, so "only write what differs" holds under concurrent
        # edits and the title decision below sees the fresh title.
        changes: dict[str, Any] = {}
        if destination is not UNSET or utm:
            base = locked.destination if destination is UNSET else destination
            new_destination = _destination_with_utm(base, utm, current=locked.destination)
            if new_destination != locked.destination:
                changes['destination'] = new_destination
        if title is not UNSET:
            changes['title'] = validate_title(title)
        if tags is not UNSET:
            changes['tags'] = normalize_tags(tags)
        if expires_at is not UNSET:
            if expires_at is not None and timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at)
            # An unchanged expiry that has since passed is not an error: only a new value is
            # checked.
            if expires_at is None or expires_at != locked.expires_at:
                changes['expires_at'] = validate_expiry(expires_at)
        if max_clicks is not UNSET:
            changes['max_clicks'] = validate_max_clicks(max_clicks)
        if new_hash is not UNSET:
            changes['password_hash'] = new_hash
        if filter_bots is not UNSET:
            changes['filter_bots'] = bool(filter_bots)
        changes = {name: value for name, value in changes.items() if getattr(locked, name) != value}

        owes_password = isinstance(new_hash, str) and not billing.is_paid(
            locked, billing.Extra.PASSWORD
        )
        if expected_cost is not None:
            cost = billing.quote([billing.Extra.PASSWORD] if owes_password else [])
            if cost.total != expected_cost:
                raise CostChangedError(cost)

        if changes:
            ShortLink.objects.filter(pk=link.pk).update(updated_at=timezone.now(), **changes)
            for name, value in changes.items():
                setattr(locked, name, value)
        for name in (
            'destination',
            'title',
            'tags',
            'expires_at',
            'max_clicks',
            'password_hash',
            'filter_bots',
        ):
            setattr(link, name, getattr(locked, name))
        if owes_password:
            billing.charge_extra(link, billing.Extra.PASSWORD)
        # A new destination makes an automatically fetched title stale, so look again; a title
        # the owner typed is kept (it is set, so nothing is fetched).
        if 'destination' in changes and not locked.title:
            enqueue_after_commit(fetch_title, str(link.pk))
    return link


def quote_update(link: ShortLink, *, password: str | None = UNSET) -> billing.Quote:
    """What `update_link(link, password=...)` would charge, to show before it does: the
    `password` price when a password is being set on a link that never paid for one."""
    if not password or password is UNSET or billing.is_paid(link, billing.Extra.PASSWORD):
        return billing.Quote()
    return billing.quote([billing.Extra.PASSWORD])


def set_enabled(link: ShortLink, enabled: bool, *, expected_cost: int | None = None) -> bool:
    """Disable or enable `link`. Returns whether anything changed (a link already in that state
    is a no-op). Enabling beyond the free allowance pays the extra link price once (never again
    for the same link); a disabled link already counts toward the hard cap, so it needs no check.

    `expected_cost` is the total the caller confirmed (see the module docstring); `None` accepts
    any cost. Raises `ValidationError` for a taken down or deleted link, an owner whose account
    is not active (`OwnerInactiveError`) or a destination on the block list (`BlockedDomainError`,
    checked before anything is charged), `CostChangedError`, and `InsufficientCreditsError`.
    Disabling needs neither of the last two checks.
    """
    with transaction.atomic():
        if enabled:
            _lock_owner(link.owner)
        else:
            # Disabling stays open to a deactivated owner (it only ever reduces what they run),
            # but still takes the lock so it serializes with their other calls.
            User.objects.select_for_update().filter(pk=link.owner_id).first()
        locked = ShortLink.objects.select_for_update().get(pk=link.pk)
        # Disabling is the one thing a hold still allows the owner.
        _require_editable(locked, allow_held=not enabled)
        link.status = locked.status
        target = LinkStatus.ACTIVE if enabled else LinkStatus.DISABLED
        if locked.status == target:
            return False
        if enabled:
            # A blocked destination never works, so enabling would only charge for nothing.
            blocklist.check_not_blocked(destination_host(locked.destination))

        settings_row = ShortUrlSettings.load()
        # Free for a link that already paid its way in (`charge_extra` skips it too).
        owes = (
            enabled
            and not billing.is_paid(locked, billing.Extra.EXTRA_LINK)
            and limits.is_extra_link(link.owner, settings=settings_row)
        )
        if expected_cost is not None:
            cost = billing.quote([billing.Extra.EXTRA_LINK] if owes else [], settings_row)
            if cost.total != expected_cost:
                raise CostChangedError(cost)
        changed = ShortLink.objects.filter(pk=link.pk, status=locked.status).update(
            status=target, updated_at=timezone.now()
        )
        if not changed:
            return False
        link.status = target
        if owes:
            billing.charge_extra(link, billing.Extra.EXTRA_LINK, settings=settings_row)
        return True


def quote_enable(link: ShortLink) -> billing.Quote:
    """What enabling `link` would charge (nothing for an active link, or one that already paid
    its way in), to show before it is."""
    if link.status != LinkStatus.DISABLED or billing.is_paid(link, billing.Extra.EXTRA_LINK):
        return billing.Quote()
    settings_row = ShortUrlSettings.load()
    if limits.is_extra_link(link.owner, settings=settings_row):
        return billing.quote([billing.Extra.EXTRA_LINK], settings_row)
    return billing.Quote()


def enable_costs(owner: User, links: Iterable[ShortLink]) -> dict[uuid.UUID, int]:
    """`quote_enable(...).total` for each of `links` (the owner's), with the settings and the
    owner's active count read once: for a page of rows."""
    settings_row = ShortUrlSettings.load()
    beyond_allowance = limits.is_extra_link(owner, settings=settings_row)
    extra_link_price = billing.price(billing.Extra.EXTRA_LINK, settings_row)
    return {
        link.pk: extra_link_price
        if beyond_allowance
        and link.status == LinkStatus.DISABLED
        and not billing.is_paid(link, billing.Extra.EXTRA_LINK)
        else 0
        for link in links
    }


def analytics_unlocked(link: ShortLink) -> bool:
    """Whether the link's detailed statistics are open: paid for, or free (a price of 0)."""
    return (
        billing.is_paid(link, billing.Extra.ANALYTICS)
        or billing.price(billing.Extra.ANALYTICS) == 0
    )


def unlock_free_analytics(link: ShortLink) -> bool:
    """`analytics_unlocked`, and while the price is 0 also records the unlock: the first time a
    free link's statistics are opened `analytics_paid_at` is stamped (a conditional update, so
    idempotent and race-free), as `charge_extra` does for the other extras at a price of 0. A
    later price increase then doesn't take away statistics the link already had."""
    if billing.is_paid(link, billing.Extra.ANALYTICS):
        return True
    if billing.price(billing.Extra.ANALYTICS) != 0:
        return False
    now = timezone.now()
    if ShortLink.objects.filter(pk=link.pk, analytics_paid_at__isnull=True).update(
        analytics_paid_at=now
    ):
        link.analytics_paid_at = now
    else:
        link.refresh_from_db(fields=['analytics_paid_at'])
    return True


def quote_analytics(link: ShortLink) -> billing.Quote:
    """What opening the detailed statistics would charge (nothing once paid, or when free)."""
    if analytics_unlocked(link):
        return billing.Quote()
    return billing.quote([billing.Extra.ANALYTICS])


def enable_analytics(link: ShortLink, *, expected_cost: int | None = None) -> bool:
    """Pay for the link's detailed statistics, once. Returns whether anything was charged or
    recorded (False when it was already open). Events are recorded from the first click, so the
    history is there the moment it is enabled.

    `expected_cost` is the total the caller confirmed (see the module docstring; `None` accepts
    any). Raises `ValidationError` for a taken down or deleted link or `CostChangedError`, and
    `InsufficientCreditsError`.
    """
    with transaction.atomic():
        _require_active_owner(link.owner_id)
        locked = ShortLink.objects.select_for_update().get(pk=link.pk)
        # Allowed while held: it changes nothing about the link, only what its owner can see.
        _require_editable(locked, allow_held=True)
        if locked.analytics_paid_at is not None:
            link.analytics_paid_at = locked.analytics_paid_at
            return False
        if expected_cost is not None:
            cost = billing.quote([billing.Extra.ANALYTICS])
            if cost.total != expected_cost:
                raise CostChangedError(cost)
        billing.charge_extra(link, billing.Extra.ANALYTICS)
        return True


def delete_link(link: ShortLink) -> bool:
    """Soft delete: the link stops working and leaves the owner's lists, but the row stays so its
    code is never issued again. Returns whether it was deleted (False if it already was). A link
    taken down by staff can't be deleted by its owner, nor can one held for abuse review
    (`LinkHeldError`: it stays as it is until staff decide)."""
    if link.status == LinkStatus.DELETED:
        return False
    now = timezone.now()
    changed = ShortLink.objects.filter(
        pk=link.pk,
        status__in=(LinkStatus.ACTIVE, LinkStatus.DISABLED),
        held_for_review_at__isnull=True,
    ).update(status=LinkStatus.DELETED, deleted_at=now, updated_at=now)
    if not changed:
        link.refresh_from_db()
        if link.status == LinkStatus.DELETED:
            return False
        if link.status in (LinkStatus.ACTIVE, LinkStatus.DISABLED):
            raise LinkHeldError()
        raise ValidationError('This link can no longer be changed.')
    link.status = LinkStatus.DELETED
    link.deleted_at = now
    return True
