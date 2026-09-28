"""The anonymous send flow (spec section 2 phase 2): the same picker-then-options-then-send page a
logged-in sender uses (`views.send`/`views.uploads`), but tied to the browser session instead of a
user, gated by `FileTransferSettings.anonymous_enabled`, and ending in email confirmation instead
of an immediate send.

One page (`send_page`) renders whichever step the session's current anonymous transfer is in --
`DRAFT` (upload + options), `PENDING_CONFIRMATION` (code entry) or `ACTIVE` (redirects to the sent
page) -- so there's one URL to come back to across reloads. The upload/confirm/resend actions are
separate endpoints, mirroring `views.uploads`/`views.send`, just session- rather than login-owned.
"""

import json
import math

from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.htmx import hx_redirect
from apps.core.services.email_verification import (
    EmailVerificationError,
    EmailVerificationSendFailed,
    ResendTooSoon,
)

from .. import services
from ..forms import AnonymousSendOptionsForm, ConfirmCodeForm
from ..models import FileTransferSettings, Transfer, TransferStatus
from ..services.client_ip import client_ip
from ..services.storage import PART_SIZE_BYTES


class AnonymousHttpRequest(HttpRequest):
    """`HttpRequest` typed with `.session` -- these views never require login."""

    session: SessionBase


def _anonymous_enabled() -> bool:
    return FileTransferSettings.load().anonymous_enabled


def _not_available(request: HttpRequest) -> HttpResponse:
    """Anonymous sending is switched off (spec section 1: "can be turned off globally"). A plain
    404, like a URL that doesn't exist -- there's no reason to advertise the feature toggle."""
    return render(request, 'file_transfer/anon_disabled.html', status=404)


def _cookie_id(request: HttpRequest) -> str:
    return services.read_cookie_id(request) or services.new_cookie_id()


def _finish(request: HttpRequest, response: HttpResponse, cookie_id: str) -> HttpResponse:
    services.set_cookie(response, cookie_id)
    return response


def _owned_draft(request: AnonymousHttpRequest, draft_id: str) -> Transfer:
    transfer = get_object_or_404(
        Transfer, id=draft_id, owner__isnull=True, status=TransferStatus.DRAFT
    )
    if not services.owns_draft(request.session, transfer):
        raise Http404
    return transfer


def _owned_pending(request: AnonymousHttpRequest, draft_id: str) -> Transfer:
    transfer = get_object_or_404(
        Transfer, id=draft_id, owner__isnull=True, status=TransferStatus.PENDING_CONFIRMATION
    )
    if not services.owns_draft(request.session, transfer):
        raise Http404
    return transfer


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return {}


@require_GET
def send_page(request: AnonymousHttpRequest) -> HttpResponse:
    if not _anonymous_enabled():
        return _not_available(request)

    cookie_id = _cookie_id(request)
    session_key = services.ensure_session_key(request.session)
    transfer = services.current_anonymous_transfer(session_key)

    if transfer is not None and transfer.status == TransferStatus.ACTIVE:
        return _finish(
            request, redirect('file_transfer:anon-sent', transfer_id=transfer.id), cookie_id
        )

    if transfer is None:
        transfer = services.get_or_create_anonymous_draft(
            session_key, client_ip(request), cookie_id
        )

    settings_row = FileTransferSettings.load()
    if transfer.status == TransferStatus.PENDING_CONFIRMATION:
        context = {'transfer': transfer, 'confirm_form': ConfirmCodeForm()}
        response = render(request, 'file_transfer/anon_confirm.html', context)
        return _finish(request, response, cookie_id)

    context = {
        'draft_id': str(transfer.id),
        'settings': settings_row,
        'form': AnonymousSendOptionsForm(settings_row=settings_row),
        'part_size_bytes': PART_SIZE_BYTES,
    }
    response = render(request, 'file_transfer/anon_send.html', context)
    return _finish(request, response, cookie_id)


