"""Redirects to one of our own pages carrying part of the request's query string."""

from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme


def redirect_to_page(path: str, query: str = '') -> HttpResponseRedirect:
    """Redirect to `path` (a site-relative page URL from `reverse`) with `query` (without the `?`)
    appended.

    The query is rebuilt by the caller from validated values, but since it comes from the request
    the target is checked to be a same-site path before it is used: anything else (a `//host`
    shape, a scheme, a backslash) goes to the home page instead, so this can never become an open
    redirect.
    """
    target = f'{path}?{query}' if query else path
    if not url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        target = '/'
    return HttpResponseRedirect(target)
