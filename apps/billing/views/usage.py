"""The Usage tab of the Credits page (issue #347): what each app used, as tiles, a chart and a table.

`usage_context` builds everything the page needs from the query string; `credits_usage_partial` is
the htmx fragment the filters re-fetch (`#usage-report`), pushing the page's own address to the
browser. Filters are a GET form: without JavaScript a button submits it.
"""

import math
from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today, user_tz
from apps.core import ratelimit
from apps.core.datetime_format import format_date
from apps.core.htmx import is_htmx

from ..forms import UsageParams, parse_params
from ..services import usage
from ..services.usage_ranges import (
    GROUPS,
    RANGES,
    PeriodLimitError,
    auto_group,
    needs_first_transaction,
    resolve,
)

#: The chart's own coordinate box; the SVG scales to its container (`preserveAspectRatio="none"`).
CHART_WIDTH = 600
CHART_HEIGHT = 192
#: The share of a period's slot its bar fills.
BAR_FILL = 0.76


def number(value: int | float) -> str:
    """`1284` as `1,284`; a float with one decimal at most (`12.5`), none when whole."""
    if isinstance(value, float):
        text = f'{value:,.1f}'
        return text[:-2] if text.endswith('.0') else text
    return f'{value:,}'


def nice_scale(peak: int) -> tuple[int, int]:
    """`(step, top)` for a y axis that holds `peak`: a round step and a top that is a multiple of
    it, with no more than five steps."""
    peak = max(peak, 1)
    step = 10 ** math.floor(math.log10(peak))
    top = math.ceil(peak / step) * step
    if top / step > 5:
        step *= 2
        top = math.ceil(peak / step) * step
    return step, top


@dataclass(frozen=True, slots=True)
class Segment:
    x: float
    y: float
    width: float
    height: float
    color: str
    title: str


def period_stack(report: usage.UsageReport, amounts: dict[str, int]) -> list[tuple]:
    """`[(app, drawn, reduced)]` for one period: the heights its segments are drawn to.

    A bar is drawn to `max(0, period.total)`, the figure the table, "Highest" line and the
    chart's sentence give. A refund for one app and a spend for another in the same period would
    otherwise stack to more than that total, so the negative amounts are taken off the positive
    segments, largest first, until the visible segments sum to the total (a period below zero has
    no bar). `reduced` says a segment was cut that way.
    """
    positive = [(app, max(0, amounts.get(app.label, 0))) for app in report.shown_apps]
    owed = sum(value for _app, value in positive) - max(
        0, sum(amounts.get(app.label, 0) for app in report.shown_apps)
    )
    drawn = {app.label: value for app, value in positive}
    cut: set[str] = set()
    for app, value in sorted(positive, key=lambda item: -item[1]):
        if owed <= 0:
            break
        take = min(value, owed)
        if take:
            drawn[app.label] = value - take
            cut.add(app.label)
            owed -= take
    return [(app, drawn[app.label], app.label in cut) for app, _value in positive]


