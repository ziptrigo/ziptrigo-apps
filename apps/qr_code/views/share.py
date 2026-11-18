"""The owner's share panel: a QR code's links, a form for a new one, editing and revoking (issue #5).

The QR app has no detail page, so the panel is a dialog: the dashboard's row menu (and the editor,
in edit mode) fetches it into `#qr-share-modal` (`GET share/<id>/`, the shared `fragmentModal`), and
each action re-renders its content (`#qr-share-panel`). A refusal is the usual 422 with `Label:
message` lines in `#qr-share-msg`. Without htmx every action is a plain POST that flashes a message
and goes back to the page it came from (the editor, else the dashboard) with the dialog open
(`?share=<id>`). Changes spend `QR_SHARE_USER`,
validated first and charged after. **Emailing a link** (`share_email`) and **resending** to a person
(`share_resend`) spend `QR_SHARE_EMAIL_USER` instead, one hit per email, charged by the service
once nothing else can refuse the request (`services.share_emails`); over it the 429 says how long
to wait. A deleted code has no dialog (it is a 404), so nothing here reaches
one. **Revoking is never rate limited** (World Time's event delete is the same): an owner who learns
that a link leaked must be able to kill it right after a burst of changes. Another user's code or
link is a 404.
"""

from functools import partial
from urllib.parse import urlsplit
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core import ratelimit
from apps.core.field_errors import ErrorLines, form_lines
from apps.core.field_errors import validation_lines as core_validation_lines
from apps.core.htmx import is_htmx

from ..forms import ShareEmailForm, ShareLinkForm
from ..limits import MAX_ACTIVE_LINKS_PER_CODE, MAX_RECIPIENTS_PER_LINK, SHARE_MESSAGE_MAX
from ..models import QRCode, QRShareLink, QRShareRecipient
from ..services import share, share_emails

SHARE_MSG = '#qr-share-msg'
FIELD_LABELS = {
    'label': 'Label',
    'expires_on': 'Expires on',
    'emails': 'Email addresses',
    'message': 'Message',
}


def dashboard_share_url(qrcode: QRCode) -> str:
    """The dashboard with the share dialog open."""
    return f'{reverse("qr_code:dashboard")}?share={qrcode.pk}'


def back_url(request: HttpRequest, qrcode: QRCode) -> str:
    """Where a plain POST goes back to, with the dialog open: the QR code's editor when the POST
    came from it (the referring page's path is compared with the editor's, so nothing the client
    sends is ever redirected to), else the dashboard."""
    editor = reverse('qr_code:edit', args=[qrcode.pk])
    referer = request.META.get('HTTP_REFERER', '')
    if referer and urlsplit(referer).path == editor:
        return f'{editor}?share={qrcode.pk}'
    return dashboard_share_url(qrcode)


def owned_qrcode(request: AuthenticatedHttpRequest, qr_id: UUID) -> QRCode:
    """The requester's QR code `qr_id`, or a 404 (another user's, a deleted one, an unknown id)."""
    return get_object_or_404(
        QRCode.objects.select_related('created_by'),
        id=qr_id,
        created_by=request.user,
        deleted_at__isnull=True,
    )


def _owned_link(qrcode: QRCode, link_id: UUID) -> QRShareLink:
    link = get_object_or_404(QRShareLink, pk=link_id, qrcode=qrcode)
    link.qrcode = qrcode  # the code (and its owner) are already loaded
    return link


def validation_lines(exc: ValidationError) -> ErrorLines:
    """`Label: message` per error of a service `ValidationError`, non-field errors plain first."""
    return core_validation_lines(exc, FIELD_LABELS)


def _owned_recipient(link: QRShareLink, recipient_id: UUID) -> QRShareRecipient:
    recipient = get_object_or_404(QRShareRecipient, pk=recipient_id, link=link)
    recipient.link = link  # the link, its code and the owner are already loaded
    return recipient


def panel_context(qrcode: QRCode, notice: str = '') -> dict:
    items = share.list_links(qrcode)
    active_count = sum(1 for link in items if link.state == 'active')
    return {
        'notice': notice,
        'can_email': bool(share.owner_of(qrcode).email_confirmed),
        'max_recipients': MAX_RECIPIENTS_PER_LINK,
        'message_max': SHARE_MESSAGE_MAX,
        'qrcode': qrcode,
        'links': items,
        'active_count': active_count,
        # Staff took the code down: nothing can be made, changed or emailed, only revoked, and no
        # link works (so the dashboard's "Shared" icon goes).
        'taken_down': qrcode.is_taken_down,
        'shared': bool(active_count) and not qrcode.is_taken_down,
        'max_links': MAX_ACTIVE_LINKS_PER_CODE,
        'today': user_today(share.owner_of(qrcode)).isoformat(),
    }


def _panel(
    request: HttpRequest, qrcode: QRCode, *, template: str = 'share_panel', notice: str = ''
) -> HttpResponse:
    return render(request, f'qr_code/partials/{template}.html', panel_context(qrcode, notice))


def _done(
    request: AuthenticatedHttpRequest, qrcode: QRCode, message: str, *, notice: bool = False
) -> HttpResponse:
    """The panel again for htmx (with `message` as a notice in its message slot when `notice`); a
    flash and back to the page it came from otherwise."""
    if is_htmx(request):
        return _panel(request, qrcode, notice=message if notice else '')
    messages.success(request, message)
    return redirect(back_url(request, qrcode))


