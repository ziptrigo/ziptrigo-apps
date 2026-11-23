"""The credit usage report (issue #347): what each app used, per day, week, month, quarter or year.

Read from the ledger (`CreditTransaction`) with no table of its own. **Credits used** are what the
ledger took *out*: spends, minus refunds (a refund is subtracted in the period it was made, so a
period can come out negative), plus a negative admin adjustment. Credits that came *in* (a
purchase, a promo code, the welcome credits, a positive adjustment) are not usage; the report only
sums them by type for a summary line.

A row belongs to an app by `CreditTransaction.source`. A source that is not a registered product
(blank for a manual adjustment, or the label of an app that has gone) is `other`. Days are the
account's own (`user_tz`): a spend at 23:30 in Los Angeles is that day's, whatever UTC says.

`build_report` is two queries: one aggregate over the periods and sources, one for the credits added.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q, Sum
from django.db.models.functions import (
    TruncDay,
    TruncMonth,
    TruncQuarter,
    TruncWeek,
    TruncYear,
)

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_tz
from apps.core.datetime_format import MONTH_ABBR, DisplayFormats, format_date
from apps.core.products import get_products

from ..models import CreditTransaction, CreditTransactionType
from .usage_ranges import (
    GROUP_KEYS,
    MAX_PERIODS,
    PeriodLimitError,
    count_periods,
    period_start,
    period_starts,
)

#: The label of everything that is no registered product.
OTHER = 'other'
OTHER_NAME = 'Other'
OTHER_ICON = 'fas fa-circle-question'

#: How many colours the chart palette has (`--c-chart-1` to `--c-chart-8` in core/css/site.css).
CHART_SLOTS = 8

_TRUNC = {
    'day': TruncDay,
    'week': TruncWeek,  # Django's week starts on Monday
    'month': TruncMonth,
    'quarter': TruncQuarter,
    'year': TruncYear,
}


@dataclass(frozen=True, slots=True)
class UsageApp:
    """One app (or `other`) with usage in the report's interval."""

    label: str
    name: str
    icon: str
    slot: int | None
    """The chart colour (1 to `CHART_SLOTS`, by the app's place in the product registry, so it
    keeps its colour on every page and whatever is filtered); `None` for `other`."""
    used: int
    """Net credits used over the whole interval (spends minus refunds)."""
    shown: bool
    """Whether the app filter lets it through (tiles, chart and table count only these)."""

    @property
    def color(self) -> str:
        """The CSS value of its chart colour (a variable of core/css/site.css, light and dark)."""
        return f'var(--c-chart-{self.slot})' if self.slot else 'var(--c-chart-other)'


@dataclass(frozen=True, slots=True)
class UsagePeriod:
    start: date
    """The first day of the period (a whole period, even when the interval cuts it short)."""
    amounts: dict[str, int]
    """Net credits used per app label, for every app of the report."""
    total: int
    """The sum over the shown apps."""


@dataclass(frozen=True, slots=True)
class UsageAdded:
    type: str
    label: str
    credits: int


@dataclass(frozen=True, slots=True)
class UsageReport:
    first: date
    last: date
    group: str
    apps: tuple[UsageApp, ...]
    """The apps that have usage, in registry order, `other` last."""
    periods: tuple[UsagePeriod, ...]
    """Every period of the interval, oldest first, empty ones included."""
    used: int
    refunded: int
    """Credits refunded in the interval (shown apps); already subtracted from `used`."""
    added: tuple[UsageAdded, ...]
    peak: int
    """The highest period total (0 when none is above zero)."""
    average: float
    """`used` per period."""

    @property
    def shown_apps(self) -> tuple[UsageApp, ...]:
        return tuple(app for app in self.apps if app.shown)

    @property
    def added_total(self) -> int:
        return sum(item.credits for item in self.added)


def registry() -> dict[str, tuple[str, str, int]]:
    """The registered products as `{label: (name, icon, chart slot)}`, in registry order."""
    return {
        product.label: (product.name, product.icon, index % CHART_SLOTS + 1)
        for index, product in enumerate(get_products())
    }


def app_name(label: str) -> str:
    """The display name of the registered app a ledger row's `source` names; `''` for none (a
    manual adjustment, or a source that is no product, like the promo codes' `billing`)."""
    entry = registry().get(label) if label else None
    return entry[0] if entry else ''


def first_transaction_date(user: User) -> date | None:
    """The date of the user's first ledger row in their time zone, or `None` for none."""
    first = (
        CreditTransaction.objects.filter(user=user)
        .order_by('created_at', 'id')
        .values_list('created_at', flat=True)
        .first()
    )
    return first.astimezone(user_tz(user)).date() if first else None


def period_label(
    start: date, group: str, formats: DisplayFormats | None = None, *, short: bool = False
) -> str:
    """The name of the period starting on `start`: the date for a day, `Week of <date>`,
    `Nov 2026`, `Q4 2026` or `2026`. `short` is for a chart axis: dates without the year (and no
    "Week of")."""
    match group:
        case 'day':
            return format_date(start, 'compact' if short else 'full', formats)
        case 'week':
            text = format_date(start, 'compact' if short else 'full', formats)
            return text if short else f'Week of {text}'
        case 'month':
            return f'{MONTH_ABBR[start.month - 1]} {start.year}'
        case 'quarter':
            return f'Q{(start.month - 1) // 3 + 1} {start.year}'
        case 'year':
            return str(start.year)
    raise ValueError(f'Unknown grouping: {group}')


