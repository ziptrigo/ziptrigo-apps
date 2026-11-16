from ..models import Transfer


def transfer_display_name(transfer: Transfer) -> str:
    """The dashboard/email-body name for a transfer: the owner's optional title (issue #318), else
    its first file's name. See `Transfer.display_name` (the same logic, directly usable from
    templates)."""
    return transfer.display_name


def transfer_file_label(transfer: Transfer) -> str:
    """The name for an email *subject*. For a send, the first file's name and never its title,
    because a title is free text the owner typed (the repo keeps those out of subjects). A request
    has no file to name it by, so its owner-only emails (expiry, suspension, deleted) name it by its
    title, as they always did ("Untitled request" without one). See `Transfer.file_label`."""
    return transfer.file_label
