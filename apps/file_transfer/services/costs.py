"""What file transfer costs, for the Costs page (`apps.core.costs`)."""

from django.template.defaultfilters import filesizeformat

from apps.accounts.models import User
from apps.billing.services import get_storage_price_per_gb_per_day
from apps.core.costs import CostSection, credits_text, free, plural, storage

from ..models import FileTransferSettings, Transfer, TransferStatus
from .metering import MIN_BALANCE_TO_REENABLE, daily_cost
from .send import MIN_BALANCE_TO_SEND


def cost_section(user: User) -> CostSection:
    config = FileTransferSettings.load()
    price = get_storage_price_per_gb_per_day()

    lines = [
        free(
            'Send a transfer',
            f'You need at least {credits_text(MIN_BALANCE_TO_SEND)} to start one. Recipients, '
            'resends and downloads are free.',
        ),
        storage(
            'Keep the files available',
            f'Each transfer, while it is active: at least {credits_text(MIN_BALANCE_TO_REENABLE)} '
            'a day per transfer.',
            unit='per day',
        ),
        free('"Download all" as a zip'),
    ]
    if config.anonymous_enabled:
        lines.append(free('Send without an account', 'Smaller limits, no credits involved.'))

    # What the daily job bills (`jobs.meter_transfers`): active, owned, not held for review.
    billed = list(
        Transfer.objects.filter(
            owner=user, status=TransferStatus.ACTIVE, held_for_review_at__isnull=True
        ).only('size_bytes')
    )
    if billed:
        size = sum(transfer.size_bytes for transfer in billed)
        per_day = sum(daily_cost(transfer, price) for transfer in billed)
        usage = [
            f'You have {plural(len(billed), "active transfer")} ({filesizeformat(size)}): '
            f'{credits_text(per_day)} a day.'
        ]
    else:
        usage = ['You have no active transfers, so nothing is charged for them.']
    suspended = Transfer.objects.filter(owner=user, status=TransferStatus.SUSPENDED).count()
    if suspended:
        usage.append(
            f'{plural(suspended, "transfer")} {"is" if suspended == 1 else "are"} suspended '
            'until you top up.'
        )

    return CostSection(
        lines=lines,
        usage=usage,
        footnote=f'Out of credits: a transfer is suspended and its files are kept '
        f'{plural(config.suspension_grace_days, "day")}. Topping up turns it back on.',
    )
