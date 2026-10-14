"""Template helpers shared by the Receipts pages: the status pill and a few formatters.

Load with `{% load receipts_ui %}`.
"""

from django import template
from django.utils.html import format_html

from ..models import FailureReason, ReceiptStatus

register = template.Library()

# label, Font Awesome icon, text classes, border classes, icon classes. The dot/icon and the border use
# the -600 (light) / -400 (dark) steps from the design system; the label text is -700 on light for
# green and amber because -600 on white is only 3.3:1 / 3.2:1 (docs/ui-design/README.md, "Status
# pills").
_TONES = {
    'green': (
        'text-green-700 dark:text-green-400',
        'border-green-600/40 dark:border-green-400/40',
        'text-green-600 dark:text-green-400',
    ),
    'amber': (
        'text-amber-700 dark:text-amber-400',
        'border-amber-600/40 dark:border-amber-400/40',
        'text-amber-600 dark:text-amber-400',
    ),
    'blue': (
        'text-blue-600 dark:text-blue-400',
        'border-blue-600/40 dark:border-blue-400/40',
        'text-blue-600 dark:text-blue-400',
    ),
    'red': (
        'text-red-600 dark:text-red-400',
        'border-red-600/40 dark:border-red-400/40',
        'text-red-600 dark:text-red-400',
    ),
    'gray': (
        'text-gray-600 dark:text-gray-400',
        'border-gray-400/60 dark:border-gray-500/60',
        'text-gray-500 dark:text-gray-400',
    ),
}

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
    """The coloured pill for a receipt status (`receipt.status`)."""
    label, icon, tone = _PILLS.get(status, (str(status).replace('_', ' ').capitalize(), '', 'gray'))
    text, border, icon_color = _TONES[tone]
    return format_html(
        '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border '
        '{} {} px-2 py-0.5 text-xs font-medium">'
        '<i class="{} {}" aria-hidden="true"></i>{}</span>',
        text,
        border,
        icon,
        icon_color,
        label,
    )


@register.simple_tag
def vendor_chip(vetted: bool, short: bool = False) -> str:
    """The "Vetted" / "New vendor" chip of a vendor (`short`: just "New", for the Vendors page).
    Neutral gray for a vendor nobody confirmed yet, so it doesn't read as a problem."""
    if vetted:
        label, icon, tone = 'Vetted', 'fas fa-circle-check', 'green'
    else:
        label, icon, tone = ('New' if short else 'New vendor'), 'fas fa-seedling', 'gray'
    text, border, icon_color = _TONES[tone]
    return format_html(
        '<span class="inline-flex items-center gap-1 whitespace-nowrap rounded-full border '
        '{} {} px-2 py-0.5 text-xs font-medium">'
        '<i class="{} {}" aria-hidden="true"></i>{}</span>',
        text,
        border,
        icon,
        icon_color,
        label,
    )


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
