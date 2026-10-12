"""A receipt's page: preview plus the editable fields, and the actions on it.

`GET /receipts/<id>/` renders the page; `POST` to the same URL is the form (an HTMX form view: a
422 puts the messages in the page's message slot, success redirects back with a flash message).
The other actions -- re-run the extraction, move to the trash, "not a duplicate", download -- are
small POST/GET endpoints that call the same `services.actions` the API will.

The preview is never proxied through Django except for an email body (`body_view`): files are
shown through short-lived presigned S3 URLs, and an HTML body is served here with a locked-down
CSP into a sandboxed `<iframe>` so it can run no script and load nothing remote.
"""

import html
import logging

from botocore.exceptions import BotoCoreError, ClientError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx
from apps.core.services.storage import ObjectTooLargeError

from ..forms import ReceiptForm
from ..models import (
    IN_PROGRESS_STATUSES,
    FailureReason,
    Receipt,
    ReceiptCategory,
    ReceiptsSettings,
    ReceiptStatus,
)
from ..services import (
    AccountFrozenError,
    dismiss_duplicate,
    download_url,
    get_account,
    get_body_bytes,
    preview_url,
    reprocess,
    trash,
    update_receipt,
)
from ..services.currencies import ISO_4217_CODES
from ..services.documents import HEIC, HTML, JPEG, PDF, PLAIN, PNG, WEBP
from .common import form_error_lines, message_response, owned_receipt, render_page

logger = logging.getLogger(__name__)

#: The CSP of the email body document. The first three directives are the contract (no script, no
#: remote loads: only inline styles and `data:` images); the rest close what `default-src` does not
#: cover (`<base>`, forms) and allow only our own pages to frame it.
BODY_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)

#: Polls of the in-progress page for its result, every 3 s, before it stops asking.
_MAX_AUTO_POLLS = 40

_BODY_TYPES = (HTML, PLAIN)
_IMAGE_TYPES = (JPEG, PNG, WEBP, HEIC)
_NOT_REPROCESSABLE = (FailureReason.UNSUPPORTED_TYPE, FailureReason.TOO_LARGE)


# -- The page -----------------------------------------------------------------------------------


def _preview(receipt: Receipt, frozen: bool) -> dict:
    """What the preview well shows: `kind` is `image`, `pdf`, `body` (the sandboxed iframe),
    `frozen` or `none`, with the `url` to load or a `message` saying why there is nothing."""
    if frozen:
        return {'kind': 'frozen'}
    if receipt.content_type in _BODY_TYPES and receipt.file_key:
        return {'kind': 'body', 'url': reverse('receipts:body', args=[receipt.pk])}
    if receipt.content_type == PDF or receipt.content_type in _IMAGE_TYPES:
        try:
            url = preview_url(receipt)
        except AccountFrozenError:
            return {'kind': 'frozen'}
        except ValidationError as exc:
            return {'kind': 'none', 'message': exc.messages[0]}
        return {'kind': 'pdf' if receipt.content_type == PDF else 'image', 'url': url}
    return {'kind': 'none', 'message': 'There is no preview for this file.'}


def _currency_choices(*extra: str) -> list[str]:
    return sorted(ISO_4217_CODES | {code for code in extra if code})


def _detail_context(request: AuthenticatedHttpRequest, receipt: Receipt) -> dict:
    account = get_account(request.user)
    frozen = account.is_frozen
    in_progress = receipt.status in IN_PROGRESS_STATUSES
    trashed = receipt.deleted_at is not None

    try:
        polls = max(int(request.GET.get('polls', '0')), 0)
    except ValueError:
        polls = 0
    poll_url = ''
    if receipt.status in (ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING) and (
        polls < _MAX_AUTO_POLLS
    ):
        poll_url = f'{reverse("receipts:detail", args=[receipt.pk])}?polls={polls + 1}'

    duplicate_of = None
    if receipt.possible_duplicate_of_id and not receipt.duplicate_dismissed:
        duplicate_of = receipt.possible_duplicate_of

    settled = receipt.status in (ReceiptStatus.NEEDS_REVIEW, ReceiptStatus.PROCESSED)
    price = ReceiptsSettings.load().price_per_file
    return {
        'receipt': receipt,
        'preview': _preview(receipt, frozen),
        'frozen': frozen,
        'trashed': trashed,
        'in_progress': in_progress,
        'failed': receipt.status == ReceiptStatus.FAILED,
        'poll_url': poll_url,
        'duplicate_of': duplicate_of,
        'missing': set(receipt.missing_required_fields) if settled else set(),
        'can_download': bool(receipt.file_key and receipt.uploaded) and not frozen,
        'can_reprocess': (
            not trashed
            and not in_progress
            and bool(receipt.file_key)
            and receipt.failure_reason not in _NOT_REPROCESSABLE
        ),
        'price_per_file': price,
        'categories': ReceiptCategory.objects.filter(user=request.user),
        'currencies': _currency_choices(receipt.currency, account.default_currency),
        'default_currency': account.default_currency,
        'inbound_email': receipt.inbound_email,
        'refunded': receipt.refunded_at is not None,
        'dashboard_url': reverse('receipts:dashboard'),
    }


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def detail(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)
    if request.method == 'POST':
        return _save(request, receipt)

    context = _detail_context(request, receipt)
    response = render_page(request, 'receipts/detail.html', context)
    if context['preview']['kind'] == 'body':
        # The sandboxed iframe may only ever show our own body view, even if the email tries to
        # navigate it (a `<meta http-equiv="refresh">` to a tracking URL, say).
        response['Content-Security-Policy'] = "frame-src 'self'"
    return response


