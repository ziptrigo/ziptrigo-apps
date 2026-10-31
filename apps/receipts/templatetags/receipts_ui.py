"""Template helpers shared by the Receipts pages: the status pill and a few formatters.

Load with `{% load receipts_ui %}`.
"""

from django import template

from apps.core.templatetags.core_ui import pill_html

from ..models import FailureReason, ReceiptStatus

register = template.Library()

_PILLS = {
    ReceiptStatus.PROCESSED: ('Processed', 'fas fa-check', 'green'),
    ReceiptStatus.NEEDS_REVIEW: ('Needs review', 'fas fa-triangle-exclamation', 'amber'),
    ReceiptStatus.RECEIVED: ('Received', 'fas fa-inbox', 'blue'),
    ReceiptStatus.PROCESSING: ('Processing', 'fas fa-spinner fa-spin', 'blue'),
    ReceiptStatus.WAITING_FOR_CREDITS: ('Waiting for credits', 'fas fa-coins', 'gray'),
    ReceiptStatus.FAILED: ('Failed', 'fas fa-circle-xmark', 'red'),
}


@register.simple_tag
def status_pill(status: str) -> str:
    """The pill for a receipt status (`receipt.status`)."""
    label, icon, tone = _PILLS.get(status, (str(status).replace('_', ' ').capitalize(), '', 'gray'))
    return pill_html(tone, label, icon=icon)


@register.simple_tag
def vendor_chip(vetted: bool, short: bool = False) -> str:
    """The "Vetted" / "New vendor" chip of a vendor (`short`: just "New", for the Vendors page).
    Neutral gray for a vendor nobody confirmed yet, so it doesn't read as a problem."""
    if vetted:
        return pill_html('green', 'Vetted', icon='fas fa-circle-check')
    return pill_html('gray', 'New' if short else 'New vendor', icon='fas fa-seedling')


#: What to tell the user about a failed receipt, and the next step. `refundable` reasons mean no AI
#: answer was paid for (see services.processing), so the text can say the credits came back.
FAILURE_HELP = {
    FailureReason.MULTIPLE_RECEIPTS: (
        'This file seems to contain more than one receipt.',
        'Upload each receipt as its own file.',
    ),
    FailureReason.NOT_A_RECEIPT: (
        "This doesn't look like a receipt or an invoice.",
        'If it is one, run the extraction again. Otherwise move it to the trash.',
    ),
    FailureReason.UNREADABLE: (
        "We couldn't read this file.",
        'Try a sharper photo or scan and upload it again.',
    ),
    FailureReason.UNSUPPORTED_TYPE: (
        "This file type isn't supported.",
        'Upload a PDF, JPG, PNG, HEIC or WebP file instead.',
    ),
    FailureReason.TOO_LARGE: (
        'This file is too large or too complex to read.',
        'Upload a smaller or simpler version of it, or a photo or scan of the receipt.',
    ),
    FailureReason.EXTRACTION_ERROR: (
        "We couldn't process this receipt.",
        'Run the extraction again in a moment.',
    ),
}


@register.filter
def failure_reason_text(reason: str) -> str:
    """The first sentence of the explanation for a `failure_reason`."""
    return FAILURE_HELP.get(reason, ("We couldn't process this receipt.", ''))[0]


@register.filter
def failure_next_step(reason: str) -> str:
    """The next step for a `failure_reason`."""
    return FAILURE_HELP.get(reason, ('', 'Run the extraction again in a moment.'))[1]


_FIELD_LABELS = {'vendor': 'vendor', 'amount': 'amount', 'receipt_date': 'date'}


@register.filter
def missing_fields(receipt) -> str:
    """ "vendor, amount, date" for the required fields a receipt still lacks."""
    return ', '.join(_FIELD_LABELS[name] for name in receipt.missing_required_fields)


@register.filter
def human_size(num_bytes: int) -> str:
    """ "340 KB", "2.1 MB" (binary units, like the file transfer page)."""
    kb, mb = 1024, 1024**2
    if num_bytes >= mb:
        return f'{num_bytes / mb:.1f}'.removesuffix('.0') + ' MB'
    return f'{max(round(num_bytes / kb), 1) if num_bytes else 0} KB'
