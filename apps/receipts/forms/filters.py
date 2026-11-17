"""The dashboard's filters, read from the query string.

One definition serves the dashboard page, its list partial and the CSV export, so "the current
filters" means the same thing in all three. Parsing is lenient: a value that doesn't validate (a
hand-edited URL) is dropped and the default applies, rather than showing an error page for a GET.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django import forms
from django.http import HttpRequest
from django.utils import timezone

from apps.core.date_ranges import (
    DATE_RANGE_ANY,
    DATE_RANGE_CUSTOM,
    DATE_RANGES,
    resolve_date_range,
)

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
    vendor = forms.CharField(required=False, max_length=20)
    date_range = forms.ChoiceField(choices=DATE_RANGES, required=False)
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
    sort = forms.ChoiceField(choices=[(s, s) for s in SORTS], required=False)

    def clean_category(self) -> str:
        value = self.cleaned_data['category'].strip()
        return value if value == UNCATEGORIZED or re.fullmatch(r'\d{1,10}', value) else ''

    def clean_vendor(self) -> str:
        value = self.cleaned_data['vendor'].strip()
        return value if re.fullmatch(r'\d{1,10}', value) else ''


@dataclass(slots=True)
class ParsedFilters:
    """The filters of a request, as `ReceiptFilters` for the services and as a canonical set of
    query-string `params` (defaults left out) for building links."""

    tab: str = DEFAULT_TAB
    date_range: str = DATE_RANGE_ANY
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
            key in self.params
            for key in ('status', 'q', 'category', 'vendor', 'date_range', 'date_from', 'date_to')
        )


def parse_filters(request: HttpRequest, data: Mapping[str, str] | None = None) -> ParsedFilters:
    """The filters of `request.GET`, or of `data` (a POST's `request.POST` that carries them as
    hidden fields, like the "Download files" dialog's)."""
    form = ReceiptFilterForm(request.GET if data is None else data)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    data = form.cleaned_data

    tab = data.get('status') or DEFAULT_TAB
    sort = data.get('sort') or DEFAULT_SORT
    q = (data.get('q') or '').strip()
    category = data.get('category') or ''
    vendor = str(int(data['vendor'])) if data.get('vendor') else ''
    date_range = data.get('date_range') or ''
    date_from = data.get('date_from')
    date_to = data.get('date_to')
    if not date_range:
        # Dates without a range: a link from before the dropdown existed.
        date_range = DATE_RANGE_CUSTOM if date_from or date_to else DATE_RANGE_ANY
    if date_range not in (DATE_RANGE_ANY, DATE_RANGE_CUSTOM):
        date_from, date_to = resolve_date_range(date_range, timezone.localdate())
    elif date_range == DATE_RANGE_ANY:
        date_from = date_to = None

    params: dict[str, str] = {}
    if tab != DEFAULT_TAB:
        params['status'] = tab
    if q:
        params['q'] = q
    if category:
        params['category'] = category
    if vendor:
        params['vendor'] = vendor
    if date_range == DATE_RANGE_CUSTOM:
        # Just the dates: they say "custom" on their own, and a custom range without any is
        # simply every date.
        if date_from:
            params['date_from'] = date_from.isoformat()
        if date_to:
            params['date_to'] = date_to.isoformat()
    elif date_range != DATE_RANGE_ANY:
        params['date_range'] = date_range
    if sort != DEFAULT_SORT:
        params['sort'] = sort

    filters = ReceiptFilters(
        status=_TAB_STATUSES.get(tab),
        category=category or None,
        vendor=int(vendor) if vendor else None,
        q=q,
        date_from=date_from,
        date_to=date_to,
        duplicates=tab == 'duplicates',
        sort=sort,
    )
    return ParsedFilters(tab=tab, date_range=date_range, filters=filters, params=params)