def _fail(
    request: AuthenticatedHttpRequest,
    receipt: Receipt,
    lines: list[str] | str,
    *,
    top_up: bool = False,
) -> HttpResponse:
    """A refused action: the message slot for htmx (422), a flash message and a redirect back
    otherwise."""
    if is_htmx(request):
        return message_response(request, lines, retarget='#receipt-form-msg', top_up=top_up)
    for line in [lines] if isinstance(lines, str) else lines:
        messages.error(request, line)
    return redirect('receipts:detail', receipt_id=receipt.pk)


def _save(request: AuthenticatedHttpRequest, receipt: Receipt) -> HttpResponse:
    form = ReceiptForm(request.POST, user=request.user)
    if not form.is_valid():
        return _fail(request, receipt, form_error_lines(form))
    try:
        update_receipt(receipt, form.cleaned_data)
    except ValidationError as exc:
        return _fail(request, receipt, exc.messages)
    messages.success(request, 'Saved.')
    return hx_redirect(request, reverse('receipts:detail', args=[receipt.pk]))


# -- Actions ------------------------------------------------------------------------------------


@login_required
@require_POST
def reprocess_receipt(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_REPROCESS_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#receipt-form-msg')
    try:
        reprocess(receipt)
    except AccountFrozenError as exc:
        return _fail(request, receipt, exc.message, top_up=True)
    except InsufficientCreditsError:
        return _fail(
            request,
            receipt,
            "You don't have enough credits to run the extraction again.",
            top_up=True,
        )
    except ValidationError as exc:
        return _fail(request, receipt, exc.messages)
    messages.success(request, 'Reading the receipt again. This usually takes a few seconds.')
    return hx_redirect(request, reverse('receipts:detail', args=[receipt.pk]))


@login_required
@require_POST
def trash_receipt(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id)
    trash(receipt)
    days = ReceiptsSettings.load().trash_days
    messages.success(request, f'Moved to the trash. You can restore it from there for {days} days.')
    return hx_redirect(request, reverse('receipts:dashboard'))


@login_required
@require_POST
def dismiss_duplicate_flag(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    """ "Not a duplicate": clear the flag for good. htmx gets the (now empty) banner slot."""
    receipt = owned_receipt(request, receipt_id)
    receipt = dismiss_duplicate(receipt)
    if not is_htmx(request):
        return redirect('receipts:detail', receipt_id=receipt.pk)
    return render(request, 'receipts/partials/duplicate_banner.html', {'duplicate_of': None})


@login_required
@require_GET
def download(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    """Redirect to a short-lived "Save As" URL of the original file."""
    receipt = owned_receipt(request, receipt_id)
    try:
        url = download_url(receipt)
    except AccountFrozenError as exc:
        messages.error(request, exc.message)
        return redirect('receipts:detail', receipt_id=receipt.pk)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('receipts:detail', receipt_id=receipt.pk)
    response = redirect(url)
    response['Cache-Control'] = 'private, no-store'
    return response


# -- The email body -----------------------------------------------------------------------------


def _body_response(content: str | bytes, content_type: str, status: int = 200) -> HttpResponse:
    response = HttpResponse(content, content_type=content_type, status=status)
    response['Content-Security-Policy'] = BODY_CSP
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    response['Cache-Control'] = 'private, no-store'
    return response


def _body_notice(text: str, status: int) -> HttpResponse:
    page = (
        '<!doctype html><meta charset="utf-8"><body style="font:14px system-ui,sans-serif;'
        f'color:#6b7280;margin:1rem">{html.escape(text)}</body>'
    )
    return _body_response(page, 'text/html; charset=utf-8', status)


@login_required
@require_GET
@xframe_options_sameorigin
def body_view(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    """The stored body of an email-receipt, for the detail page's `<iframe sandbox>`.

    The HTML is the sender's, so it is served as an isolated document: the CSP above allows no
    script and no remote loads, `nosniff` pins the type, and the frame itself is sandboxed without
    `allow-scripts`. Plain-text bodies are escaped into a `<pre>`. Refused while the account is
    frozen, like every other way of reading the stored file.

    Only our own pages may frame it (`X-Frame-Options: SAMEORIGIN` and the CSP's
    `frame-ancestors 'self'`).
    """
    receipt = owned_receipt(request, receipt_id)
    if receipt.content_type not in _BODY_TYPES:
        raise Http404('Receipt not found')
    try:
        data = get_body_bytes(receipt)
    except AccountFrozenError as exc:
        return _body_notice(exc.message, 402)
    except ObjectTooLargeError:
        return _body_notice('This email is too large to preview.', 413)
    except ValidationError, ClientError, BotoCoreError:
        logger.exception('Could not read the body of receipt %s', receipt.pk)
        return _body_notice('The email could not be loaded.', 404)

    if receipt.content_type == PLAIN:
        text = data.decode('utf-8', errors='replace')
        page = (
            '<!doctype html><meta charset="utf-8"><body style="margin:1rem"><pre style="'
            'white-space:pre-wrap;word-wrap:break-word;font:14px/1.5 ui-monospace,monospace;'
            f'margin:0">{html.escape(text)}</pre></body>'
        )
        return _body_response(page, 'text/html; charset=utf-8')
    return _body_response(data, 'text/html; charset=utf-8')
