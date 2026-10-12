"""Processing one receipt: charge, read the stored file, ask the AI, apply the result.

`process_receipt(receipt_id)` is a `django.tasks` task on the `receipts` queue, so a slow AI or PDF
job never delays file transfer's emails and zip builds. The queue is at-least-once and its
backends don't retry, so everything here is written to be re-run safely:

1. **Claim and charge** (`claim`). One conditional `UPDATE` moves `received` / `waiting_for_credits`
   to `processing`; only the worker that wins it goes on. In the same `atomic()` it charges
   `ReceiptsSettings.price_per_file` once per `attempt_id` (`charged_attempt_id` records it, so a
   re-run of the same attempt never charges twice, while a user re-run takes a new `attempt_id` and
   is charged again). A price of 0 skips `spend_credits`. A frozen account, or not enough credits,
   parks the receipt in `waiting_for_credits`; `services.metering.resume_account` picks it up on a
   top-up.
2. **Prepare the input** (outside any transaction): read the object from S3, `documents.
   prepare_input` (HEIC to JPEG, downscale, PDF text or rendered pages, HTML to text). A HEIC
   original also gets its JPEG preview stored.
3. **Call the AI** (outside any transaction, `services.ai`).
4. **Apply the result** under a row lock, only if the receipt is still `processing` for the same
   `attempt_id` (a trash/re-run in the meantime wins):
   - more than one receipt in the file: `failed` / `multiple_receipts`;
   - not a receipt: `failed` / `not_a_receipt`;
   - vendor, amount or receipt date missing, or a low confidence: `needs_review`;
   - otherwise `processed`.
   Fields the user edited (`user_edited_fields`) are never overwritten, and `notes` is never
   touched. A failed outcome stores the raw result in `ai_result` but fills no fields.
5. **Transient AI errors** (timeouts, 5xx, 429, a malformed reply) put the receipt back to `received`
   with `next_attempt_at` set from a backoff; `retry_due` re-enqueues it. After
   `ReceiptsSettings.max_ai_attempts` it fails as `extraction_error`. Neither `django.tasks`'s
   retries nor `run_after` are used (the Immediate backend rejects `run_after`).
6. **Refunds.** When a receipt fails *without a usable AI answer* -- retries exhausted, a permanent
   AI error, or a file that could not be read at all so no AI call was made -- the charge is
   returned (`add_credits(..., tx_type=REFUND)`, guarded by a compare-and-swap on `refunded_at`).
   A receipt the AI did answer about (not a receipt, several receipts, needs review) stays charged:
   the work was done.
7. **Notify**: when this settles the last receipt of an inbound email and any failed or need
   review, the owner gets one "needs your attention" email (`services.emails`).
"""

import logging
from datetime import timedelta
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from django.db import transaction
from django.tasks import task
from django.utils import timezone

from apps.billing.models import CreditTransactionType
from apps.billing.services import InsufficientCreditsError, add_credits, spend_credits
from apps.core.services.storage import ObjectTooLargeError

from ..models import (
    EXTRACTED_FIELDS,
    REQUIRED_FIELDS,
    FailureReason,
    Receipt,
    ReceiptCategory,
    ReceiptsSettings,
    ReceiptStatus,
)
from . import ai, documents
from .accounts import add_stored_bytes, get_account
from .duplicates import refresh_duplicate
from .emails import queue_attention_email_if_settled
from .queue import enqueue_after_commit
from .storage import get_storage

logger = logging.getLogger(__name__)

#: Below this the AI itself says it isn't sure, so a person should look (unless they already
#: filled in every required field themselves).
LOW_CONFIDENCE_THRESHOLD = 0.5

#: Delay before retry number 1, 2, 3, ... after a transient failure (the last repeats). The
#: `retry_due` job runs every 5 minutes, so there is no point in a shorter first step.
RETRY_BACKOFF = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))

#: `CreditTransaction.description` is a 255-character column.
_DESCRIPTION_MAX = 255

_SAVE_FIELDS = [
    'status',
    'failure_reason',
    'attempts',
    'next_attempt_at',
    'processing_started_at',
    'last_error',
    'refunded_at',
    'processed_at',
    'ai_result',
    'ai_confidence',
    'possible_duplicate_of',
    'updated_at',
    *EXTRACTED_FIELDS,
]


