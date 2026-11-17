"""The "Download files" dialog and the quick XLSX (issue #326): the "Task created" range read from
the request, and the cost the user confirmed.

Parsing is lenient, like Receipts' dashboard filters: a value that doesn't validate (a hand-edited
URL) is dropped and the default (any date) applies, rather than an error page for a GET.
"""

from collections.abc import Mapping

from django import forms

from apps.core.date_ranges import DATE_RANGE_ANY, DATE_RANGE_CUSTOM, DATE_RANGES

from ..services.export_range import ExportRange


class ExportRangeForm(forms.Form):
    date_range = forms.ChoiceField(choices=DATE_RANGES, required=False)
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)


def parse_range(data: Mapping[str, str]) -> ExportRange:
    """The "Task created" range of a query string or a POST (`date_range`, `date_from`,
    `date_to`). No `date_range` with dates is read as Custom; "any" and "custom" without dates are
    every task."""
    form = ExportRangeForm(data)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    cleaned = form.cleaned_data
    date_range = cleaned.get('date_range') or ''
    date_from, date_to = cleaned.get('date_from'), cleaned.get('date_to')
    if not date_range:
        date_range = DATE_RANGE_CUSTOM if date_from or date_to else DATE_RANGE_ANY
    if date_range != DATE_RANGE_CUSTOM:
        date_from = date_to = None
    return ExportRange(date_range=date_range, date_from=date_from, date_to=date_to)


class ExportForm(forms.Form):
    #: The total the dialog showed. Required for the web (credits are never spent unseen): a
    #: post without it is refused and the dialog shows the cost again.
    confirm_cost = forms.IntegerField(
        min_value=0,
        required=False,
        error_messages={'invalid': 'Confirm the cost.', 'min_value': 'Confirm the cost.'},
    )
