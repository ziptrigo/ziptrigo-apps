"""The Reports page's filters (issue #351), read from the query string.

One definition serves the page, its htmx partial and the three downloads, so "the current report"
means the same in all of them. Parsing is lenient: a value that doesn't validate (a hand-edited
URL) is dropped and the default applies, rather than an error page for a GET.

The defaults are **this year, processed receipts only, every vendor**. A form's unticked checkbox
posts nothing, so the form also posts a hidden `report_form` marker: with it, a missing checkbox
means "off"; without it (a link, a download URL) a missing one means the default. The canonical
`params` therefore write a flag only when it differs from the default (`processed=0`,
`vetted=1`), and `date_range=any` explicitly, since "all" is not the default here.
"""

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

from ..services.reports import DEFAULT_GROUP, GROUPS, ReportFilters

DEFAULT_DATE_RANGE = 'this_year'
FORM_MARKER = 'report_form'
_OFF = frozenset({'0', 'false', 'off', 'no', ''})


class ReportFilterForm(forms.Form):
    group = forms.ChoiceField(choices=[(g, g) for g in GROUPS], required=False)
    date_range = forms.ChoiceField(choices=DATE_RANGES, required=False)
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)


@dataclass(slots=True)
class ParsedReport:
    """The filters of a request, as `ReportFilters` for the service and as the canonical query
    `params` (defaults left out) for building links."""

    filters: ReportFilters
    date_range: str = DEFAULT_DATE_RANGE
    params: dict[str, str] = field(default_factory=dict)

    @property
    def group(self) -> str:
        return self.filters.group

    def query(self, **overrides: str | None) -> str:
        """The query string for these filters with `overrides` applied (`None` removes a key)."""
        merged: dict[str, str] = dict(self.params)
        for key, value in overrides.items():
            if value is None or value == '':
                merged.pop(key, None)
            else:
                merged[key] = value
        return urlencode(merged)

    def download_query(self) -> str:
        """The query of a download link: the group always spelled out."""
        return urlencode({'group': self.filters.group, **self.params})

    def dashboard_query(self, **extra: str) -> str:
        """The Receipts dashboard's query for the same receipt dates (plus `extra`, a vendor or
        category). The dashboard's own default is every date, so this year is spelled out."""
        params: dict[str, str] = {}
        if self.date_range == DATE_RANGE_CUSTOM:
            if self.filters.date_from:
                params['date_from'] = self.filters.date_from.isoformat()
            if self.filters.date_to:
                params['date_to'] = self.filters.date_to.isoformat()
        elif self.date_range != DATE_RANGE_ANY:
            params['date_range'] = self.date_range
        params.update(extra)
        return urlencode(params)


def _flag(data: Mapping[str, str], name: str, *, default: bool, from_form: bool) -> bool:
    if name in data:
        return str(data[name]).strip().lower() not in _OFF
    return False if from_form else default


def parse_report(request: HttpRequest, data: Mapping[str, str] | None = None) -> ParsedReport:
    """The report filters of `request.GET` (or of `data`)."""
    source = request.GET if data is None else data
    form = ReportFilterForm(source)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    cleaned = form.cleaned_data

    group = cleaned.get('group') or DEFAULT_GROUP
    date_range = cleaned.get('date_range') or ''
    date_from = cleaned.get('date_from')
    date_to = cleaned.get('date_to')
    if not date_range:
        # Dates without a range are a custom one; nothing at all is the default period.
        date_range = DATE_RANGE_CUSTOM if date_from or date_to else DEFAULT_DATE_RANGE
    if date_range == DATE_RANGE_ANY:
        date_from = date_to = None
    elif date_range != DATE_RANGE_CUSTOM:
        date_from, date_to = resolve_date_range(date_range, timezone.localdate())
    elif date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    from_form = FORM_MARKER in source
    processed = _flag(source, 'processed', default=True, from_form=from_form)
    vetted = _flag(source, 'vetted', default=False, from_form=from_form)

    params: dict[str, str] = {}
    if group != DEFAULT_GROUP:
        params['group'] = group
    if date_range == DATE_RANGE_CUSTOM:
        # The dates say "custom" on their own; a custom range with none is every date.
        if date_from:
            params['date_from'] = date_from.isoformat()
        if date_to:
            params['date_to'] = date_to.isoformat()
        if not date_from and not date_to:
            params['date_range'] = DATE_RANGE_CUSTOM
    elif date_range != DEFAULT_DATE_RANGE:
        params['date_range'] = date_range
    if not processed:
        params['processed'] = '0'
    if vetted:
        params['vetted'] = '1'

    filters = ReportFilters(
        group=group,
        date_from=date_from,
        date_to=date_to,
        processed_only=processed,
        vetted_only=vetted,
    )
    return ParsedReport(filters=filters, date_range=date_range, params=params)