def retry_delay(failures: int) -> timedelta:
    """How long to wait after the `failures`-th transient failure."""
    return RETRY_BACKOFF[min(max(failures, 1), len(RETRY_BACKOFF)) - 1]


def enqueue_processing(receipt_id: object) -> None:
    """Queue `process_receipt` once the surrounding transaction (if any) has committed."""
    enqueue_after_commit(process_receipt, str(receipt_id))


def _description(prefix: str, receipt: Receipt) -> str:
    name = receipt.original_filename or 'email'
    return f'{prefix}: {name}'[:_DESCRIPTION_MAX]


@task(queue_name='receipts')
def process_receipt(receipt_id: str) -> None:
    process(receipt_id)


def process(receipt_id: str) -> None:
    """The body of `process_receipt`. Safe to call for any receipt in any state: it does nothing
    unless it wins the claim."""
    receipt = claim(receipt_id)
    if receipt is None:
        return
    try:
        _run(receipt)
    except Exception:
        # A bug or an unforeseen failure must not leave the receipt `processing` for the 30
        # minutes `retry_due` waits; count it as a failed try instead.
        logger.exception('Processing receipt %s failed unexpectedly', receipt.pk)
        _transient_failure(receipt.pk, receipt.attempt_id, 'Unexpected error while processing')


# -- 1. Claim and charge --------------------------------------------------------------------------


def claim(receipt_id: object) -> Receipt | None:
    """Win the right to process `receipt_id` and charge for the attempt. Returns the receipt (now
    `processing`) or `None` if it wasn't claimable or couldn't be paid for (then it is parked in
    `waiting_for_credits`)."""
    now = timezone.now()
    with transaction.atomic():
        claimed = Receipt.objects.filter(
            pk=receipt_id,
            status__in=[ReceiptStatus.RECEIVED, ReceiptStatus.WAITING_FOR_CREDITS],
            uploaded=True,
            deleted_at__isnull=True,
        ).update(status=ReceiptStatus.PROCESSING, processing_started_at=now, next_attempt_at=None)
        if not claimed:
            return None
        receipt = Receipt.objects.select_related('user').get(pk=receipt_id)

        if get_account(receipt.user).is_frozen:
            _park_waiting(receipt)
            return None

        if receipt.charged_attempt_id != receipt.attempt_id:
            price = ReceiptsSettings.load().price_per_file
            if price > 0:
                try:
                    spend_credits(
                        receipt.user,
                        price,
                        description=_description('Receipt processing', receipt),
                        source='receipts',
                    )
                except InsufficientCreditsError:
                    _park_waiting(receipt)
                    return None
            Receipt.objects.filter(pk=receipt.pk).update(
                charged_attempt_id=receipt.attempt_id, credits_charged=price
            )
            receipt.charged_attempt_id = receipt.attempt_id
            receipt.credits_charged = price
        return receipt


def _park_waiting(receipt: Receipt) -> None:
    Receipt.objects.filter(pk=receipt.pk).update(
        status=ReceiptStatus.WAITING_FOR_CREDITS, processing_started_at=None
    )
    receipt.status = ReceiptStatus.WAITING_FOR_CREDITS


# -- 2-3. Read, prepare, ask the AI ---------------------------------------------------------------


def _is_missing_object(exc: ClientError) -> bool:
    return str(exc.response.get('Error', {}).get('Code', '')) in ('NoSuchKey', '404', 'NotFound')


