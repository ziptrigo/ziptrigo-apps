"""Emailing a share link (issue #5): the people a link was sent to (`QRShareRecipient` rows), the
email each gets, and resending it. Sending is free for the owner in credits but bounded per user
(`QR_SHARE_EMAIL_USER`, one hit per email queued, charged through the `charge` callback the web
view and the API bind to the user) and per person (`RESEND_COOLDOWN`).

`send_link` and `resend` make **every refusal before they charge**, so a refused request spends no
budget, in this order: the link works; the owner's account is active and its **email is confirmed**
(an unconfirmed account must not email strangers); the addresses (valid, de-duplicated ignoring
case, at most `MAX_RECIPIENTS_PER_LINK` people in all); the message; the cooldown of an address
already on the link (typing one again is a resend). Then `charge(n)`, then one transaction with the
QR code and link rows locked: the rows are created, or `last_sent_at` is claimed with a conditional
`UPDATE` (two quick clicks email the person once), and one `send_share_email` task per address is
queued on commit. The task re-reads everything and sends nothing for a link that stopped working
or a person who is no longer on it. No notification and no copy to the owner: the recipient may
have no account.

Refusals are `ValidationError`s keyed by field (`emails`, `message`; `__all__` for none).
"""

import math
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.tasks import task
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core import ratelimit
from apps.core.datetime_format import format_utc
from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..limits import MAX_RECIPIENTS_PER_LINK, RESEND_COOLDOWN, SHARE_MESSAGE_MAX
from ..models import QRShareLink, QRShareRecipient
from . import share
from .text import clean_message

if TYPE_CHECKING:
    from apps.accounts.models import User

#: Spends `n` hits of the per-user budget (one per email about to be queued); raises
#: `ratelimit.RateLimitExceeded` at the first one over the limit.
ChargeEmails = Callable[[int], None]

SUBJECT = 'A QR code was shared with you'
NOT_WORKING_MESSAGE = 'This link no longer works.'
UNCONFIRMED_MESSAGE = 'Confirm your email address to send a QR code by email.'
NO_ADDRESS_MESSAGE = 'Enter at least one email address.'
CAP_MESSAGE = f'A link can be emailed to at most {MAX_RECIPIENTS_PER_LINK} people.'
NO_LONGER_MESSAGE = 'This person is no longer on the list.'


def charge_share_emails(user: 'User', count: int) -> None:
    """Spend `count` hits (one per email about to be queued) of the per-user `QR_SHARE_EMAIL_USER`
    budget, shared by the web and later the API. Raises `ratelimit.RateLimitExceeded` (with how
    long to wait) at the first hit over the limit; the ones already made stay spent. Bind it to the
    user (`functools.partial`) and pass it as `charge`: `send_link` and `resend` call it only once
    nothing else can refuse the request, so a refused one costs no budget."""
    for _ in range(count):
        result = ratelimit.hit_user(user, 'QR_SHARE_EMAIL_USER')
        if not result.allowed:
            raise ratelimit.RateLimitExceeded(
                retry_after=result.retry_after, message=ratelimit.retry_message(result.retry_after)
            )


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


# -- Checking ------------------------------------------------------------------------------------


def normalize_email(email: str) -> str:
    """An address as stored: surrounding space dropped, the domain lower-cased (the local part is
    kept as typed)."""
    return BaseUserManager.normalize_email(email.strip())