def chart_context(report: usage.UsageReport) -> dict:
    """The stacked bars of `report` for the SVG: one bar per period, one segment per shown app
    with something left to draw (see `period_stack`: a bar's height is the period's total, never
    below zero), and a y scale made from the same totals as `report.peak`."""
    stacks = [period_stack(report, period.amounts) for period in report.periods]
    step, top = nice_scale(
        max([0, *(sum(value for _app, value, _cut in stack) for stack in stacks)])
    )
    count = max(len(report.periods), 1)
    slot_width = CHART_WIDTH / count
    bar_width = slot_width * BAR_FILL

    segments: list[Segment] = []
    for index, (period, stack) in enumerate(zip(report.periods, stacks)):
        label = usage.period_label(period.start, report.group)
        bottom = float(CHART_HEIGHT)
        for app, value, reduced in stack:
            if not value:
                continue
            height = value / top * CHART_HEIGHT
            bottom -= height
            note = ' (after refunds)' if reduced else ''
            segments.append(
                Segment(
                    x=round(index * slot_width + (slot_width - bar_width) / 2, 2),
                    y=round(bottom, 2),
                    width=round(bar_width, 2),
                    height=round(max(0.0, height - 0.6), 2),
                    color=app.color,
                    title=f'{label} · {app.name}: {number(value)}{note}',
                )
            )
    gridlines = [
        {'value': number(tick), 'y': round(CHART_HEIGHT - tick / top * CHART_HEIGHT, 2)}
        for tick in range(0, top + 1, step)
    ]
    labels = [usage.period_label(p.start, report.group, short=True) for p in report.periods]
    return {
        'segments': segments,
        'gridlines': gridlines,
        'y_labels': [line['value'] for line in reversed(gridlines)],
        'width': CHART_WIDTH,
        'height': CHART_HEIGHT,
        'x_first': labels[0] if labels else '',
        'x_mid': labels[len(labels) // 2] if len(labels) > 2 else '',
        'x_last': labels[-1] if len(labels) > 1 else '',
    }


def table_context(report: usage.UsageReport) -> dict:
    """The table: periods newest first, one column per shown app, a total column and row."""
    shown = report.shown_apps
    rows = []
    for period in reversed(report.periods):
        rows.append(
            {
                'label': usage.period_label(period.start, report.group),
                'cells': [_cell(period.amounts.get(app.label, 0)) for app in shown],
                'total': _cell(period.total),
            }
        )
    return {
        'apps': shown,
        'rows': rows,
        'totals': [_cell(app.used) for app in shown],
        'grand_total': _cell(report.used),
    }


def _cell(value: int) -> dict:
    return {'text': number(value) if value else '–', 'negative': value < 0, 'zero': value == 0}


def usage_context(request: HttpRequest, user, params: UsageParams) -> dict:
    """Everything the Usage tab renders for `params` (read from the query string)."""
    tz = user_tz(user)
    today = user_today(user)
    first_transaction = (
        usage.first_transaction_date(user)
        if needs_first_transaction(params.range, params.date_from)
        else None
    )
    first, last = resolve(params.range, today, params.date_from, params.date_to, first_transaction)
    auto = auto_group(first, last)
    group = params.group or auto
    refusal = ''
    explicit = params.group if params.group != auto else ''
    try:
        usage.check_period_count(first, last, group)
    except PeriodLimitError as exc:
        refusal = exc.messages[0]
        group = auto
        explicit = ''

    report = usage.build_report(
        user, first=first, last=last, group=group, apps=params.apps, lenient=True
    )
    shown = report.shown_apps
    group_label = dict(GROUPS)[group].lower()
    top_app = max(shown, key=lambda app: app.used, default=None)

    used_note = ''
    if top_app and top_app.used > 0:
        used_note = f'Most: {top_app.name} ({number(top_app.used)})'
    if report.refunded:
        used_note += (
            f'{" · " if used_note else ""}{number(report.refunded)} refunded, already subtracted'
        )

    effective = UsageParams(
        range=params.range,
        date_from=params.date_from,
        date_to=params.date_to,
        group=explicit,
        apps=tuple(app.label for app in shown) if len(shown) < len(report.apps) else (),
    )
    query = effective.to_query()
    page_url = reverse('billing:credits-history') + (f'?{query}' if query else '')
    chart = chart_context(report)
    first_text, last_text = format_date(first, 'full'), format_date(last, 'full')
    peak_text = number(report.peak)
    aria = (
        f'Credits used per {group_label} from {first_text} to {last_text}: '
        f'{number(report.used)} in total, at most {peak_text} in a {group_label}. '
        'The table below has every number.'
    )
    return {
        'report': report,
        'group': group,
        'group_label': group_label,
        'groups': [(key, label, key == group) for key, label in GROUPS],
        'ranges': [{'value': key, 'label': label} for key, label in RANGES],
        'range': params.range,
        'date_from': first.isoformat() if params.range == 'custom' else '',
        'date_to': last.isoformat() if params.range == 'custom' else '',
        'dates_disabled': params.range != 'custom',
        'refusal': refusal,
        'first_text': first_text,
        'last_text': last_text,
        'showing_some': len(shown) < len(report.apps),
        'shown_count': len(shown),
        'app_count': len(report.apps),
        'used_text': number(report.used),
        'used_note': used_note,
        'added': report.added,
        'added_text': f'+{number(report.added_total)}' if report.added_total else '0',
        'added_note': ' · '.join(f'{a.label} {number(a.credits)}' for a in report.added),
        'average_text': number(report.average),
        'periods_count': len(report.periods),
        'peak_text': peak_text,
        'chips': [{'app': app, 'used_text': number(app.used)} for app in report.apps],
        'chart': chart,
        'chart_aria': aria,
        'table': table_context(report),
        'empty': not report.apps,
        'empty_title': f'No credits used from {first_text} to {last_text}',
        'time_zone': tz.key,
        'page_url': page_url,
        'fragment_url': reverse('billing:credits-usage'),
        'form_action': reverse('billing:credits-history'),
    }


def limited_context(params: UsageParams, message: str) -> dict:
    """The Usage tab without a report, for a request over `BILLING_USAGE_USER`: the filters as the
    query string left them and `message` in `#usage-msg`. Runs no query."""
    custom = params.range == 'custom'
    return {
        'limited': message,
        'groups': [(key, label, key == params.group) for key, label in GROUPS],
        'ranges': [{'value': key, 'label': label} for key, label in RANGES],
        'range': params.range,
        'date_from': params.date_from.isoformat() if custom and params.date_from else '',
        'date_to': params.date_to.isoformat() if custom and params.date_to else '',
        'dates_disabled': not custom,
        'fragment_url': reverse('billing:credits-usage'),
        'form_action': reverse('billing:credits-history'),
    }


@login_required
@require_GET
def credits_usage_partial(request: AuthenticatedHttpRequest) -> HttpResponse:
    """`#usage-report`, re-fetched by the filters. `HX-Push-Url` is the page's address with the
    filters; opened directly it redirects to the page."""
    params = parse_params(request.GET)
    if not is_htmx(request):
        query = params.to_query()
        return redirect(reverse('billing:credits-history') + (f'?{query}' if query else ''))
    result = ratelimit.hit_user(request.user, 'BILLING_USAGE_USER')
    if not result.allowed:
        return ratelimit.web_response(request, result, retarget='#usage-msg')
    context = usage_context(request, request.user, params)
    response = render(request, 'billing/partials/usage_fragment.html', context)
    response['HX-Push-Url'] = context['page_url']
    return response


__all__ = ['credits_usage_partial', 'limited_context', 'usage_context']