def _run(receipt: Receipt) -> None:
    config = ReceiptsSettings.load()
    storage = get_storage()
    attempt_id = receipt.attempt_id

    try:
        data = storage.get_object_bytes(
            receipt.file_key, max_bytes=max(config.max_file_bytes, receipt.size_bytes)
        )
    except ObjectTooLargeError:
        _fail(receipt.pk, attempt_id, FailureReason.TOO_LARGE, 'The stored file is too large.')
        return
    except ClientError as exc:
        if _is_missing_object(exc):
            _fail(receipt.pk, attempt_id, FailureReason.UNREADABLE, 'The stored file is missing.')
        else:
            _transient_failure(receipt.pk, attempt_id, 'Could not read the stored file')
        return
    except BotoCoreError:
        _transient_failure(receipt.pk, attempt_id, 'Could not read the stored file')
        return

    content_type = receipt.content_type or documents.sniff_content_type(data, allow_text=True) or ''
    try:
        prepared = documents.prepare_input(
            data, content_type, max_pdf_pages=config.max_pdf_pages_to_ai
        )
    except documents.UnsupportedDocumentError as exc:
        _fail(receipt.pk, attempt_id, FailureReason.UNSUPPORTED_TYPE, str(exc))
        return
    except documents.UnreadableDocumentError as exc:
        _fail(receipt.pk, attempt_id, FailureReason.UNREADABLE, str(exc))
        return

    if prepared.preview_jpeg and not receipt.preview_key:
        _store_preview(receipt, prepared.preview_jpeg)

    categories = list(
        ReceiptCategory.objects.filter(user_id=receipt.user_id).values_list('name', flat=True)
    )
    request = ai.ExtractionRequest(
        categories=categories,
        images=[ai.ImagePart('image/jpeg', jpeg) for jpeg in prepared.images],
        text=prepared.text,
    )
    try:
        extracted = ai.get_client().extract(request)
    except ai.PermanentAIError as exc:
        _fail(receipt.pk, attempt_id, FailureReason.EXTRACTION_ERROR, str(exc), refund=True)
        return
    except ai.TransientAIError as exc:
        _transient_failure(receipt.pk, attempt_id, str(exc))
        return

    _apply_result(receipt.pk, attempt_id, extracted)


def _store_preview(receipt: Receipt, jpeg: bytes) -> None:
    from .storage import preview_key

    key = preview_key(receipt.user_id, receipt.pk)
    get_storage().put_object(key, jpeg, content_type='image/jpeg')
    stored = Receipt.objects.filter(pk=receipt.pk, preview_key='').update(
        preview_key=key, preview_size_bytes=len(jpeg)
    )
    if stored:
        add_stored_bytes(receipt.user_id, len(jpeg))
        receipt.preview_key = key


# -- 4. Apply the result --------------------------------------------------------------------------


def _lock_current(receipt_id: object, attempt_id: UUID) -> Receipt | None:
    """The receipt, row-locked, if it is still `processing` this attempt (else `None`: it was
    trashed-and-purged, re-run, or already settled by another run)."""
    receipt = (
        Receipt.objects.select_for_update(of=('self',))
        .select_related('user')
        .filter(pk=receipt_id, status=ReceiptStatus.PROCESSING, attempt_id=attempt_id)
        .first()
    )
    return receipt


def _apply_result(receipt_id: object, attempt_id: UUID, extracted: ai.ExtractedReceipt) -> None:
    now = timezone.now()
    with transaction.atomic():
        receipt = _lock_current(receipt_id, attempt_id)
        if receipt is None:
            return
        receipt.ai_result = extracted.model_dump(mode='json')
        receipt.ai_confidence = extracted.confidence
        receipt.next_attempt_at = None
        receipt.processing_started_at = None
        receipt.last_error = ''

        if extracted.receipt_count > 1:
            receipt.status = ReceiptStatus.FAILED
            receipt.failure_reason = FailureReason.MULTIPLE_RECEIPTS
        elif not extracted.is_receipt or extracted.receipt_count == 0:
            receipt.status = ReceiptStatus.FAILED
            receipt.failure_reason = FailureReason.NOT_A_RECEIPT
        else:
            _fill_fields(receipt, extracted)
            edited = set(receipt.user_edited_fields or [])
            confident = extracted.confidence >= LOW_CONFIDENCE_THRESHOLD or all(
                name in edited for name in REQUIRED_FIELDS
            )
            if receipt.missing_required_fields or not confident:
                receipt.status = ReceiptStatus.NEEDS_REVIEW
                receipt.processed_at = None
            else:
                receipt.status = ReceiptStatus.PROCESSED
                receipt.processed_at = now
            receipt.failure_reason = ''
            if receipt.deleted_at is None:
                refresh_duplicate(receipt)

        receipt.save(update_fields=_SAVE_FIELDS)
        queue_attention_email_if_settled(receipt)


