"""Where the Credits page lives, in one place (issue #347).

`/billing/credits/` has two tabs, Usage and History. The promo code form is on History, so every
"Top up" link, email and notification across the site opens that tab: build the address here (or
with `{% credits_url 'history' %}` in a template), never with a bare `reverse('billing:credits-history')`.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse

USAGE = 'usage'
HISTORY = 'history'
TABS = (USAGE, HISTORY)
DEFAULT_TAB = USAGE


def credits_url(tab: str = HISTORY, *, absolute: bool = False, **query: str | int) -> str:
    """The address of the Credits page's `tab` ("usage" or "history"), site-relative or, with
    `absolute`, on `BASE_URL` (for an email). `query` adds parameters (`page=2`)."""
    if tab not in TABS:
        raise ValueError(f'Unknown credits tab: {tab}')
    params: dict[str, str | int] = {}
    if tab != DEFAULT_TAB:
        params['tab'] = tab
    params.update(query)
    path = reverse('billing:credits-history')
    url = f'{path}?{urlencode(params)}' if params else path
    return f'{settings.BASE_URL.rstrip("/")}{url}' if absolute else url
