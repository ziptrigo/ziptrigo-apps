"""Possible-duplicate detection: another live (not trashed) receipt of the same user with the same
file contents (`sha256`), or with the same vendor, receipt date and amount."""

from ..models import Receipt


def find_duplicate(receipt: Receipt) -> Receipt | None:
    """The oldest other live receipt of `receipt`'s owner that looks like the same document, or
    `None`. A receipt already flagged as a duplicate *of this one* is not offered back, so two
    copies don't flag each other."""
    others = (
        Receipt.objects.filter(user_id=receipt.user_id, deleted_at__isnull=True)
        .exclude(pk=receipt.pk)
        .exclude(possible_duplicate_of_id=receipt.pk)
        .order_by('created_at', 'id')
    )
    if receipt.sha256:
        match = others.filter(sha256=receipt.sha256).first()
        if match is not None:
            return match
    if receipt.vendor and receipt.amount is not None and receipt.receipt_date:
        return others.filter(
            vendor__iexact=receipt.vendor,
            receipt_date=receipt.receipt_date,
            amount=receipt.amount,
        ).first()
    return None


def refresh_duplicate(receipt: Receipt) -> bool:
    """Re-run detection and set `receipt.possible_duplicate_of` (in memory; the caller saves it,
    including that field). Does nothing once the user dismissed the flag. Returns whether the
    value changed."""
    if receipt.duplicate_dismissed:
        return False
    match = find_duplicate(receipt)
    new_id = match.pk if match is not None else None
    if new_id == receipt.possible_duplicate_of_id:
        return False
    receipt.possible_duplicate_of = match
    receipt.possible_duplicate_of_id = new_id
    return True