def clean_addresses(emails: Iterable[object]) -> list[str]:
    """The addresses as a list of valid ones: blanks skipped, repeats (ignoring case) dropped, the
    first spelling kept, the domain lower-cased. (A copy of file transfer's rule: products may not
    import each other.)

    Raises:
        ValidationError: no address, one that is not valid (named, cut to 80 characters) or more
            than `MAX_RECIPIENTS_PER_LINK` different ones (checked as it goes, so a huge list is
            refused without validating all of it).
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in emails:
        candidate = str(raw or '').strip()
        key = candidate.lower()
        if not candidate or key in seen:
            continue
        seen.add(key)
        try:
            validate_email(candidate)
        except ValidationError:
            raise _error('emails', f'"{candidate[:80]}" is not a valid email address.') from None
        cleaned.append(normalize_email(candidate))
        if len(cleaned) > MAX_RECIPIENTS_PER_LINK:
            raise _error('emails', CAP_MESSAGE)
    if not cleaned:
        raise _error('emails', NO_ADDRESS_MESSAGE)
    return cleaned


def clean_share_message(message: object) -> str:
    cleaned = clean_message(message)
    if len(cleaned) > SHARE_MESSAGE_MAX:
        raise _error('message', f'Keep the message to {SHARE_MESSAGE_MAX} characters or fewer.')
    return cleaned


def require_sendable(link: QRShareLink) -> None:
    """The link works and its owner may email people: refused (`__all__`) otherwise. `link.qrcode`
    and the code's owner are read (`select_related('qrcode__created_by')`)."""
    if not share.is_available(link):
        raise ValidationError(NOT_WORKING_MESSAGE)
    if not share.owner_of(link.qrcode).email_confirmed:
        raise ValidationError(UNCONFIRMED_MESSAGE)


def _cooldown_wait(recipient: QRShareRecipient, now=None) -> timedelta | None:
    """How long until `recipient` may be emailed again, or `None` when it may be now."""
    if recipient.last_sent_at is None:
        return None
    wait = recipient.last_sent_at + RESEND_COOLDOWN - (now or timezone.now())
    return wait if wait > timedelta(0) else None


def _cooldown_message(wait: timedelta, email: str | None = None) -> str:
    minutes = max(1, math.ceil(wait.total_seconds() / 60))
    unit = 'minute' if minutes == 1 else 'minutes'
    who = 'This person' if email is None else f'"{email[:80]}"'
    return f'{who} was emailed recently; you can resend in {minutes} {unit}.'


def _cooldown_error(wait: timedelta, email: str | None = None, *, field: str | None = None):
    message = _cooldown_message(wait, email)
    return _error(field, message) if field else ValidationError(message)


def _existing(link: QRShareLink) -> dict[str, QRShareRecipient]:
    return {
        recipient.email.lower(): recipient
        for recipient in QRShareRecipient.objects.filter(link=link)
    }


# -- Sending -------------------------------------------------------------------------------------


def _claim(recipient_id, now) -> bool:
    """Move `last_sent_at` to `now` if the person may be emailed (never, or past the cooldown):
    one conditional write, so of two requests that both passed the check only one wins."""
    return bool(
        QRShareRecipient.objects.filter(pk=recipient_id)
        .filter(Q(last_sent_at__isnull=True) | Q(last_sent_at__lte=now - RESEND_COOLDOWN))
        .update(last_sent_at=now)
    )


def send_link(
    link: QRShareLink,
    emails: Iterable[object],
    *,
    message: object = '',
    charge: ChargeEmails | None = None,
) -> list[QRShareRecipient]:
    """Email the share link to each address and remember who was emailed.

    An address already on the link is a resend and obeys the cooldown: if any address of the
    request is still in it the whole request is refused (nothing is sent or created), naming it.
    Returns the people emailed, in the order given. `charge(n)` is called once with the number of
    emails, after every check that needs no write.

    Raises:
        ValidationError: see the module docstring for the checks and their order.
        ratelimit.RateLimitExceeded: from `charge`.
    """
    require_sendable(link)
    addresses = clean_addresses(emails)
    cleaned_message = clean_share_message(message)
    existing = _existing(link)
    now = timezone.now()
    for address in addresses:
        known = existing.get(address.lower())
        wait = _cooldown_wait(known, now) if known else None
        if wait is not None:
            raise _cooldown_error(wait, address, field='emails')
    if _total_after(existing, addresses) > MAX_RECIPIENTS_PER_LINK:
        raise _error('emails', CAP_MESSAGE)
    if charge is not None:
        charge(len(addresses))
    return _write(link, addresses, cleaned_message)