def _refused(request: AuthenticatedHttpRequest, qrcode: QRCode, lines: list[str]) -> HttpResponse:
    if is_htmx(request):
        response = render(
            request, 'qr_code/partials/share_messages.html', {'messages_list': lines}, status=422
        )
        response['HX-Retarget'] = SHARE_MSG
        response['HX-Reswap'] = 'innerHTML'
        return response
    for line in lines:
        messages.error(request, line)
    return redirect(back_url(request, qrcode))


def _limited(request: AuthenticatedHttpRequest) -> HttpResponse | None:
    """The 429 response when the user is over `QR_SHARE_USER`, else None. Charges one hit."""
    result = ratelimit.hit_user(request.user, 'QR_SHARE_USER')
    if result.allowed:
        return None
    return ratelimit.web_response(request, result, retarget=SHARE_MSG)


@login_required
@require_GET
def share_panel(request: AuthenticatedHttpRequest, qr_id: UUID) -> HttpResponse:
    """The dialog (htmx). Opened directly it goes to the dashboard with the dialog open."""
    qrcode = owned_qrcode(request, qr_id)
    if not is_htmx(request):
        return redirect(back_url(request, qrcode))
    return _panel(request, qrcode, template='share_modal')


@login_required
@require_POST
def share_create(request: AuthenticatedHttpRequest, qr_id: UUID) -> HttpResponse:
    qrcode = owned_qrcode(request, qr_id)
    form = ShareLinkForm(request.POST)
    if not form.is_valid():
        return _refused(request, qrcode, form_lines(form))
    label = form.cleaned_data['label']
    expires_on = form.expiry()
    try:  # validated first, charged after: a refused request spends no budget
        share.check_create(qrcode, label=label, expires_on=expires_on)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    if (response := _limited(request)) is not None:
        return response
    try:
        share.create_link(qrcode, label=label, expires_on=expires_on)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    return _done(request, qrcode, 'Link created.')


@login_required
@require_POST
def share_edit(request: AuthenticatedHttpRequest, qr_id: UUID, link_id: UUID) -> HttpResponse:
    """Change a link's label or expiry. A field missing from the POST is left alone; a posted
    empty `expires_on` removes the expiry."""
    qrcode = owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    form = ShareLinkForm(request.POST)
    if not form.is_valid():
        return _refused(request, qrcode, form_lines(form))
    changes: dict = {'label': form.cleaned_data['label'] if 'label' in request.POST else None}
    if 'expires_on' in request.POST:
        expires_on = form.expiry()
        if expires_on is None:
            changes['clear_expiry'] = True
        else:
            changes['expires_on'] = expires_on
    try:
        share.check_update(link, **changes)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    if (response := _limited(request)) is not None:
        return response
    try:
        share.update_link(link, **changes)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    return _done(request, qrcode, 'Link updated.')


@login_required
@require_POST
def share_revoke(request: AuthenticatedHttpRequest, qr_id: UUID, link_id: UUID) -> HttpResponse:
    """Revoke a link for good (from the confirmation modal, a plain POST). Always allowed for the
    owner: revoking only reduces what is exposed, so it spends no rate-limit budget."""
    qrcode = owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    share.revoke_link(link)
    return _done(request, qrcode, 'Link revoked.')


def _email_limited(request: AuthenticatedHttpRequest, exc: ratelimit.RateLimitExceeded):
    """The 429 for a send or resend that doesn't fit the user's hourly budget: the message says
    how long to wait."""
    result = ratelimit.RateLimitResult(allowed=False, remaining=0, retry_after=exc.retry_after)
    return ratelimit.web_response(request, result, exc.message, retarget=SHARE_MSG)


def _people(count: int) -> str:
    return '1 person' if count == 1 else f'{count} people'


@login_required
@require_POST
def share_email(request: AuthenticatedHttpRequest, qr_id: UUID, link_id: UUID) -> HttpResponse:
    """Email a link to the addresses typed (and an optional message). Every refusal comes before
    the budget is charged (`services.share_emails.send_link`); the budget is
    `QR_SHARE_EMAIL_USER`, one hit per email."""
    qrcode = owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    form = ShareEmailForm(request.POST)
    if not form.is_valid():
        return _refused(request, qrcode, form_lines(form))
    try:
        people = share_emails.send_link(
            link,
            form.addresses(),
            message=form.cleaned_data['message'],
            charge=partial(share_emails.charge_share_emails, request.user),
        )
    except ratelimit.RateLimitExceeded as exc:
        return _email_limited(request, exc)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    return _done(request, qrcode, f'Emailed the link to {_people(len(people))}.', notice=True)


@login_required
@require_POST
def share_resend(
    request: AuthenticatedHttpRequest, qr_id: UUID, link_id: UUID, recipient_id: UUID
) -> HttpResponse:
    """Email one person the link again (without a message), subject to the cooldown."""
    qrcode = owned_qrcode(request, qr_id)
    link = _owned_link(qrcode, link_id)
    recipient = _owned_recipient(link, recipient_id)
    try:
        share_emails.resend(
            recipient, charge=partial(share_emails.charge_share_emails, request.user)
        )
    except ratelimit.RateLimitExceeded as exc:
        return _email_limited(request, exc)
    except ValidationError as exc:
        return _refused(request, qrcode, validation_lines(exc))
    return _done(request, qrcode, f'Sent the email to {recipient.email} again.', notice=True)
