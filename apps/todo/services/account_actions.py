"""To-do's block of the account page's "App actions" (`apps.core.app_actions`, issue #326).

The two rarely used downloads, Download files (the zip export of the files attached to tasks and
comments) and Export XLSX (the task list as a spreadsheet), live there rather than in the To-do
pages. The block needs only the date options, so nothing is read from the database and no To-do
account is created. It is shown, and its script loaded, for a user who switched To-do off too: the
app's data is still theirs to download.

The date range may come preselected from the account page's query string, under a `todo_` prefix
so another app's block (Receipts' `receipts_`) can't collide with it: that is how the dialog,
refused without htmx, sends the user back with the range they chose
(`views.exports.account_actions_url`).
"""

from django.http import HttpRequest

from apps.core.app_actions import AppActions
from apps.core.date_ranges import DATE_RANGE_CUSTOM, DATE_RANGES

from ..forms.export import parse_range

#: The query-string prefix of this block's fields on the account page.
QUERY_PREFIX = 'todo_'

#: The fields the block has.
DATE_FIELDS = ('date_range', 'date_from', 'date_to')


def account_actions(request: HttpRequest) -> AppActions:
    data = {
        key: request.GET[f'{QUERY_PREFIX}{key}']
        for key in DATE_FIELDS
        if f'{QUERY_PREFIX}{key}' in request.GET
    }
    chosen = parse_range(data)
    return AppActions(
        template='todo/partials/account_actions.html',
        context={
            'date_ranges': DATE_RANGES,
            'date_range': chosen.date_range,
            'custom': chosen.date_range == DATE_RANGE_CUSTOM,
            'date_from': chosen.params.get('date_from', ''),
            'date_to': chosen.params.get('date_to', ''),
        },
        scripts=('core/js/fragment_modal.js',),
    )
