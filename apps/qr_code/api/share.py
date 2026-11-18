"""The share-link endpoints (`/api/qr/{id}/links`): the links of one of the caller's QR codes and
the emails they were sent in (issue #5). Each calls the service the website's Share dialog uses.

Status codes: **400** a service refusal (`field: message`, or the message alone for one that
belongs to no field); **404** another user's QR code, link or recipient, or a deleted code (never
403, so an id can't be probed); **409** staff took the QR code down (final: it can no longer be
shared or emailed; reading its links and revoking them still work); **422** ninja's own
validation (a malformed id, an unknown key in a body: every body is `extra='forbid'`); **429**
the site-wide rate limiter, with `Retry-After`.

Rate limits are the web's, so the budgets are shared: creating and changing a link spend
`QR_SHARE_USER`, charged only after the refusals that need no lock (`check_create` /
`check_update`) and just before the write, so a refused request spends nothing, and a `PATCH`
that would change nothing spends nothing either; emailing and resending spend `QR_SHARE_EMAIL_USER`, one hit per email,
which the service charges once nothing else can refuse the request. **Revoking is never rate
limited** and is allowed whatever the code's state.
"""

from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.core import ratelimit

from ..models import QRCode, QRShareLink, QRShareRecipient
from ..schemas import (
    QRCodeShareEmailSchema,
    QRCodeShareErrorSchema,
    QRCodeShareLinkCreateSchema,
    QRCodeShareLinkSchema,
    QRCodeShareLinkUpdateSchema,
)
from ..services import QRCodeTakenDownError, share, share_emails
from .qrcode import router

_API_FIELDS = {'expires_on': 'expires_at'}  # the services' field names that differ from the API's


