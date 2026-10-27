"""The dashboard's filters, read from the query string.

One definition serves the dashboard page and its list partial. Parsing is lenient: a value that
doesn't validate (a hand-edited URL) is dropped and the default applies, rather than an error page
for a GET.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django import forms
from django.http import HttpRequest

#: Links per dashboard page.
PAGE_SIZE = 25

DEFAULT_SORT = '-created_at'
SORTS = ('-created_at', 'created_at', '-click_count', 'click_count')

#: The status tabs, in order. They match `display_state` (an expired or click-capped link is
#: still `active` in the database, so these are not plain status filters; see `views.dashboard`).
STATUS_TABS = (
    ('all', 'All'),
    ('active', 'Active'),
    ('disabled', 'Disabled'),
    ('expired', 'Expired'),
    ('capped', 'Click limit reached'),
    ('taken_down', 'Taken down'),
)
DEFAULT_TAB = 'all'


class LinkFilterForm(forms.Form):
    status = forms.ChoiceField(choices=STATUS_TABS, required=False)
    q = forms.CharField(required=False, max_length=200)
    tag = forms.CharField(required=False, max_length=64)
    sort = forms.ChoiceField(choices=[(s, s) for s in SORTS], required=False)


@dataclass(slots=True)
class ParsedFilters:
    """The filters of a request: the tab, search text, tag and sort, and a canonical set of
    query-string `params` (defaults left out) for building links."""

    tab: str = DEFAULT_TAB
    q: str = ''
    tag: str = ''
    sort: str = DEFAULT_SORT
    params: dict[str, str] = field(default_factory=dict)

    def query(self, **overrides: str | int | None) -> str:
        """The query string for these filters with `overrides` applied (`None` removes a key; a
        changed filter should drop `page`, which is simply never in `params`)."""
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
        return any(key in self.params for key in ('status', 'q', 'tag'))


def parse_filters(request: HttpRequest) -> ParsedFilters:
    form = LinkFilterForm(request.GET)
    form.is_valid()  # leniently: `cleaned_data` keeps the valid fields
    data = form.cleaned_data
    tab = data.get('status') or DEFAULT_TAB
    q = (data.get('q') or '').strip()
    tag = (data.get('tag') or '').strip().lower()
    sort = data.get('sort') or DEFAULT_SORT

    params: dict[str, str] = {}
    if tab != DEFAULT_TAB:
        params['status'] = tab
    if q:
        params['q'] = q
    if tag:
        params['tag'] = tag
    if sort != DEFAULT_SORT:
        params['sort'] = sort
    return ParsedFilters(tab=tab, q=q, tag=tag, sort=sort, params=params)
