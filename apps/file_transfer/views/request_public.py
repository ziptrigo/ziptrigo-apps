"""The public side of a file request's link (issue #306): `/t/<slug>/` for a request is the upload
page while it is open and the download page once it is done, behind the access check
(`services.request_access`: password, then audience).

The page itself is `render_request_page`, called by `views.download.download_page` and
`views.download.unlock`; marking the request done from the upload page is `request_done`. The JSON
endpoints that move bytes are in `views.request_upload`. Every unavailable state is the same
neutral "no longer available" page as for a transfer.
"""

import logging

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.core import ratelimit
from apps.core.htmx import hx_redirect

from .. import services
from ..forms import AbuseReportForm, DownloadPasswordForm
from ..models import FileTransferSettings, Transfer, TransferKind
from ..services.request_access import Access, check_access, is_owner
from ..services.request_uploader import identify, own_files, remember_name, remembered_name
from ..services.storage import PART_SIZE_BYTES
from .types import PublicHttpRequest

logger = logging.getLogger(__name__)


def owner_display_name(transfer: Transfer) -> str:
    """The owner as an uploader reads it: the account name, never the email."""
    return (transfer.owner.name if transfer.owner else '') or 'Someone'


def _gate(request: PublicHttpRequest, transfer: Transfer, access: Access, form, status: int):
    context = {
        'transfer': transfer,
        'access': access.value,
        'password_form': form or DownloadPasswordForm(),
        'owner_name': owner_display_name(transfer),
    }
    template = 'file_transfer/request_gate.html'
    if access == Access.NEEDS_PASSWORD:
        return render(request, template, context, status=status)
    return render(request, template, context, status=403)


def _upload_context(request, transfer: Transfer, uploader) -> dict:
    settings_row = FileTransferSettings.load()
    own = own_files(transfer, uploader).order_by('created_at')
    return {
        'transfer': transfer,
        'owner_name': owner_display_name(transfer),
        'upload_base': reverse('t:download', args=[transfer.slug]) + 'upload',
        'part_size_bytes': PART_SIZE_BYTES,
        'max_files_text': services.limits.count_text(transfer.effective_max_files, 'file'),
        'max_total_text': services.limits.size_text(transfer.effective_max_total_size_bytes),
        'max_file_text': services.limits.size_text(settings_row.logged_in_max_file_size_bytes),
        'uploader': uploader,
        'is_owner': is_owner(transfer, request.user),
        'existing_files': [
            {
                'id': str(file.id),
                'name': file.name,
                'size': file.size,
                'uploaded': file.uploaded,
                'client_last_modified': file.client_last_modified,
            }
            for file in own
        ],
        'report_form': AbuseReportForm(),
    }


def render_request_page(
    request: PublicHttpRequest, transfer: Transfer, *, password_form=None, status: int = 200
) -> HttpResponse:
    """The page of an *available* request: a sign-in redirect, the gate (password, confirm your
    email, not invited), the upload page (open) or the download page (done). Never says why a
    link is unavailable; the callers handled that already."""
    access = check_access(transfer, request.user, request.session)
    if access == Access.NEEDS_SIGN_IN:
        return redirect_to_login(request.get_full_path())
    if access != Access.OK:
        return _gate(request, transfer, access, password_form, status)

    if transfer.is_open:
        uploader = identify(request.session, request.user)
        return render(
            request,
            'file_transfer/request_upload.html',
            _upload_context(request, transfer, uploader),
            status=status,
        )

    from .download import download_qr_data_uri

    owner_viewing = is_owner(transfer, request.user)
    files = transfer.files.filter(uploaded=True).order_by('name')
    context = {
        'transfer': transfer,
        'files': files,
        'unlocked': True,
        'password_form': DownloadPasswordForm(),
        'report_form': AbuseReportForm(),
        'qr_data_uri': download_qr_data_uri(transfer),
        'show_uploaders': owner_viewing,
        'is_request': True,
    }
    return render(request, 'file_transfer/download.html', context, status=status)


def _json_or_message(request: PublicHttpRequest, message: str, status: int = 422) -> HttpResponse:
    response = render(
        request,
        'file_transfer/partials/request_message.html',
        {'message': message},
        status=status,
    )
    response['HX-Retarget'] = '#request-msg'
    response['HX-Reswap'] = 'innerHTML'
    return response


@require_POST
def request_done(request: PublicHttpRequest, slug: str) -> HttpResponse:
    """ "Mark as done" from the upload page: any person who passes the access check may do it
    (whether or not they uploaded), once the request has at least one uploaded file. After it the
    link shows the download view. An uploader cannot undo it."""
    from .download import _became_unavailable

    limited = ratelimit.hit_ip(request, 'FT_REQUEST_UPLOAD_IP')
    if not limited.allowed:
        return ratelimit.htmx_response(request, limited, retarget='#request-msg')

    transfer = Transfer.objects.filter(slug=slug, kind=TransferKind.REQUEST).first()
    if transfer is None or not services.is_available(transfer):
        return _became_unavailable(request)
    access = check_access(transfer, request.user, request.session)
    if access == Access.NEEDS_SIGN_IN:
        return redirect_to_login(request.get_full_path())
    if access != Access.OK:
        return _became_unavailable(request)

    user = request.user if isinstance(request.user, User) else None
    name = ''
    if user is None:
        typed = request.POST.get('name')
        name = (
            remember_name(request.session, typed)
            if typed is not None
            else remembered_name(request.session)
        )
    try:
        services.requests.mark_done(transfer, by_user=user, by_name=name)
    except ValidationError as exc:
        return _json_or_message(request, exc.messages[0])

    if is_owner(transfer, request.user):
        messages.success(request, 'Marked as done.')
    else:
        messages.success(request, f'Thanks, {owner_display_name(transfer)} has been told.')
    return hx_redirect(request, reverse('t:download', args=[slug]))
