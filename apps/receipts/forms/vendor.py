"""The Vendors page's query string (tab and search) and the forms its modals post."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django import forms

from ..models import MAX_VENDOR_NAME_LENGTH

#: Vendors per page, like the dashboard's list.
VENDOR_PAGE_SIZE = 50

DEFAULT_VENDOR_TAB = 'all'

VENDOR_TABS = (
    ('all', 'All'),
    ('to_vet', 'To vet'),
    ('vetted', 'Vetted'),
)


class VendorFilterForm(forms.Form):
    status = forms.ChoiceField(choices=VENDOR_TABS, required=False)
    q = forms.CharField(required=False, max_length=200)


@dataclass(slots=True)
class ParsedVendorFilters:
    """The Vendors page's tab and search, plus the canonical query-string `params` (defaults left
    out) for building links, like `ParsedFilters` on the dashboard."""

    status: str = DEFAULT_VENDOR_TAB
    q: str = ''
    params: dict[str, str] = field(default_factory=dict)

    def query(self, **overrides: str | int | None) -> str:
        merged: dict[str, str | int] = dict(self.params)
        for key, value in overrides.items():
            if value is None or value == '':
                merged.pop(key, None)
            else:
                merged[key] = value
        return urlencode(merged)

    @property
    def is_filtered(self) -> bool:
        return bool(self.params)


def parse_vendor_filters(data: Mapping) -> ParsedVendorFilters:
    """Read `status` and `q` leniently from a query string (or a POST that carries them along): a
    value that doesn't validate is dropped and the default applies."""
    form = VendorFilterForm(data)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    status = form.cleaned_data.get('status') or DEFAULT_VENDOR_TAB
    q = (form.cleaned_data.get('q') or '').strip()
    params: dict[str, str] = {}
    if status != DEFAULT_VENDOR_TAB:
        params['status'] = status
    if q:
        params['q'] = q
    return ParsedVendorFilters(status=status, q=q, params=params)


class VendorNameForm(forms.Form):
    """The rename modal: the new name, and whether the vendor is vetted afterwards."""

    name = forms.CharField(
        label='Name',
        strip=False,
        max_length=MAX_VENDOR_NAME_LENGTH,
        error_messages={'required': 'Enter a vendor name.'},
    )
    vetted = forms.BooleanField(label='Vetted', required=False)
