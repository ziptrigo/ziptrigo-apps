"""Receipts' block of the account page's "App actions" (`apps.core.app_actions`).

The two rarely used downloads, Download files (the zip export) and Export CSV, live there rather
than on the dashboard. The block only needs the date options, so nothing is read from the
database and no Receipts account is created.
"""

from apps.accounts.models import User
from apps.core.app_actions import AppActions

from ..forms.filters import DATE_RANGE_ANY, DATE_RANGES


def account_actions(user: User) -> AppActions:
    return AppActions(
        template='receipts/partials/account_actions.html',
        context={'date_ranges': DATE_RANGES, 'date_range': DATE_RANGE_ANY},
    )