def _fill_fields(receipt: Receipt, extracted: ai.ExtractedReceipt) -> None:
    """Copy the extracted values onto `receipt`, except the fields the user edited."""
    edited = set(receipt.user_edited_fields or [])
    account = get_account(receipt.user)
    categories = {
        category.name.casefold(): category
        for category in ReceiptCategory.objects.filter(user_id=receipt.user_id)
    }
    values = {
        'vendor': extracted.vendor or '',
        'amount': extracted.amount,
        'currency': extracted.currency or account.default_currency,
        'receipt_date': extracted.receipt_date,
        'document_type': extracted.document_type or '',
        'document_number': extracted.document_number or '',
        'payment_method': extracted.payment_method or '',
        'category': categories.get((extracted.category or '').casefold()),
    }
    for name in EXTRACTED_FIELDS:
        if name not in edited:
            setattr(receipt, name, values[name])


# -- 5-6. Failures, retries, refunds --------------------------------------------------------------


def _refund_locked(receipt: Receipt) -> None:
    """Give back what the current attempt cost, once (compare-and-swap on `refunded_at`)."""
    if not receipt.credits_charged or receipt.charged_attempt_id != receipt.attempt_id:
        return
    now = timezone.now()
    won = Receipt.objects.filter(pk=receipt.pk, refunded_at__isnull=True).update(refunded_at=now)
    if not won:
        return
    receipt.refunded_at = now
    add_credits(
        receipt.user,
        receipt.credits_charged,
        tx_type=CreditTransactionType.REFUND,
        description=_description('Receipt could not be processed', receipt),
        source='receipts',
    )


def _fail_locked(receipt: Receipt, reason: str, detail: str = '', *, refund: bool = False) -> None:
    receipt.status = ReceiptStatus.FAILED
    receipt.failure_reason = reason
    receipt.next_attempt_at = None
    receipt.processing_started_at = None
    receipt.last_error = detail[:255]
    if refund:
        _refund_locked(receipt)
    receipt.save(update_fields=_SAVE_FIELDS)
    queue_attention_email_if_settled(receipt)


def _fail(
    receipt_id: object,
    attempt_id: UUID,
    reason: str,
    detail: str = '',
    *,
    refund: bool = True,
) -> None:
    """Fail the receipt (if it is still this attempt's). By default refunds: this is for failures
    where no AI answer exists (see the module docstring)."""
    with transaction.atomic():
        receipt = _lock_current(receipt_id, attempt_id)
        if receipt is not None:
            _fail_locked(receipt, reason, detail, refund=refund)


def _retry_or_fail_locked(receipt: Receipt, detail: str) -> None:
    config = ReceiptsSettings.load()
    receipt.attempts += 1
    if receipt.attempts >= config.max_ai_attempts:
        _fail_locked(receipt, FailureReason.EXTRACTION_ERROR, detail, refund=True)
        return
    receipt.status = ReceiptStatus.RECEIVED
    receipt.next_attempt_at = timezone.now() + retry_delay(receipt.attempts)
    receipt.processing_started_at = None
    receipt.last_error = detail[:255]
    receipt.save(update_fields=_SAVE_FIELDS)


def _transient_failure(receipt_id: object, attempt_id: UUID, detail: str) -> None:
    """A try failed in a way that may work later: back off, or fail for good after the last."""
    with transaction.atomic():
        receipt = _lock_current(receipt_id, attempt_id)
        if receipt is not None:
            _retry_or_fail_locked(receipt, detail)


def requeue_stuck(receipt_id: object, older_than: timedelta) -> bool:
    """For `retry_due`: a receipt that has been `processing` for longer than `older_than` lost its
    worker. Count it as a failed try (so an input that keeps killing the worker ends up failed
    instead of looping forever). Returns whether it was stuck."""
    cutoff = timezone.now() - older_than
    with transaction.atomic():
        receipt = (
            Receipt.objects.select_for_update(of=('self',))
            .select_related('user')
            .filter(
                pk=receipt_id,
                status=ReceiptStatus.PROCESSING,
                processing_started_at__lt=cutoff,
            )
            .first()
        )
        if receipt is None:
            return False
        _retry_or_fail_locked(receipt, 'Processing timed out')
        return True
