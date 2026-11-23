"""`GET /api/billing/usage`: what each app used, per day, week, month, quarter or year (issue #347).

The caller's own usage, from the very service the Usage tab of the Credits page reads, so the
numbers agree. Unlike the web page, which falls back to defaults for a value it can't read, the API
is strict: a request it can't answer as asked is a 400 that says why.
"""

from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.services.timezones import user_today, user_tz
from apps.core import ratelimit
from apps.core.datetime_format import PUBLIC

from ..forms import parse_date
from ..schemas import BillingUsageSchema
from ..services import get_balance, usage
from ..services.usage_ranges import (
    CUSTOM,
    DEFAULT_RANGE,
    GROUP_KEYS,
    RANGE_KEYS,
    PeriodLimitError,
    auto_group,
    needs_first_transaction,
    resolve,
)
from .router import router


def _date(name: str, value: str | None):
    if value is None or value == '':
        return None
    parsed = parse_date(value)
    if parsed is None:
        raise HttpError(400, f'{name} must be a date like 2026-11-23.')
    return parsed


@router.get('/usage', response=BillingUsageSchema, auth=JWTAuth(), summary='Credit usage')
def credit_usage(
    request,
    range: str = DEFAULT_RANGE,  # shadows the builtin: ty rejects ninja's Query(alias=...)
    date_from: str | None = None,
    date_to: str | None = None,
    group: str | None = None,
    app: str | None = None,
):
    """The credits the caller's apps used, net of refunds, over an interval.

    **Used** is what the ledger took out: spends, minus refunds (a refund is subtracted in the
    period it was made, so a period can be negative), plus negative admin adjustments. Credits
    that came in are not usage; `added` sums them by type. A charge whose source is no registered
    app is `other`. Days are in the account's time zone (`timezone`). A period is a whole day,
    week (Monday to Sunday), month, quarter or year, even when the interval cuts the first or
    last one short.

    `range` is a preset (`last_7_days`, `last_30_days` (default), `last_90_days`, `this_month`,
    `last_month`, `this_quarter`, `this_year`, `last_12_months`, `all_time`) or `custom` with
    `date_from` and `date_to` (`YYYY-MM-DD`); the ends of a custom range default to the first
    transaction and today, a future end is cut to today and ends given the wrong way round are put
    right. The answer says the dates it used. `app` limits the answer to some apps (their labels,
    or `other`): repeat the parameter (`app=receipts&app=todo`) or separate them with commas
    (`app=receipts,todo`); without it every app is counted. **400** for an unknown `range`,
    `group` or app, a malformed date, dates sent with a preset, and a grouping that would make
    more than 120 periods.
    """
    if range not in RANGE_KEYS:
        raise HttpError(400, f'range must be one of: {", ".join(RANGE_KEYS)}.')
    if group is not None and group != '' and group not in GROUP_KEYS:
        raise HttpError(400, f'group must be one of: {", ".join(GROUP_KEYS)}.')
    first_given, last_given = _date('date_from', date_from), _date('date_to', date_to)
    if range != CUSTOM and (first_given or last_given):
        raise HttpError(400, 'date_from and date_to can only be used with range=custom.')

    known = set(usage.registry()) | {usage.OTHER}
    # `app` is the parameter ninja documents; the value is read from the query string so that both
    # `?app=a&app=b` (what the Usage tab's address holds) and `?app=a,b` work.
    labels = [
        label.strip()
        for value in request.GET.getlist('app')
        for label in value.split(',')
        if label.strip()
    ]
    unknown = [label for label in labels if label not in known]
    if unknown:
        raise HttpError(400, f'Unknown app: {", ".join(unknown)}.')

    user = request.auth
    first_transaction = (
        usage.first_transaction_date(user) if needs_first_transaction(range, first_given) else None
    )
    first, last = resolve(range, user_today(user), first_given, last_given, first_transaction)
    chosen = group or auto_group(first, last)
    try:
        usage.check_period_count(first, last, chosen)
    except PeriodLimitError as exc:
        raise HttpError(400, exc.messages[0])

    ratelimit.enforce(ratelimit.hit_user(user, 'BILLING_USAGE_USER'))
    report = usage.build_report(user, first=first, last=last, group=chosen, apps=labels or None)
    shown = report.shown_apps
    return {
        'date_from': first,
        'date_to': last,
        'group': chosen,
        'timezone': user_tz(user).key,
        'balance': get_balance(user),
        'used': report.used,
        'refunded': report.refunded,
        'added': [{'type': item.type, 'credits': item.credits} for item in report.added],
        'apps': [{'app': a.label, 'name': a.name, 'used': a.used} for a in shown],
        'periods': [
            {
                'start': period.start,
                'label': usage.period_label(period.start, chosen, PUBLIC),
                'total': period.total,
                'apps': {a.label: period.amounts.get(a.label, 0) for a in shown},
            }
            for period in report.periods
        ],
    }
