"""The emails Receipts sends, always to the account owner and never to a sender: "needs your
attention" (one per inbound email whose receipts failed or need review) and "storage paused".

Each is a `django.tasks` task on the `receipts` queue -- call `<name>.enqueue(...)`, never
`<name>.call(...)` -- taking plain ids and re-reading the rows (see
`apps/file_transfer/services/emails.py`). Both pass an explicit autoescaped `html_body` (built with
`format_html`, same precedent as `apps.feedback`: without one, `SesEmailBackend` wraps the text in
an unescaped `<pre>`), because filenames and email subjects come from whoever sent the email. None
of that sender-controlled text goes into a subject line.

The mail is only sent to a *confirmed* address: changing the email through the API un-confirms the
account but leaves sessions valid, and these emails quote file names and subjects.

Links are absolute (`settings.BASE_URL` + `reverse()`), like the other apps' emails.
"""

import logging
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.tasks import task
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from apps.accounts.models import User
from apps.core.services.email import send_email

from ..models import (
    IN_PROGRESS_STATUSES,
    FailureReason,
    InboundEmail,
    Receipt,
    ReceiptStatus,
)
from .queue import enqueue_after_commit

logger = logging.getLogger(__name__)

_FAILURE_TEXT = {
    FailureReason.MULTIPLE_RECEIPTS: 'contains more than one receipt; upload each separately',
    FailureReason.NOT_A_RECEIPT: "doesn't look like a receipt or an invoice",
    FailureReason.UNREADABLE: "couldn't be read",
    FailureReason.UNSUPPORTED_TYPE: 'is not a supported file type',
    FailureReason.TOO_LARGE: 'is too large',
    FailureReason.EXTRACTION_ERROR: "couldn't be processed; open it and run the extraction again",
}

_FIELD_LABELS = {'vendor': 'vendor', 'amount': 'amount', 'receipt_date': 'date'}


def receipts_url() -> str:
    return f'{settings.BASE_URL}{reverse("receipts:dashboard")}'


def receipt_url(receipt_id: object) -> str:
    return f'{settings.BASE_URL}{reverse("receipts:detail", args=[receipt_id])}'


def _label(receipt: Receipt) -> str:
    if receipt.original_filename:
        return receipt.original_filename
    return 'the email body' if receipt.source == 'email' else 'a file'


def _issue_text(receipt: Receipt) -> str:
    if receipt.status == ReceiptStatus.NEEDS_REVIEW:
        missing = [_FIELD_LABELS[name] for name in receipt.missing_required_fields]
        if missing:
            return f"couldn't read the {', '.join(missing)}"
        return 'needs a quick check'
    try:
        return _FAILURE_TEXT[FailureReason(receipt.failure_reason)]
    except ValueError:
        return "couldn't be processed"


def lock_inbound_email(email_id: object) -> None:
    """Row-lock the inbound email (inside a transaction; a no-op on SQLite). A settling receipt
    takes this right after locking its own row, *before* it looks at its siblings: two siblings
    settling at once then run their "is everyone done?" checks one after the other, and the
    second sees the first's committed status. Without it each could still see the other as
    `processing` (READ COMMITTED) and nobody would send the email. Lock order is always receipt,
    then inbound email."""
    if email_id is not None:
        list(InboundEmail.objects.select_for_update().filter(pk=email_id).values_list('pk'))


def queue_attention_email_if_settled(receipt: Receipt) -> None:
    """Call right after `receipt` settles (in the transaction that settled it, which has already
    called `lock_inbound_email`). When it was the last unsettled receipt of its inbound email and
    any of them failed or need review, queue the "needs your attention" email -- at most once per
    inbound email (a compare-and-swap on `attention_notified_at`)."""
    email_id = receipt.inbound_email_id
    if email_id is None:
        return
    siblings = Receipt.objects.filter(inbound_email_id=email_id, deleted_at__isnull=True)
    if siblings.filter(status__in=IN_PROGRESS_STATUSES).exists():
        return
    if not siblings.filter(status__in=[ReceiptStatus.FAILED, ReceiptStatus.NEEDS_REVIEW]).exists():
        return
    claimed = InboundEmail.objects.filter(pk=email_id, attention_notified_at__isnull=True).update(
        attention_notified_at=timezone.now()
    )
    if claimed:
        enqueue_after_commit(send_attention_email, str(email_id))


@task(queue_name='receipts')
def send_attention_email(email_id: str) -> None:
    """ "N receipts need your attention" -- to the owner, about one inbound email."""
    try:
        email = InboundEmail.objects.select_related('user').get(pk=email_id)
    except InboundEmail.DoesNotExist, ValidationError:
        return
    user = email.user
    if not user.email or not user.email_confirmed:
        return
    receipts = list(
        Receipt.objects.filter(
            inbound_email=email,
            deleted_at__isnull=True,
            status__in=[ReceiptStatus.FAILED, ReceiptStatus.NEEDS_REVIEW],
        ).order_by('created_at')
    )
    if not receipts:
        return

    count = len(receipts)
    subject = (
        '1 receipt needs your attention' if count == 1 else f'{count} receipts need your attention'
    )
    received = (email.received_at or email.created_at or timezone.now()).strftime('%b %d, %H:%M')
    heading = f'From the email "{email.subject or "(no subject)"}" (received {received} UTC):'

    text_lines = [heading]
    for receipt in receipts:
        text = _issue_text(receipt)
        link = receipt_url(receipt.pk)
        suffix = f' -> Review: {link}' if receipt.status == ReceiptStatus.NEEDS_REVIEW else ''
        text_lines.append(f'  - {_label(receipt)} {text}{suffix}')
    text_lines += ['', f'Manage receipts: {receipts_url()}']

    html_body = format_html(
        '<p>{}</p><ul>{}</ul><p><a href="{}">Manage receipts</a></p>',
        heading,
        format_html_join(
            '',
            '<li>{} {} <a href="{}">Review</a></li>',
            ((_label(r), _issue_text(r), receipt_url(r.pk)) for r in receipts),
        ),
        receipts_url(),
    )
    send_email(
        to=user.email, subject=subject, text_body='\n'.join(text_lines), html_body=str(html_body)
    )


@task(queue_name='receipts')
def send_storage_paused_email(user_id: str) -> None:
    """ "Storage is paused" -- to the owner, when the daily storage charge couldn't be paid."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return
    if not user.email or not user.email_confirmed:
        return
    top_up = f'{settings.BASE_URL}{reverse("billing:credits-history")}'
    lines = [
        "Your credit balance couldn't cover the storage for your receipts.",
        'Your receipts are safe, nothing was deleted. Until you add credits, previews, '
        'downloads and processing are paused.',
        '',
        f'Add credits: {top_up}',
        f'Your receipts: {receipts_url()}',
    ]
    html_body = format_html(
        "<p>Your credit balance couldn't cover the storage for your receipts.</p>"
        '<p>Your receipts are safe, nothing was deleted. Until you add credits, previews, '
        'downloads and processing are paused.</p>'
        '<p><a href="{}">Add credits</a> · <a href="{}">Your receipts</a></p>',
        top_up,
        receipts_url(),
    )
    send_email(
        to=user.email,
        subject='Your receipts storage is paused',
        text_body='\n'.join(lines),
        html_body=str(html_body),
    )
