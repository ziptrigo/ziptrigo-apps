"""What a user can do to their receipts, inbox and categories -- shared by the web views and the
JWT API, so both enforce the same rules. Each function raises Django's `ValidationError` for bad
input (the web shows it as a 422, the API as a 400), `AccountFrozenError` /
`InsufficientCreditsError` where the API answers 402.

Callers pass objects they already fetched *for the requesting user* (a missing or foreign id is the
caller's 404); these functions do not re-check ownership of the objects they are handed, except to
make sure a category belongs to the receipt's owner.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError, get_balance

from ..models import (
    EXTRACTED_FIELDS,
    IN_PROGRESS_STATUSES,
    MAX_CATEGORY_NAME_LENGTH,
    MAX_NOTES_LENGTH,
    DocumentType,
    FailureReason,
    InboxCode,
    Receipt,
    ReceiptCategory,
    ReceiptsSettings,
    ReceiptStatus,
)
from . import lifecycle
from .accounts import get_account, is_frozen
from .addresses import parse_allowlist
from .ai import MAX_AMOUNT, mask_payment_method
from .currencies import normalize_currency
from .duplicates import refresh_duplicate
from .errors import AccountFrozenError
from .processing import enqueue_processing

#: Fields `update_receipt` accepts: the extracted ones, plus the user-only `notes`.
EDITABLE_FIELDS = (*EXTRACTED_FIELDS, 'notes')

MAX_CATEGORIES = 100

_NOT_REPROCESSABLE = (FailureReason.UNSUPPORTED_TYPE, FailureReason.TOO_LARGE)


# -- Editing a receipt ----------------------------------------------------------------------------


def _clean_amount(value: Any) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        amount = Decimal(str(value).replace(',', '').strip())
    except InvalidOperation, ValueError:
        raise ValidationError('Enter the amount as a number.') from None
    if not amount.is_finite():
        raise ValidationError('Enter the amount as a number.')
    if amount < 0:
        raise ValidationError('The amount cannot be negative.')
    if amount > MAX_AMOUNT:
        raise ValidationError('That amount is too large.')
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _clean_date(value: Any) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError('Enter the date as YYYY-MM-DD.') from None


def _clean_text(value: Any, max_length: int, label: str) -> str:
    text = '' if value is None else str(value).strip()
    if len(text) > max_length:
        raise ValidationError(f'{label} can be at most {max_length} characters.')
    return text


def _clean_category(user: User, value: Any) -> ReceiptCategory | None:
    if value is None or value == '':
        return None
    if isinstance(value, ReceiptCategory):
        category_id = value.pk
    else:
        try:
            category_id = int(value)
        except TypeError, ValueError:
            raise ValidationError('Unknown category.') from None
    category = ReceiptCategory.objects.filter(pk=category_id, user=user).first()
    if category is None:
        raise ValidationError('Unknown category.')
    return category


def clean_changes(user: User, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a partial update. Raises a field-keyed `ValidationError`."""
    cleaned: dict[str, Any] = {}
    errors: dict[str, list[str]] = {}
    for name, value in changes.items():
        try:
            match name:
                case 'vendor':
                    cleaned[name] = _clean_text(value, 255, 'Vendor')
                case 'amount':
                    cleaned[name] = _clean_amount(value)
                case 'currency':
                    text = '' if value is None else str(value).strip()
                    code = normalize_currency(text) if text else ''
                    if text and not code:
                        raise ValidationError('Enter a 3-letter currency code, like USD.')
                    cleaned[name] = code
                case 'receipt_date':
                    cleaned[name] = _clean_date(value)
                case 'document_type':
                    text = '' if value is None else str(value).strip().lower()
                    if text not in ('', *DocumentType.values):
                        raise ValidationError('Choose receipt or invoice.')
                    cleaned[name] = text
                case 'document_number':
                    cleaned[name] = _clean_text(value, 100, 'The number')
                case 'payment_method':
                    # Masked here too, so a full card number typed by hand is never stored.
                    cleaned[name] = mask_payment_method(
                        _clean_text(value, 100, 'The payment method')
                    )
                case 'category':
                    cleaned[name] = _clean_category(user, value)
                case 'notes':
                    notes = '' if value is None else str(value).replace('\r\n', '\n').strip()
                    if len(notes) > MAX_NOTES_LENGTH:
                        raise ValidationError(
                            f'Notes can be at most {MAX_NOTES_LENGTH} characters.'
                        )
                    cleaned[name] = notes
                case _:
                    raise ValidationError('This field cannot be edited.')
        except ValidationError as exc:
            errors[name] = list(exc.messages)
    if errors:
        raise ValidationError(errors)
    return cleaned


