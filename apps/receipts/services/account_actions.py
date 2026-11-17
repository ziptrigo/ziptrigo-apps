"""Receipts' block of the account page's "App actions" (`apps.core.app_actions`).

The two rarely used downloads, Download files (the zip export) and Export CSV, live there rather
than on the dashboard. The block needs only the date options, so nothing is read from the
database and no Receipts account is created. It is shown, and its script loaded, for a user who
switched Receipts off too: the app's data is still theirs to download.

The date range may come preselected from the account page's query string, under a `receipts_`
prefix so another app's block can't collide with it: that is how the dialog, refused without
htmx, sends the user back with the range they chose (`views.exports.account_actions_url`).
"""

from django.http import HttpRequest

from apps.core.app_actions import AppActions

from ..forms.filters import DATE_RANGE_CUSTOM, DATE_RANGES, parse_filters

#: The query-string prefix of this block's fields on the account page.
QUERY_PREFIX = 'receipts_'

#: The fields the block has (the rest of the dashboard's filters are not offered here).
DATE_FIELDS = ('date_range', 'date_from', 'date_to')


def account_actions(request: HttpRequest) -> AppActions:
    data = {
        key: request.GET[f'{QUERY_PREFIX}{key}']
        for key in DATE_FIELDS
        if f'{QUERY_PREFIX}{key}' in request.GET
    }
    parsed = parse_filters(request, data)
    return AppActions(
        template='receipts/partials/account_actions.html',
        context={
            'date_ranges': DATE_RANGES,
            'date_range': parsed.date_range,
            'custom': parsed.date_range == DATE_RANGE_CUSTOM,
            'date_from': parsed.params.get('date_from', ''),
            'date_to': parsed.params.get('date_to', ''),
        },
        scripts=('core/js/fragment_modal.js',),
    )
