"""The send page (spec section 2, logged-in senders only in phase 1): file picker, recipients,
message, expiry, max downloads, password. Submitting is a normal Django form
(`SendOptionsForm`), so it follows the HTMX conventions in `CLAUDE.md`: 422 + the errors partial
on failure, `hx_redirect` to the confirmation page on success. Adding/removing/uploading files
is handled by `apps/file_transfer/views/uploads.py` instead -- see its module docstring for why
those speak JSON rather than form-encoded HTMX.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core.htmx import hx_redirect
from apps.core.services.client_ip import client_ip

from .. import services
from ..forms import SendOptionsForm
from ..models import FileTransferSettings, Transfer, TransferStatus
from ..services.blocklist import is_blocked
from ..services.storage import PART_SIZE_BYTES


def _files_for_hydration(transfer: Transfer) -> list[dict]:
    """Every file already on `transfer`, serialized for the send page's upload JS to rebuild its
    file list after a reload (spec: resumable uploads) -- see `send.html`."""
    return [
        {
            'id': str(file.id),
            'name': file.name,
            'size': file.size,
            'uploaded': file.uploaded,
            'client_last_modified': file.client_last_modified,
        }
        for file in transfer.files.all()
    ]


@login_required
@require_GET
def send_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the send page. A draft transfer backs it, so the file picker has somewhere to
    attach uploads to right away -- reusing the owner's existing empty draft, if there is one,
    rather than creating a fresh row on every visit (see `services.get_or_create_draft`).

    Resuming (spec section 2, "resumable after a page reload"): a `?resume=<draft id>` query
    string picks up that specific draft instead -- including one that already has files, which
    `get_or_create_draft` deliberately never reuses on its own (see its docstring). The upload JS
    adds this parameter to the URL (`history.replaceState`, no navigation) the moment the first
    file lands on a fresh draft, so an ordinary page reload lands back here with it already set;
    the draft's existing files are then serialized into the page for that JS to rebuild its file
    list and offer to resume whichever ones haven't finished uploading yet.
    """
    if is_blocked(email=request.user.email, ip=client_ip(request)):
        # Block list (issue #59): checked at draft creation, before anything else on this page
        # even runs. Never says why -- just that sending isn't available right now, same neutral
        # wording as any other blocked checkpoint (`services.blocklist.BLOCKED_MESSAGE`).
        return render(request, 'file_transfer/blocked.html', status=403)

    resume_id = request.GET.get('resume')
    draft = None
    if resume_id:
        try:
            draft = Transfer.objects.filter(
                id=resume_id, owner=request.user, status=TransferStatus.DRAFT
            ).first()
        except ValueError, ValidationError:
            # Not a well-formed UUID (a stale/tampered query string) -- fall back below rather
            # than 404, same as if `resume` had simply been omitted.
            draft = None
    if draft is None:
        draft = services.get_or_create_draft(request.user)
    context = {
        'draft_id': str(draft.id),
        'settings': FileTransferSettings.load(),
        'form': SendOptionsForm(),
        'part_size_bytes': PART_SIZE_BYTES,
        'balance': get_balance(request.user),
        'min_balance_to_send': services.MIN_BALANCE_TO_SEND,
        'existing_files': _files_for_hydration(draft),
    }
    return render(request, 'file_transfer/send.html', context)


def _errors(request: AuthenticatedHttpRequest, form: SendOptionsForm) -> HttpResponse:
    response = render(
        request, 'file_transfer/partials/send_errors.html', {'form': form}, status=422
    )
    response['HX-Retarget'] = '#send-errors'
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
@require_POST
def send_submit(request: AuthenticatedHttpRequest, draft_id: str) -> HttpResponse:
    transfer = get_object_or_404(
        Transfer, id=draft_id, owner=request.user, status=TransferStatus.DRAFT
    )
    form = SendOptionsForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    try:
        services.finalize_send(transfer, form.to_send_options(), ip=client_ip(request))
    except ValidationError as exc:
        form.add_error(None, exc.messages[0])
        return _errors(request, form)
    except InsufficientCreditsError as exc:
        form.add_error(None, str(exc))
        return _errors(request, form)

    return hx_redirect(request, reverse('file_transfer:sent', args=[transfer.id]))


@login_required
@require_GET
def sent_page(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    """Confirmation page shown right after sending: the link is always shown here too, on top of
    the recipient/sender emails (spec section 2)."""
    transfer = get_object_or_404(
        Transfer, id=transfer_id, owner=request.user, status=TransferStatus.ACTIVE
    )
    context = {'transfer': transfer, 'download_url': transfer.absolute_download_url}
    return render(request, 'file_transfer/sent.html', context)