def update_receipt(receipt: Receipt, changes: Mapping[str, Any]) -> Receipt:
    """Apply a partial update from the user (only the keys present are touched).

    Records which extracted fields actually changed in `user_edited_fields`, so a later re-run of
    the extraction never overwrites them. `notes` is the user's own and is not tracked. When the
    three required fields (vendor, amount, receipt date) are all set, a `needs_review` receipt
    becomes `processed`; clearing one of them on a `processed` receipt sends it back to
    `needs_review`. A `failed` receipt keeps its status.

    Raises:
        ValidationError: a field is invalid, the receipt is in the Trash, or (except for notes) it
            is still being processed.
    """
    cleaned = clean_changes(receipt.user, changes)
    with transaction.atomic():
        locked = (
            Receipt.objects.select_for_update(of=('self',))
            .select_related('user')
            .get(pk=receipt.pk)
        )
        if locked.deleted_at is not None:
            raise ValidationError('Restore this receipt from the Trash to edit it.')
        if locked.status in IN_PROGRESS_STATUSES and set(cleaned) - {'notes'}:
            raise ValidationError('This receipt is still being processed; try again in a moment.')

        edited = list(locked.user_edited_fields or [])
        update_fields = set()
        for name, value in cleaned.items():
            if getattr(locked, name) == value:
                continue
            setattr(locked, name, value)
            update_fields.add(name)
            if name in EXTRACTED_FIELDS and name not in edited:
                edited.append(name)
        if not update_fields:
            return locked

        if edited != list(locked.user_edited_fields or []):
            locked.user_edited_fields = edited
            update_fields.add('user_edited_fields')

        if update_fields & set(EXTRACTED_FIELDS):
            if locked.status == ReceiptStatus.NEEDS_REVIEW and not locked.missing_required_fields:
                locked.status = ReceiptStatus.PROCESSED
                locked.processed_at = timezone.now()
                locked.failure_reason = ''
                update_fields |= {'status', 'processed_at', 'failure_reason'}
            elif locked.status == ReceiptStatus.PROCESSED and locked.missing_required_fields:
                locked.status = ReceiptStatus.NEEDS_REVIEW
                locked.processed_at = None
                update_fields |= {'status', 'processed_at'}
            if refresh_duplicate(locked):
                update_fields.add('possible_duplicate_of')

        update_fields.add('updated_at')
        locked.save(update_fields=sorted(update_fields))
        return locked


# -- Re-running, trash, duplicates ---------------------------------------------------------------


