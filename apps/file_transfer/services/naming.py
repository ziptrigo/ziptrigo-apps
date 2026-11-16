from ..models import Transfer


def transfer_display_name(transfer: Transfer) -> str:
    """The dashboard/email-body name for a transfer: the owner's optional title (issue #318), else
    its first file's name. See `Transfer.display_name` (the same logic, directly usable from
    templates)."""
    return transfer.display_name


def transfer_file_label(transfer: Transfer) -> str:
    """The name for an email *subject*: the first file's name, never the title, because a title is
    free text the owner typed (the repo keeps those out of subjects). See `Transfer.file_label`."""
    return transfer.file_label