def _upload_errors(request: AnonymousHttpRequest, form: AnonymousSendOptionsForm) -> HttpResponse:
    response = render(
        request, 'file_transfer/partials/send_errors.html', {'form': form}, status=422
    )
    response['HX-Retarget'] = '#send-errors'
    response['HX-Reswap'] = 'innerHTML'
    return response


@require_POST
def start_confirmation(request: AnonymousHttpRequest, draft_id: str) -> HttpResponse:
    """Submitting the options form: validates everything, then starts email confirmation (spec
    section 2 step 4) -- moving the draft to `PENDING_CONFIRMATION` rather than sending anything
    yet."""
    cookie_id = _cookie_id(request)
    transfer = _owned_draft(request, draft_id)
    settings_row = FileTransferSettings.load()
    form = AnonymousSendOptionsForm(request.POST, settings_row=settings_row)
    if not form.is_valid():
        return _finish(request, _upload_errors(request, form), cookie_id)

    try:
        services.start_confirmation(transfer, form.to_anonymous_send_options())
    except ValidationError as exc:
        form.add_error(None, exc.messages[0])
        return _finish(request, _upload_errors(request, form), cookie_id)
    except ResendTooSoon as exc:
        form.add_error(None, f'Please wait {exc.retry_after_seconds}s before trying again.')
        return _finish(request, _upload_errors(request, form), cookie_id)
    except EmailVerificationSendFailed:
        form.add_error(None, 'Could not send the confirmation email. Please try again shortly.')
        return _finish(request, _upload_errors(request, form), cookie_id)

    response = hx_redirect(request, reverse('file_transfer:anon-send'))
    return _finish(request, response, cookie_id)


@require_POST
def resend_confirmation(request: AnonymousHttpRequest, draft_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_pending(request, draft_id)
    form = ConfirmCodeForm()
    error = ''
    try:
        services.resend_confirmation(transfer)
    except ValidationError as exc:
        error = exc.messages[0]
    except ResendTooSoon as exc:
        error = f'Please wait {exc.retry_after_seconds}s before requesting another code.'
    except EmailVerificationSendFailed:
        error = 'Could not send the confirmation email. Please try again shortly.'

    context = {'transfer': transfer, 'confirm_form': form, 'error': error}
    response = render(request, 'file_transfer/partials/anon_confirm_box.html', context)
    return _finish(request, response, cookie_id)


@require_POST
def confirm_code(request: AnonymousHttpRequest, draft_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_pending(request, draft_id)
    form = ConfirmCodeForm(request.POST)
    ip = client_ip(request)

    if form.is_valid():
        try:
            transfer = services.confirm_anonymous_by_code(
                transfer, form.cleaned_data['code'], ip, cookie_id
            )
        except EmailVerificationError as exc:
            form.add_error('code', str(exc))
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])

    if transfer.status == TransferStatus.ACTIVE:
        response = hx_redirect(request, reverse('file_transfer:anon-sent', args=[transfer.id]))
        return _finish(request, response, cookie_id)

    context = {'transfer': transfer, 'confirm_form': form}
    status = 200 if form.is_valid() else 422
    response = render(
        request, 'file_transfer/partials/anon_confirm_box.html', context, status=status
    )
    return _finish(request, response, cookie_id)


@require_GET
def confirm_link(request: HttpRequest, draft_id: str, token: str) -> HttpResponse:
    """The link half of confirmation (spec section 2 step 4): no session/ownership check needed
    here -- the token itself, from `apps.core.services.email_verification`, is what proves this
    click is legitimate, and this must keep working from a different browser than the one that
    started the send (e.g. the sender opens their email on their phone)."""
    transfer = get_object_or_404(Transfer, id=draft_id, owner__isnull=True)
    ip = client_ip(request)
    cookie_id = _cookie_id(request)

    if transfer.status == TransferStatus.ACTIVE:
        return _finish(
            request, redirect('file_transfer:anon-sent', transfer_id=transfer.id), cookie_id
        )

    error = ''
    if transfer.status != TransferStatus.PENDING_CONFIRMATION:
        error = 'This confirmation link is no longer valid.'
    else:
        try:
            transfer = services.confirm_anonymous_by_link(transfer, token, ip, cookie_id)
        except EmailVerificationError:
            error = 'This confirmation link is invalid or has expired.'
        except ValidationError as exc:
            error = exc.messages[0]

    if transfer.status == TransferStatus.ACTIVE:
        return _finish(
            request, redirect('file_transfer:anon-sent', transfer_id=transfer.id), cookie_id
        )

    context = {'transfer': transfer, 'confirm_form': ConfirmCodeForm(), 'error': error}
    response = render(request, 'file_transfer/anon_confirm.html', context, status=422)
    return _finish(request, response, cookie_id)


