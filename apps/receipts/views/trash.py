"""The Trash: trashed receipts (still billed, restorable) until `ReceiptsSettings.trash_days`
pass, when the `purge_trash` job deletes them. "Delete forever" does it now.

These are plain POST forms (a confirmation modal for the destructive one), not htmx: each ends in a
redirect with a flash message, which keeps the page's counts and empty state right.
"""

import logging
from datetime import timedelta
from math import ceil

from botocore.exceptions import BotoCoreError, ClientError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect

from ..forms import PAGE_SIZE
from ..models import Receipt, ReceiptsSettings
from ..services import delete_forever, filter_receipts, restore
from .common import owned_receipt, render_page

logger = logging.getLogger(__name__)


def _label(receipt: Receipt) -> str:
    return receipt.vendor or receipt.original_filename or 'the receipt'


@login_required
@require_GET
@ensure_csrf_cookie
def trash_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    config = ReceiptsSettings.load()
    queryset = filter_receipts(request.user, trashed=True).select_related('category')
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))

    now = timezone.now()
    rows = []
    for receipt in page_obj.object_list:
        purge_at = receipt.deleted_at + timedelta(days=config.trash_days)
        days_left = max(ceil((purge_at - now).total_seconds() / 86400), 0)
        rows.append({'receipt': receipt, 'days_left': days_left, 'label': _label(receipt)})
    return render_page(
        request,
        'receipts/trash.html',
        {'rows': rows, 'page_obj': page_obj, 'trash_days': config.trash_days},
    )


@login_required
@require_POST
def restore_receipt(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id, trashed=True)
    restore(receipt)
    messages.success(request, f'Restored {_label(receipt)}.')
    if request.POST.get('next') == 'detail':
        return hx_redirect(request, reverse('receipts:detail', args=[receipt.pk]))
    return hx_redirect(request, reverse('receipts:trash'))


@login_required
@require_POST
def delete_receipt_forever(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = owned_receipt(request, receipt_id, trashed=True)
    label = _label(receipt)
    try:
        delete_forever(receipt)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('receipts:trash')
    except ClientError, BotoCoreError:
        logger.exception('Could not delete receipt %s forever', receipt.pk)
        messages.error(request, "We couldn't delete it just now. Try again in a moment.")
        return redirect('receipts:trash')
    messages.success(request, f'Deleted {label} forever.')
    return redirect('receipts:trash')
