"""Template helpers of the file transfer pages (issue #306)."""

import math

from django import template

from ..models import Transfer, UploadAudience

register = template.Library()


@register.simple_tag
def transfer_qr_data_uri(transfer: Transfer) -> str:
    """The `data:` URI of the PNG QR code of a transfer's (or request's) link, the one the
    download page shows (`views.download.download_qr_data_uri`)."""
    from ..views.download import download_qr_data_uri

    return download_qr_data_uri(transfer)


@register.filter
def gb_input(num_bytes: int | None) -> str:
    """A byte limit as the settings form's "Max total size (GB)" field shows it: rounded *up* to
    two decimals (so posting the form unchanged never lowers it below what is already uploaded),
    without trailing zeros; empty for "the site maximum"."""
    if not num_bytes:
        return ''
    gb = math.ceil(num_bytes / 1024**3 * 100) / 100
    return f'{gb:.2f}'.rstrip('0').rstrip('.')


@register.simple_tag
def audience_choices() -> list[tuple[str, str]]:
    """The "Who can upload" choices of a file request, as `(value, label)` pairs."""
    return list(UploadAudience.choices)