def resend(recipient: QRShareRecipient, *, charge: ChargeEmails | None = None) -> QRShareRecipient:
    """Email one person the link again (without a message: the first email's isn't kept). The
    same refusals as `send_link`, in the same order, all before `charge`: the link works, the
    owner's email is confirmed, and the person was not emailed within `RESEND_COOLDOWN`.

    Raises:
        ValidationError: as `send_link`, with the cooldown on no field.
        ratelimit.RateLimitExceeded: from `charge`.
    """
    link = recipient.link
    require_sendable(link)
    wait = _cooldown_wait(recipient)
    if wait is not None:
        raise _cooldown_error(wait)
    if charge is not None:
        charge(1)
    with transaction.atomic():
        _, fresh = share.locked_link(link)
        require_sendable(fresh)
        now = timezone.now()
        if not _claim(recipient.pk, now):
            try:
                recipient.refresh_from_db()
            except QRShareRecipient.DoesNotExist:
                raise ValidationError(NO_LONGER_MESSAGE) from None
            raise _cooldown_error(_cooldown_wait(recipient) or RESEND_COOLDOWN)
        recipient.last_sent_at = now
        enqueue_after_commit(send_share_email, str(link.pk), recipient.email, '')
    return recipient


def _total_after(existing: dict[str, QRShareRecipient], addresses: list[str]) -> int:
    """How many people the link would have been emailed to once `addresses` are added."""
    return len(existing) + sum(1 for a in addresses if a.lower() not in existing)


def _write(link: QRShareLink, addresses: list[str], message: str) -> list[QRShareRecipient]:
    """The transaction of `send_link`: lock the code and the link, re-check what can have changed
    (the link, the cap and the cooldown, which another request may have moved), create or claim
    each person and queue their email on commit."""
    people: list[QRShareRecipient] = []
    with transaction.atomic():
        _, fresh = share.locked_link(link)
        require_sendable(fresh)
        existing = {r.email.lower(): r for r in QRShareRecipient.objects.filter(link=fresh)}
        if _total_after(existing, addresses) > MAX_RECIPIENTS_PER_LINK:
            raise _error('emails', CAP_MESSAGE)
        now = timezone.now()
        for address in addresses:
            person = existing.get(address.lower())
            if person is None:
                try:
                    with transaction.atomic():
                        person = QRShareRecipient.objects.create(
                            link=fresh, email=address, last_sent_at=now
                        )
                except IntegrityError:  # another request added the same address meanwhile
                    raise _cooldown_error(RESEND_COOLDOWN, address, field='emails') from None
            elif _claim(person.pk, now):
                person.last_sent_at = now
            else:
                person.refresh_from_db()
                wait = _cooldown_wait(person) or RESEND_COOLDOWN
                raise _cooldown_error(wait, address, field='emails')
            people.append(person)
            enqueue_after_commit(send_share_email, str(fresh.pk), person.email, message)
    return people


# -- The email -----------------------------------------------------------------------------------


def owner_name(owner: 'User') -> str:
    """The owner as a recipient reads them: the account name, never the email (it would show an
    address the owner never chose to share)."""
    return (owner.name or '').strip() or 'Someone'


@task
def send_share_email(link_id: str, email: str, message: str = '') -> None:
    """ "<owner> shared the QR code ... with you" -- to one person, at the first send, on an
    address typed again and on a resend. The subject is fixed (nothing the owner typed in it); the
    owner's name, the code's name and the message are in the body, autoescaped in the HTML.
    Nothing is sent when the link no longer works, the owner's email is no longer confirmed or the
    person is no longer on the link."""
    link = (
        QRShareLink.objects.select_related('qrcode', 'qrcode__created_by')
        .filter(pk=link_id)
        .first()
    )
    if link is None or not link.recipients.filter(email__iexact=email).exists():
        return
    try:
        require_sendable(link)
    except ValidationError:
        return
    context = {
        'owner_name': owner_name(share.owner_of(link.qrcode)),
        'name': link.qrcode.name,
        'message': message,
        'link': share.link_url(link),
        'expires_at': f'{format_utc(link.expires_at)} UTC' if link.expires_at else '',
    }
    send_email(
        to=email,
        subject=SUBJECT,
        text_body=render_to_string('qr_code/emails/share.txt', context),
        html_body=render_to_string('qr_code/emails/share.html', context),
    )