def _bounds(user: User, first: date, last: date) -> tuple[datetime, datetime]:
    """The instants the interval covers: local midnight of `first` up to local midnight after
    `last`."""
    return local_datetime(user, first, None), local_datetime(user, last + timedelta(days=1), None)


def _used_rows(user: User, tz: ZoneInfo, first: date, last: date, group: str):
    start, end = _bounds(user, first, last)
    outgoing = Q(type__in=[CreditTransactionType.SPEND, CreditTransactionType.REFUND]) | Q(
        type=CreditTransactionType.ADJUSTMENT, amount__lt=0
    )
    return (
        CreditTransaction.objects.filter(user=user, created_at__gte=start, created_at__lt=end)
        .filter(outgoing)
        .annotate(period=_TRUNC[group]('created_at', tzinfo=tz))
        .order_by()
        .values('period', 'source')
        .annotate(
            net=Sum('amount'),
            refunds=Sum('amount', filter=Q(type=CreditTransactionType.REFUND)),
        )
    )


def _added(user: User, first: date, last: date) -> tuple[UsageAdded, ...]:
    start, end = _bounds(user, first, last)
    rows = (
        CreditTransaction.objects.filter(
            user=user, created_at__gte=start, created_at__lt=end, amount__gt=0
        )
        .exclude(type__in=[CreditTransactionType.SPEND, CreditTransactionType.REFUND])
        .order_by()
        .values('type')
        .annotate(total=Sum('amount'))
    )
    labels = dict(CreditTransactionType.choices)
    order = {value: index for index, (value, _label) in enumerate(CreditTransactionType.choices)}
    added = [
        UsageAdded(
            type=row['type'], label=str(labels.get(row['type'], row['type'])), credits=row['total']
        )
        for row in rows
    ]
    return tuple(sorted(added, key=lambda item: order.get(item.type, 99)))


def check_period_count(first: date, last: date, group: str) -> int:
    """How many periods `group` makes of `first` to `last`; `PeriodLimitError` over `MAX_PERIODS`
    (raised from the dates alone, before any query)."""
    if group not in GROUP_KEYS:
        raise ValueError(f'Unknown grouping: {group}')
    count = count_periods(first, last, group)
    if count > MAX_PERIODS:
        raise PeriodLimitError(count, group)
    return count


def build_report(
    user: User,
    *,
    first: date,
    last: date,
    group: str,
    apps: Collection[str] | None = None,
    lenient: bool = False,
) -> UsageReport:
    """The usage of `user` from `first` to `last` (inclusive local dates) grouped by `group`.

    `apps` limits the tiles, chart and table to those app labels (`other` included); the report
    still lists every app with usage, with `shown` telling which are counted. A filter that names
    no app with usage shows nothing, or with `lenient` (the web page) is ignored and shows every
    app. Raises `PeriodLimitError` over `MAX_PERIODS` periods, before any query.
    """
    check_period_count(first, last, group)
    tz = user_tz(user)
    known = registry()

    amounts: dict[date, dict[str, int]] = {}
    net_by_app: dict[str, int] = {}
    refunds_by_app: dict[str, int] = {}
    for row in _used_rows(user, tz, first, last, group):
        label = row['source'] if row['source'] in known else OTHER
        day = row['period'].astimezone(tz).date()
        net = -row['net']
        bucket = amounts.setdefault(day, {})
        bucket[label] = bucket.get(label, 0) + net
        net_by_app[label] = net_by_app.get(label, 0) + net
        refunds_by_app[label] = refunds_by_app.get(label, 0) + (row['refunds'] or 0)

    labels = [label for label in known if label in net_by_app]
    if OTHER in net_by_app:
        labels.append(OTHER)
    if not apps:
        shown = set(labels)
    else:
        shown = {label for label in apps if label in net_by_app}
        if not shown and lenient:
            shown = set(labels)

    report_apps = tuple(
        UsageApp(
            label=label,
            name=OTHER_NAME if label == OTHER else known[label][0],
            icon=OTHER_ICON if label == OTHER else known[label][1],
            slot=None if label == OTHER else known[label][2],
            used=net_by_app[label],
            shown=label in shown,
        )
        for label in labels
    )

    periods = []
    for start in period_starts(first, last, group):
        per_app = {label: amounts.get(start, {}).get(label, 0) for label in labels}
        periods.append(
            UsagePeriod(
                start=start,
                amounts=per_app,
                total=sum(per_app[label] for label in labels if label in shown),
            )
        )

    used = sum(net_by_app[label] for label in labels if label in shown)
    refunded = sum(refunds_by_app[label] for label in labels if label in shown)
    return UsageReport(
        first=first,
        last=last,
        group=group,
        apps=report_apps,
        periods=tuple(periods),
        used=used,
        refunded=refunded,
        added=_added(user, first, last),
        peak=max([0, *(period.total for period in periods)]),
        average=round(used / len(periods), 1) if periods else 0.0,
    )


__all__ = [
    'CHART_SLOTS',
    'OTHER',
    'UsageAdded',
    'UsageApp',
    'UsagePeriod',
    'UsageReport',
    'app_name',
    'build_report',
    'check_period_count',
    'first_transaction_date',
    'period_label',
    'period_start',
    'registry',
]