def _detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: `field: message` pairs (the field named as the
    API's request body names it) when the service keyed its errors by field, a message that
    belongs to no field plain."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            message
            if field_name == '__all__'
            else f'{_API_FIELDS.get(field_name, field_name)}: {message}'
            for field_name, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


@contextmanager
def _mapped_errors() -> Generator[None]:
    """Turn the services' `ValidationError`s into statuses. A taken-down code comes first (409,
    caught by type, never by its text); the rest are 400s."""
    try:
        yield
    except QRCodeTakenDownError as exc:
        raise HttpError(409, _detail(exc)) from exc
    except ValidationError as exc:
        raise HttpError(400, _detail(exc)) from exc


def _owned_qrcode(request, qr_id: UUID) -> QRCode:
    """The caller's QR code, or a 404 (another user's, a deleted one, an unknown id)."""
    return get_object_or_404(
        QRCode.objects.select_related('created_by'),
        id=qr_id,
        created_by=request.auth,
        deleted_at__isnull=True,
    )


_ERRORS = {
    400: QRCodeShareErrorSchema,
    404: QRCodeShareErrorSchema,
    409: QRCodeShareErrorSchema,
}


def _people() -> Prefetch:
    return Prefetch('recipients', queryset=QRShareRecipient.objects.order_by('created_at', 'pk'))


def _owned_link(qrcode: QRCode, link_id: UUID) -> QRShareLink:
    """The code's link, with its people, or a 404 (another code's, an unknown id)."""
    link = get_object_or_404(
        QRShareLink.objects.prefetch_related(_people()), pk=link_id, qrcode=qrcode
    )
    link.qrcode = qrcode  # the code (and its owner) are already loaded
    return link


def _data(link: QRShareLink) -> dict:
    return {
        'id': link.pk,
        'url': share.link_url(link),
        'label': link.label,
        'expires_at': link.expires_at,
        'revoked_at': link.revoked_at,
        'active': link.is_active(),
        'state': share.state(link),
        'recipients': [
            {'id': person.pk, 'email': person.email, 'last_sent_at': person.last_sent_at}
            for person in link.recipients.all()
        ],
        'created_at': link.created_at,
    }


def _again(qrcode: QRCode, link: QRShareLink) -> dict:
    """The link as it is after a write, with its people read afresh."""
    return _data(_owned_link(qrcode, link.pk))


def _charge(request) -> None:
    """Spend one hit of the caller's `QR_SHARE_USER` budget: a 429 when it is over."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'QR_SHARE_USER'))


@router.get(
    '/{uuid:qr_id}/links',
    response={200: list[QRCodeShareLinkSchema], 404: QRCodeShareErrorSchema},
    auth=JWTAuth(),
)
def list_links(request, qr_id: UUID):
    """The QR code's share links: the active ones first (newest first), then the revoked and
    expired ones. Works for a code staff took down (none of its links work then)."""
    qrcode = _owned_qrcode(request, qr_id)
    return [_data(link) for link in share.list_links(qrcode)]


@router.post(
    '/{uuid:qr_id}/links', response={201: QRCodeShareLinkSchema, **_ERRORS}, auth=JWTAuth()
)
def create_link(request, qr_id: UUID, payload: QRCodeShareLinkCreateSchema):
    """Make a share link: anyone with the address (`url`) can see the QR code and download it.
    `expires_at` is a date (the end of that day in your time zone) or a date-time, not in the
    past. At most 10 links per QR code are active at once (400). A QR code staff took down cannot
    be shared (409)."""
    qrcode = _owned_qrcode(request, qr_id)
    with _mapped_errors():
        share.check_create(qrcode, label=payload.label, expires_on=payload.expires_at)
        _charge(request)
        link = share.create_link(qrcode, label=payload.label, expires_on=payload.expires_at)
    return 201, _again(qrcode, link)


def _real_changes(link: QRShareLink, changes: dict) -> dict:
    """The part of `changes` that would alter the link (the services' own no-op rules)."""
    real: dict = {}
    if 'label' in changes and share.clean_label(changes['label']) != link.label:
        real['label'] = changes['label']
    if changes.get('clear_expiry') and link.expires_at is not None:
        real['clear_expiry'] = True
    if 'expires_on' in changes and share.expiry_changes(link, changes['expires_on']):
        real['expires_on'] = changes['expires_on']
    return real


@router.patch(
    '/{uuid:qr_id}/links/{uuid:link_id}',
    response={200: QRCodeShareLinkSchema, **_ERRORS},
    auth=JWTAuth(),
)
def update_link(request, qr_id: UUID, link_id: UUID, payload: QRCodeShareLinkUpdateSchema):
    """Change a link's `label` or `expires_at`; only the fields present are applied, and an
    explicit `null` clears either. A revoked link cannot be changed (400); the code never
    changes. A body that would change nothing (nothing in it, the same label, the expiry it
    already has, `null` for a link with no expiry) answers the link and spends no budget."""
    qrcode = _owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    fields = payload.model_dump(exclude_unset=True)
    changes: dict = {}
    if 'label' in fields:
        changes['label'] = fields['label'] or ''
    if 'expires_at' in fields:
        if fields['expires_at'] is None:
            changes['clear_expiry'] = True
        else:
            changes['expires_on'] = fields['expires_at']
    if changes:
        with _mapped_errors():
            share.check_update(link, **changes)
            real = _real_changes(link, changes)
            if real:
                _charge(request)
                share.update_link(link, **real)
        if real:
            link = _owned_link(qrcode, link_id)
    return _data(link)


@router.delete(
    '/{uuid:qr_id}/links/{uuid:link_id}',
    response={204: None, 404: QRCodeShareErrorSchema},
    auth=JWTAuth(),
)
def revoke_link(request, qr_id: UUID, link_id: UUID):
    """Revoke a link: it stops working for good (idempotent, never rate limited, and allowed
    whatever the QR code's state: a leaked link can always be killed). A revoked link is never
    active again; make another."""
    qrcode = _owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    share.revoke_link(link)
    return 204, None


@router.post(
    '/{uuid:qr_id}/links/{uuid:link_id}/email',
    response={200: QRCodeShareLinkSchema, **_ERRORS},
    auth=JWTAuth(),
)
def email_link(request, qr_id: UUID, link_id: UUID, payload: QRCodeShareEmailSchema):
    """Email the link to up to 20 people (in all, per link) with an optional message. Needs a
    confirmed email address on your account. An address already on the link is a resend and obeys
    the 10-minute cooldown. Spends `QR_SHARE_EMAIL_USER`, one hit per email; a request that does
    not fit the budget emails nobody (429). Every refusal (400, 409) spends nothing."""
    qrcode = _owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    with _mapped_errors():
        share_emails.send_link(
            link,
            payload.emails,
            message=payload.message,
            charge=partial(share_emails.charge_share_emails, request.auth),
        )
    return _again(qrcode, link)


@router.post(
    '/{uuid:qr_id}/links/{uuid:link_id}/recipients/{uuid:recipient_id}/resend',
    response={200: QRCodeShareLinkSchema, **_ERRORS},
    auth=JWTAuth(),
)
def resend_to_recipient(request, qr_id: UUID, link_id: UUID, recipient_id: UUID):
    """Email one person the link again (without a message), subject to the 10-minute cooldown and
    the same budget as `email`."""
    qrcode = _owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    recipient = get_object_or_404(QRShareRecipient, pk=recipient_id, link=link)
    recipient.link = link  # the link, its code and the owner are already loaded
    with _mapped_errors():
        share_emails.resend(
            recipient, charge=partial(share_emails.charge_share_emails, request.auth)
        )
    return _again(qrcode, link)
