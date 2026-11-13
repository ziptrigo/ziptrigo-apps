"""What file transfer costs, for the Costs page (`apps.core.costs`)."""

from django.db.models import Count, Q, Sum
from django.template.defaultfilters import filesizeformat

from apps.accounts.models import User
from apps.billing.services import get_storage_price_per_gb_per_day
from apps.core.costs import (
    CostSection,
    credits_text,
    daily_rate_text,
    daily_storage_rate,
    free,
    plural,
    storage,
)

from ..models import FileTransferSettings, Transfer, TransferStatus
from .metering import MIN_BALANCE_TO_REENABLE
from .send import MIN_BALANCE_TO_SEND


def cost_section(user: User) -> CostSection:
    config = FileTransferSettings.load()

    lines = [
        free(
            'Send a transfer',
            f'You need at least {credits_text(MIN_BALANCE_TO_SEND)} to start one. Recipients, '
            'resends and downloads are free.',
        ),
        storage(
            'Keep the files available',
            'Each transfer, for its size, while it is active.',
            unit='per day',
        ),
        free('"Download all" as a zip'),
    ]
    if config.anonymous_enabled:
        lines.append(free('Send without an account', 'Smaller limits, no credits involved.'))

    # What the daily job bills (`jobs.meter_transfers`): active, owned, not held for review. It
    # accrues `price x size` a day and spends only the whole credits (`storage_charge`), so the
    # rate shown is the fractional one, not `daily_cost` (the re-enable threshold, at least 1).
    is_billed = Q(status=TransferStatus.ACTIVE, held_for_review_at__isnull=True)
    totals = Transfer.objects.filter(owner=user).aggregate(
        billed=Count('pk', filter=is_billed),
        size=Sum('size_bytes', filter=is_billed),
        suspended=Count('pk', filter=Q(status=TransferStatus.SUSPENDED)),
    )
    if totals['billed']:
        size = totals['size'] or 0
        rate = daily_storage_rate(size, get_storage_price_per_gb_per_day())
        usage = [
            f'You have {plural(totals["billed"], "active transfer")} ({filesizeformat(size)}): '
            f'{daily_rate_text(rate)}.'
        ]
    else:
        usage = ['You have no active transfers, so nothing is charged for them.']
    suspended = totals['suspended']
    if suspended:
        verb = 'is' if suspended == 1 else 'are'
        usage.append(f'{plural(suspended, "transfer")} {verb} suspended until you top up.')

    return CostSection(
        lines=lines,
        usage=usage,
        footnote=f'Out of credits: a transfer is suspended and its files are kept '
        f'{plural(config.suspension_grace_days, "day")}. Topping up turns it back on once your '
        f'balance covers a day of its storage (at least {credits_text(MIN_BALANCE_TO_REENABLE)}).',
    )