def reprocess(receipt: Receipt) -> Receipt:
    """Run the extraction again. Costs `price_per_file` credits again; fields the user edited and
    the notes are kept. The receipt goes back to `received` and is queued.

    Raises:
        AccountFrozenError: storage is unpaid.
        InsufficientCreditsError: the balance can't cover the price (so a client gets an immediate
            402 instead of a receipt silently waiting).
        ValidationError: it is in the Trash, already being processed, or can't be re-run (an
            unsupported or too-large file).
    """
    with transaction.atomic():
        locked = (
            Receipt.objects.select_for_update(of=('self',))
            .select_related('user')
            .get(pk=receipt.pk)
        )
        if locked.deleted_at is not None:
            raise ValidationError('Restore this receipt from the Trash first.')
        if locked.status in IN_PROGRESS_STATUSES:
            raise ValidationError('This receipt is already being processed.')
        if (
            locked.failure_reason in _NOT_REPROCESSABLE
            or not locked.uploaded
            or not locked.file_key
        ):
            raise ValidationError('This file cannot be processed again.')
        if is_frozen(locked.user):
            raise AccountFrozenError()
        price = ReceiptsSettings.load().price_per_file
        if price > 0 and get_balance(locked.user) < price:
            raise InsufficientCreditsError('Insufficient credits')

        locked.attempt_id = uuid.uuid4()
        locked.attempts = 0
        locked.status = ReceiptStatus.RECEIVED
        locked.failure_reason = ''
        locked.next_attempt_at = None
        locked.processing_started_at = None
        locked.last_error = ''
        locked.refunded_at = None
        locked.credits_charged = 0
        locked.save(
            update_fields=[
                'attempt_id',
                'attempts',
                'status',
                'failure_reason',
                'next_attempt_at',
                'processing_started_at',
                'last_error',
                'refunded_at',
                'credits_charged',
                'updated_at',
            ]
        )
        enqueue_processing(locked.pk)
        return locked