@require_GET
def sent_page(request: HttpRequest, transfer_id: str) -> HttpResponse:
    transfer = get_object_or_404(
        Transfer, id=transfer_id, owner__isnull=True, status=TransferStatus.ACTIVE
    )
    context = {'transfer': transfer, 'download_url': transfer.absolute_download_url}
    return render(request, 'file_transfer/anon_sent.html', context)


# -- File upload endpoints: identical in shape to views.uploads, just session- rather than
# login-owned, and passing the IP/cookie through to services.add_file for the per-IP byte cap. --


@require_POST
def add_file(request: AnonymousHttpRequest, draft_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_draft(request, draft_id)
    body = _json_body(request)
    name = str(body.get('name', ''))[:255].strip()
    try:
        size = int(body.get('size', 0))
    except TypeError, ValueError:
        return _finish(
            request, JsonResponse({'error': 'size must be an integer.'}, status=422), cookie_id
        )

    if not name:
        return _finish(request, JsonResponse({'error': 'name is required.'}, status=422), cookie_id)

    try:
        file = services.add_file(transfer, name, size, ip=client_ip(request), cookie_id=cookie_id)
    except ValidationError as exc:
        return _finish(request, JsonResponse({'error': exc.messages[0]}, status=422), cookie_id)

    response = JsonResponse(
        {
            'file_id': str(file.id),
            'part_size_bytes': PART_SIZE_BYTES,
            'part_count': max(1, math.ceil(size / PART_SIZE_BYTES)),
        },
        status=201,
    )
    return _finish(request, response, cookie_id)


@require_POST
def part_urls(request: AnonymousHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_draft(request, draft_id)
    file = get_object_or_404(transfer.files, id=file_id)
    body = _json_body(request)
    raw_parts = body.get('parts', [])
    try:
        parts = [
            {'part_number': int(p['part_number']), 'checksum_sha256': str(p['checksum_sha256'])}
            for p in raw_parts
        ]
    except TypeError, ValueError, KeyError:
        response = JsonResponse(
            {'error': 'parts must be a list of {part_number, checksum_sha256}.'}, status=422
        )
        return _finish(request, response, cookie_id)

    if not parts:
        return _finish(
            request, JsonResponse({'error': 'parts must not be empty.'}, status=422), cookie_id
        )

    try:
        urls = services.presign_parts(file, parts)
    except ValidationError as exc:
        return _finish(request, JsonResponse({'error': exc.messages[0]}, status=422), cookie_id)

    response = JsonResponse({'urls': {str(number): url for number, url in urls.items()}})
    return _finish(request, response, cookie_id)


@require_POST
def complete_file(request: AnonymousHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_draft(request, draft_id)
    file = get_object_or_404(transfer.files, id=file_id)
    body = _json_body(request)
    parts = body.get('parts')
    if not isinstance(parts, list) or not parts:
        response = JsonResponse({'error': 'parts must be a non-empty list.'}, status=422)
        return _finish(request, response, cookie_id)

    try:
        services.complete_file_upload(file, parts)
    except ValidationError as exc:
        return _finish(request, JsonResponse({'error': exc.messages[0]}, status=422), cookie_id)

    return _finish(request, JsonResponse({'ok': True}), cookie_id)


@require_POST
def remove_file(request: AnonymousHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    cookie_id = _cookie_id(request)
    transfer = _owned_draft(request, draft_id)
    file = get_object_or_404(transfer.files, id=file_id)
    services.remove_file(file)
    return _finish(request, JsonResponse({'ok': True}), cookie_id)
