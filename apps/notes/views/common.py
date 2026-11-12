"""What the Notes views share: the page renderer (account, sidebar), ownership lookups, rate
limits and the 422 message responses.

Ownership: a note that is not the requester's is a 404, never a 403, so an id can't be probed.
"""

from collections.abc import Mapping
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.field_errors import ErrorLines, form_lines
from apps.core.field_errors import validation_lines as core_validation_lines
from apps.core.htmx import is_htmx

from ..models import Note
from ..services import NoteConflictError, get_account

FIELD_LABELS = {
    'title': 'Title',
    'content': 'Content',
    'tags': 'Tags',
    'name': 'Name',
    'color': 'Colour',
    'description': 'Description',
    'index': 'Item',
    'base_version': 'Version',
    'confirm_cost': 'Cost',
    'mode': 'Mode',
    'label': 'Label',
    'expires_on': 'Expires on',
    'reason': 'Reason',
    'reporter_email': 'Your email',
}


def nav_items() -> list[tuple[str, str, str, str]]:
    """The sidebar: `(key, label, icon, url)`."""
    entries = [
        ('notes', 'Notes', 'fas fa-note-sticky', 'notes:list'),
        ('archived', 'Archived', 'fas fa-box-archive', 'notes:archived'),
        ('trash', 'Trash', 'fas fa-trash', 'notes:trash'),
        ('tags', 'Tags', 'fas fa-tag', 'notes:tags'),
    ]
    return [(key, label, icon, reverse(url)) for key, label, icon, url in entries]


def render_page(
    request: AuthenticatedHttpRequest,
    template: str,
    context: dict | None = None,
    *,
    nav: str,
    status: int = 200,
) -> HttpResponse:
    """Render a full Notes page. Makes sure the user's Notes account exists (created on first
    visit) and adds what the layout shows: the sidebar, with `nav` highlighted."""
    page_context = {
        'account': get_account(request.user),
        'nav': nav,
        'nav_items': nav_items(),
    }
    page_context.update(context or {})
    return render(request, template, page_context, status=status)


def owned_note(
    request: AuthenticatedHttpRequest, note_id: UUID, *, trashed: bool | None = None
) -> Note:
    """The requester's note `note_id`, or a 404. `trashed=False` also refuses a note in the Trash,
    `True` accepts only one that is, `None` (the default) accepts either."""
    note = get_object_or_404(Note.objects.select_related('user'), user=request.user, pk=note_id)
    if trashed is not None and note.is_trashed != trashed:
        raise Http404('Note not found')
    return note


def credits_url() -> str:
    """Where "Top up" goes: the credits page (there is no purchase page yet)."""
    return reverse('billing:credits-history')


def credits_text(count: int) -> str:
    """`1 credit`, `3 credits`: an amount for a flash message."""
    return f'{count} credit{"" if count == 1 else "s"}'


def out_of_credits_message(text: str) -> SafeString:
    """A flash message that says `text` and links to the credits page. Safe HTML: `text` is
    escaped, the link is ours."""
    return format_html(
        '{} <a href="{}" class="underline">Top up your credits</a>.', text, credits_url()
    )


def parse_cost(value: str | None) -> int | None:
    """The `confirm_cost` a form or modal posted, or None when it is missing or not a number."""
    try:
        return int(value) if value not in (None, '') else None
    except ValueError:
        return None


def validation_lines(exc: ValidationError, names: Mapping[str, str] | None = None) -> ErrorLines:
    """`Label: message` per error of a service `ValidationError`, non-field errors plain and first.
    The field names are the inputs' names too (`FIELD_LABELS`)."""
    return core_validation_lines(exc, FIELD_LABELS, names)


def form_error_lines(form) -> ErrorLines:
    """`Label: message` per error of a form, non-field errors first."""
    return form_lines(form)


def message_response(
    request: HttpRequest,
    messages_list: list[str] | str,
    *,
    retarget: str = '#notes-msg',
    status: int = 422,
    top_up: bool = False,
) -> HttpResponse:
    """A 422 whose body is the message slot's content (one line per message), sent to `retarget`
    (a selector) whatever the request's own target was. `top_up` adds a link to the credits
    page."""
    if isinstance(messages_list, str):
        messages_list = [messages_list]
    response = render(
        request,
        'notes/partials/messages.html',
        {'messages_list': messages_list, 'top_up': top_up},
        status=status,
    )
    response['HX-Retarget'] = retarget
    response['HX-Reswap'] = 'innerHTML'
    return response


def refuse(
    request: AuthenticatedHttpRequest,
    lines: list[str] | str,
    *,
    retarget: str = '#notes-msg',
    top_up: bool = False,
    fallback: str = '',
) -> HttpResponse:
    """A refused action: the 422 message for htmx, a flash message and a redirect otherwise
    (back to `fallback`, else where the request came from)."""
    if is_htmx(request):
        return message_response(request, lines, retarget=retarget, top_up=top_up)
    for line in [lines] if isinstance(lines, str) else lines:
        messages.error(request, line)
    return redirect_back(request, fallback)


def redirect_back(request: HttpRequest, fallback: str = '', *, not_to: str = '') -> HttpResponse:
    """Redirect to `next` (a POSTed or queried same-site path), else the referring page, else
    `fallback` (default: the list). `not_to` is a page that no longer exists: a candidate that
    contains it is skipped."""
    for candidate in (
        request.POST.get('next', ''),
        request.GET.get('next', ''),
        request.headers.get('Referer', ''),
    ):
        if (
            candidate
            and (not not_to or not_to not in candidate)
            and url_has_allowed_host_and_scheme(
                candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            )
        ):
            return redirect(candidate)
    return redirect(fallback or reverse('notes:list'))


def limited(request: AuthenticatedHttpRequest, rule: str, *, retarget: str = '#notes-msg'):
    """The 429 response when the user is over the `rule` rate limit, else None. Charges one hit."""
    result = ratelimit.hit_user(request.user, rule)
    if result.allowed:
        return None
    return ratelimit.web_response(request, result, retarget=retarget)


def conflict_panel(
    exc: NoteConflictError, values: dict, *, compare_title: bool = True
) -> tuple[dict, dict]:
    """What the editor's conflict panel shows for a save that lost a race: `(conflict, values)`,
    the panel's data (the version, title and content stored now, whether the title differs and the
    message) and the posted `values` with `base_version` moved to the current version, so
    "Save mine anyway" is a plain re-post. `compare_title` is false for an edit through a share link,
    where the title can't be changed."""
    conflict = {
        'version': exc.current_version,
        'title': exc.current_title,
        'content': exc.current_content,
        'title_differs': compare_title
        and exc.current_title != str(values.get('title', '')).strip(),
        'message': NoteConflictError.MESSAGE,
    }
    return conflict, {**values, 'base_version': exc.current_version}