def trash(receipt: Receipt) -> Receipt:
    """Move to the Trash (still billed, restorable until `trash_days` pass). Receipts that were
    flagged as duplicates *of* this one lose the flag."""
    with transaction.atomic():
        now = timezone.now()
        # (`.update()` skips `auto_now`, so `updated_at` is set by hand throughout.)
        won = Receipt.objects.filter(pk=receipt.pk, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
        if won:
            Receipt.objects.filter(possible_duplicate_of_id=receipt.pk).update(
                possible_duplicate_of=None
            )
    receipt.refresh_from_db()
    return receipt


def restore(receipt: Receipt) -> Receipt:
    """Take a receipt back out of the Trash, re-checking it for duplicates, and queue it if it
    was never processed."""
    with transaction.atomic():
        won = Receipt.objects.filter(pk=receipt.pk, deleted_at__isnull=False).update(
            deleted_at=None, updated_at=timezone.now()
        )
        receipt.refresh_from_db()
        if won:
            if refresh_duplicate(receipt):
                receipt.save(update_fields=['possible_duplicate_of', 'updated_at'])
            if receipt.status == ReceiptStatus.RECEIVED and receipt.uploaded:
                enqueue_processing(receipt.pk)
    return receipt


def delete_forever(receipt: Receipt) -> None:
    """Delete a trashed receipt for good, now rather than when the retention period ends. Refused
    while the receipt is being processed: the run in flight would write a preview for a receipt
    that no longer exists."""
    if receipt.deleted_at is None:
        raise ValidationError('Move the receipt to the Trash first.')
    if Receipt.objects.filter(pk=receipt.pk, status=ReceiptStatus.PROCESSING).exists():
        raise ValidationError('This receipt is being processed; try again in a minute.')
    lifecycle.purge_receipt(receipt)


def dismiss_duplicate(receipt: Receipt) -> Receipt:
    """ "Not a duplicate": clear the flag and never raise it again for this receipt."""
    Receipt.objects.filter(pk=receipt.pk).update(
        possible_duplicate_of=None, duplicate_dismissed=True, updated_at=timezone.now()
    )
    receipt.refresh_from_db()
    return receipt


# -- Inbox ----------------------------------------------------------------------------------------


def regenerate_inbox_code(user: User) -> InboxCode:
    """Retire the active address immediately and issue a new one. The old code is kept forever
    and never reissued, so mail still sent to it is dropped rather than delivered to a stranger."""
    account = get_account(user)
    with transaction.atomic():
        InboxCode.objects.filter(account=account, active=True).update(
            active=False, retired_at=timezone.now()
        )
        return InboxCode.issue(account)


def update_inbox_settings(
    user: User,
    *,
    sender_allowlist: str | Iterable[str] | None = None,
    default_currency: str | None = None,
) -> None:
    """Set the accepted senders (a list, or the text the user typed; an empty one accepts anyone)
    and/or the default currency. `None` leaves a setting alone (pass `''` or `[]` to clear the
    allowlist).

    Raises:
        ValidationError: an allowlist entry isn't an address or `*@domain`, or the currency isn't
            an ISO 4217 code.
    """
    account = get_account(user)
    update_fields = []
    errors: dict[str, list[str]] = {}
    if sender_allowlist is not None:
        try:
            account.sender_allowlist = parse_allowlist(sender_allowlist)
            update_fields.append('sender_allowlist')
        except ValidationError as exc:
            errors['sender_allowlist'] = list(exc.messages)
    if default_currency is not None:
        code = normalize_currency(default_currency)
        if code:
            account.default_currency = code
            update_fields.append('default_currency')
        else:
            errors['default_currency'] = ['Enter a 3-letter currency code, like USD.']
    if errors:
        raise ValidationError(errors)
    if update_fields:
        account.save(update_fields=update_fields)


# -- Categories ----------------------------------------------------------------------------------


def _clean_category_name(user: User, name: str, *, exclude_pk: int | None = None) -> str:
    cleaned = re.sub(r'\s+', ' ', ''.join(ch for ch in name if unicodedata.category(ch) != 'Cc'))
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValidationError('Enter a category name.')
    if len(cleaned) > MAX_CATEGORY_NAME_LENGTH:
        raise ValidationError(
            f'A category name can be at most {MAX_CATEGORY_NAME_LENGTH} characters.'
        )
    taken = ReceiptCategory.objects.filter(user=user, name__iexact=cleaned)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise ValidationError('You already have a category with that name.')
    return cleaned


def list_categories(user: User) -> list[ReceiptCategory]:
    """The user's categories in their order, each with a `receipt_count` (trashed receipts
    included: deleting the category would uncategorize those too).

    Ordered explicitly: `Meta.ordering` is dropped from a query that aggregates, which would leave
    the order up to the database."""
    return list(
        ReceiptCategory.objects.filter(user=user)
        .annotate(receipt_count=Count('receipts'))
        .order_by('sort_order', 'name')
    )


def create_category(user: User, name: str) -> ReceiptCategory:
    get_account(user)  # seeds the defaults first, so the new one lands after them
    cleaned = _clean_category_name(user, name)
    if ReceiptCategory.objects.filter(user=user).count() >= MAX_CATEGORIES:
        raise ValidationError(f'You can have at most {MAX_CATEGORIES} categories.')
    last = ReceiptCategory.objects.filter(user=user).aggregate(last=Max('sort_order'))['last']
    return ReceiptCategory.objects.create(
        user=user, name=cleaned, sort_order=0 if last is None else last + 1
    )


def rename_category(category: ReceiptCategory, name: str) -> ReceiptCategory:
    category.name = _clean_category_name(category.user, name, exclude_pk=category.pk)
    category.save(update_fields=['name'])
    return category


def reorder_categories(user: User, ordered_ids: Iterable[int]) -> None:
    """Put the user's categories in the order given. Categories not mentioned keep their relative
    order after the listed ones; an id that isn't the user's is an error."""
    try:
        ids = [int(pk) for pk in ordered_ids]
    except TypeError, ValueError:
        raise ValidationError('Unknown category.') from None
    categories = {c.pk: c for c in ReceiptCategory.objects.filter(user=user)}
    unknown = [pk for pk in ids if pk not in categories]
    if unknown:
        raise ValidationError('Unknown category.')
    rest = [pk for pk in categories if pk not in ids]
    with transaction.atomic():
        for position, pk in enumerate([*dict.fromkeys(ids), *rest]):
            if categories[pk].sort_order != position:
                ReceiptCategory.objects.filter(pk=pk).update(sort_order=position)


def delete_category(category: ReceiptCategory) -> None:
    """Delete a category; its receipts become uncategorized (the FK is `SET_NULL`)."""
    category.delete()
