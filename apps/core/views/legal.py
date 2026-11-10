"""The Privacy Policy and Terms of Use pages (issue #96): public, no login, stable URLs
(`/privacy/`, `/terms/`) that Google's brand verification and the sign-up checkbox link to."""

from functools import cache

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from .. import legal
from ..datetime_format import PUBLIC, format_date
from ..markdown import render as render_markdown


@cache
def _document(name: str) -> SafeString:
    """One document's HTML: its Markdown template filled in with `legal.context()` and rendered
    through the site's sanitising Markdown renderer. The text only changes with a deploy, so it is
    rendered once per process."""
    values = legal.context()
    # Always the public wording (`Nov 9, 2026`): the page is the same for every visitor.
    values['effective_date'] = format_date(legal.EFFECTIVE_DATE, 'full', PUBLIC)
    return render_markdown(render_to_string(f'core/legal/{name}.md', values))


def _page(request: HttpRequest, name: str, title: str) -> HttpResponse:
    return render(request, 'core/legal.html', {'legal_title': title, 'legal_body': _document(name)})


@require_safe
def privacy_page(request: HttpRequest) -> HttpResponse:
    return _page(request, 'privacy', 'Privacy Policy')


@require_safe
def terms_page(request: HttpRequest) -> HttpResponse:
    return _page(request, 'terms', 'Terms of Use')
