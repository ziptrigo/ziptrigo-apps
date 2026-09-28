from ..models import Transfer


def transfer_display_name(transfer: Transfer) -> str:
    """The dashboard/email name for a transfer: its first file's name (spec section 2 -- there's
    no title field). See `Transfer.display_name` (the same logic, directly usable from templates)."""
    return transfer.display_name
