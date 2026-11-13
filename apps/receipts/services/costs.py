"""What Receipts costs, for the Costs page (`apps.core.costs`)."""

from apps.accounts.models import User
from apps.billing.services import get_storage_price_per_gb_per_day
from apps.core.costs import CostSection, charge, free, storage, storage_usage

from ..models import ReceiptsAccount, ReceiptsSettings


def cost_section(user: User) -> CostSection:
    config = ReceiptsSettings.load()
    lines = [
        charge(
            'Read a receipt with AI',
            config.price_per_file,
            'per file',
            "Uploaded or emailed in. A file that can't be read is refunded.",
        ),
        charge('Re-run a receipt', config.price_per_file, 'per run'),
        storage(
            'Store receipts',
            'Originals, previews and the emails they came in, receipts in the Trash included.',
            unit='per day',
        ),
        free('Edit, search, export to CSV, vendors and categories'),
    ]

    # Read only: opening the Costs page must not create a Receipts account.
    account = ReceiptsAccount.objects.filter(user=user).only('stored_bytes', 'frozen_at').first()
    usage = []
    if account is not None and account.stored_bytes:
        usage.append(storage_usage(account.stored_bytes, get_storage_price_per_gb_per_day()))
    if account is not None and account.frozen_at is not None:
        usage.append('Storage is paused until you top up.')

    return CostSection(
        lines=lines,
        usage=usage,
        footnote='Out of credits: storage pauses and nothing is deleted. You can still read, edit '
        'and export; uploads, downloads and reading new receipts wait until you top up.',
    )
