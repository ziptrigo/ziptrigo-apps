"""The dashboard's filters, read from the query string.

One definition serves the dashboard page, its list partial and the CSV export, so "the current
filters" means the same thing in all three. Parsing is lenient: a value that doesn't validate (a
hand-edited URL) is dropped and the default applies, rather than showing an error page for a GET.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django import forms
from django.http import HttpRequest

from ..services.filters import DEFAULT_SORT, SORTS, UNCATEGORIZED, ReceiptFilters

#: Receipts per dashboard page, like the credits history.
PAGE_SIZE = 25

DEFAULT_TAB = 'all'

#: The status tabs, in order. `processing` covers everything the AI pipeline still owns.
TABS = (
    ('all', 'All'),
    ('needs_review', 'Needs review'),
    ('processing', 'Processing'),
    ('failed', 'Failed'),
    ('duplicates', 'Duplicates'),
)

_TAB_STATUSES = {
    'needs_review': 'needs_review',
    'processing': 'received,processing,waiting_for_credits',
    'failed': 'failed',
}


class ReceiptFilterForm(forms.Form):
    status = forms.ChoiceField(choices=TABS, required=False)
    q = forms.CharField(required=False, max_length=200)
    category = forms.CharField(required=False, max_length=20)
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
    sort = forms.ChoiceField(choices=[(s, s) for s in SORTS], required=False)

    def clean_category(self) -> str:
        value = self.cleaned_data['category'].strip()
        return value if value == UNCATEGORIZED or re.fullmatch(r'\d{1,10}', value) else ''


@dataclass(slots=True)
class ParsedFilters:
    """The filters of a request, as `ReceiptFilters` for the services and as a canonical set of
    query-string `params` (defaults left out) for building links."""

    tab: str = DEFAULT_TAB
    filters: ReceiptFilters = field(default_factory=ReceiptFilters)
    params: dict[str, str] = field(default_factory=dict)

    def query(self, **overrides: str | int | None) -> str:
        """The query string for these filters with `overrides` applied (`None` removes a key;
        a changed filter or tab should drop `page`, which is simply never in `params`)."""
        merged: dict[str, str | int] = dict(self.params)
        for key, value in overrides.items():
            if value is None or value == '':
                merged.pop(key, None)
            else:
                merged[key] = value
        return urlencode(merged)

    @property
    def is_filtered(self) -> bool:
        """Whether anything but the default view is selected (for the empty-state wording)."""
        return any(
            key in self.params for key in ('status', 'q', 'category', 'date_from', 'date_to')
        )


def parse_filters(request: HttpRequest) -> ParsedFilters:
    form = ReceiptFilterForm(request.GET)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    data = form.cleaned_data

    tab = data.get('status') or DEFAULT_TAB
    sort = data.get('sort') or DEFAULT_SORT
    q = (data.get('q') or '').strip()
    category = data.get('category') or ''
    date_from = data.get('date_from')
    date_to = data.get('date_to')

    params: dict[str, str] = {}
    if tab != DEFAULT_TAB:
        params['status'] = tab
    if q:
        params['q'] = q
    if category:
        params['category'] = category
    if date_from:
        params['date_from'] = date_from.isoformat()
    if date_to:
        params['date_to'] = date_to.isoformat()
    if sort != DEFAULT_SORT:
        params['sort'] = sort

    filters = ReceiptFilters(
        status=_TAB_STATUSES.get(tab),
        category=category or None,
        q=q,
        date_from=date_from,
        date_to=date_to,
        duplicates=tab == 'duplicates',
        sort=sort,
    )
    return ParsedFilters(tab=tab, filters=filters, params=params)
