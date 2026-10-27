"""What the Short URLs views share: the page renderer, the ownership lookup, the 422 message
responses and the safe "go back" redirect.

Ownership: a link that is not the requester's (or that they deleted) is a 404, never a 403, so an
id can't be probed.
"""

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.http import AuthenticatedHttpRequest

from ..models import LinkStatus, ShortLink

FIELD_LABELS = {
    'destination': 'Destination',
    'alias': 'Alias',
    'title': 'Title',
    'tags': 'Tags',
    'expires_at': 'Expires',
    'max_clicks': 'Click limit',
    'password': 'Password',
}


def owned_link(request: AuthenticatedHttpRequest, link_id: object) -> ShortLink:
    """The requester's link, or a 404 (a deleted link is gone for its owner)."""
    queryset = ShortLink.objects.select_related('owner').exclude(status=LinkStatus.DELETED)
    return get_object_or_404(queryset, pk=link_id, owner=request.user)


def render_page(
    request: HttpRequest, template: str, context: dict | None = None, status: int = 200
) -> HttpResponse:
    return render(request, template, context or {}, status=status)


def message_response(
    request: HttpRequest,
    messages: list[str] | str,
    *,
    retarget: str,
    status: int = 422,
    top_up: bool = False,
) -> HttpResponse:
    """A 422 whose body is the message slot's content (one line per message), sent to `retarget`
    (a selector) whatever the request's own target was. `top_up` adds a link to the credits page."""
    if isinstance(messages, str):
        messages = [messages]
    response = render(
        request,
        'short_url/partials/form_errors.html',
        {'messages_list': messages, 'top_up': top_up},
        status=status,
    )
    response['HX-Retarget'] = retarget
    response['HX-Reswap'] = 'innerHTML'
    return response


def form_error_lines(form) -> list[str]:
    """`Label: message` per error, non-field errors first (the design system's error format)."""
    lines = [str(error) for error in form.non_field_errors()]
    for name, errors in form.errors.items():
        if name == '__all__':
            continue
        label = form.fields[name].label or name.replace('_', ' ').capitalize()
        lines += [f'{label}: {error}' for error in errors]
    return lines


def validation_lines(exc: ValidationError) -> list[str]:
    """The lines to show for a service's `ValidationError`: `Label: message` for a field's."""
    if hasattr(exc, 'error_dict'):
        return [
            f'{FIELD_LABELS.get(field, field.replace("_", " ").capitalize())}: {message}'
            for field, messages in exc.message_dict.items()
            for message in messages
        ]
    return list(exc.messages)


def credits_url() -> str:
    """Where "Top up" goes: the credits page (there is no purchase page yet)."""
    return reverse('billing:credits-history')


def parse_cost(value: str | None) -> int | None:
    """The `confirm_cost` a form or modal posted, or None when it is missing or not a number."""
    try:
        return int(value) if value not in (None, '') else None
    except ValueError:
        return None


def redirect_back(request: HttpRequest, default: str, *, not_to: str = '') -> HttpResponse:
    """Redirect to the posted (or, for the confirmation modal, queried) `next` if it is on this site (and doesn't contain `not_to`, a page
    that no longer exists), else to `default`."""
    target = request.POST.get('next') or request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) or (not_to and not_to in target):
        target = default
    return redirect(target)
