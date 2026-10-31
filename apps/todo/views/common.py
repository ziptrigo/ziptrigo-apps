"""What the To-do views share: the page renderer (account, sidebar, the free-tasks line),
ownership lookups, rate limits, and the 422 message responses.

Ownership: a task that is not the requester's is a 404, never a 403, so a number can't be probed.
A trashed task is a 404 too, except where a view says it accepts one (`trashed=True`/`None`).
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.field_errors import ErrorLines, form_lines
from apps.core.field_errors import validation_lines as core_validation_lines
from apps.core.htmx import is_htmx

from ..models import Task, TodoSettings
from ..services import get_account, month_usage, sidebar_filters, today_set

FIELD_LABELS = {
    'title': 'Title',
    'description': 'Description',
    'priority': 'Priority',
    'due_date': 'Date',
    'due_time': 'Time',
    'labels': 'Labels',
    'rrule': 'Repeat',
    'recurrence_mode': 'Repeat mode',
    'parent': 'Parent',
    'name': 'Name',
    'color': 'Colour',
    'date': 'Date',
    'preset': 'When',
    'index': 'Item',
    'body': 'Comment',
    'default_due_time': 'Default due time',
    'minutes_before': 'Reminder',
    'channel': 'Channel',
    'params': 'Filter',
}


def nav_items() -> list[tuple[str, str, str, str]]:
    """The sidebar: `(key, label, icon, url)`."""
    entries = [
        ('today', 'Today', 'fas fa-calendar-day', 'todo:today'),
        ('upcoming', 'Upcoming', 'fas fa-calendar-days', 'todo:upcoming'),
        ('all', 'All', 'fas fa-list', 'todo:all'),
        ('completed', 'Completed', 'fas fa-circle-check', 'todo:completed'),
        ('labels', 'Labels', 'fas fa-tag', 'todo:labels'),
        ('trash', 'Trash', 'fas fa-trash', 'todo:trash'),
        ('settings', 'Settings', 'fas fa-gear', 'todo:settings'),
    ]
    return [(key, label, icon, reverse(url)) for key, label, icon, url in entries]


def free_line(account, config: TodoSettings) -> tuple[int, int]:
    """`(used, free)` for "N of F free tasks used this month": the free ones used, so never more
    than F (`services.month_usage` counts every task created, paid ones too)."""
    used, free = month_usage(account, config)
    return min(used, free), free


def render_page(
    request: AuthenticatedHttpRequest,
    template: str,
    context: dict | None = None,
    *,
    nav: str,
    status: int = 200,
) -> HttpResponse:
    """Render a full To-do page. Makes sure the user's To-do account exists (created on first
    visit) and adds what the layout shows: the account, the "N of F free tasks used" line, the
    count beside Today, the saved filters and the highlighted sidebar entry (`nav`). The saved
    filters' counts are not here: the sidebar asks for them after the page loads."""
    account = get_account(request.user)
    config = TodoSettings.load()
    used, free = free_line(account, config)
    saved_filters, more_filters = sidebar_filters(request.user)
    page_context = {
        'account': account,
        'todo_blocked': account.is_blocked,
        'todo_frozen': account.is_frozen,
        'free_used': used,
        'free_total': free,
        'task_price': config.task_price,
        'nav': nav,
        'nav_items': nav_items(),
        'today_count': today_set(request.user).count(),
        'sidebar_filters': saved_filters,
        'sidebar_more': more_filters,
        'show_sidebar_search': True,
    }
    page_context.update(context or {})
    return render(request, template, page_context, status=status)


def owned_task(
    request: AuthenticatedHttpRequest, number: int, *, trashed: bool | None = False
) -> Task:
    """The requester's task numbered `number`, or a 404. `trashed=False` (the default) also
    refuses a task in the Trash, `True` accepts only one that is, `None` accepts either."""
    queryset = Task.objects.select_related('user', 'parent')
    task = get_object_or_404(queryset, user=request.user, number=number)
    if trashed is not None and task.is_trashed != trashed:
        raise Http404('Task not found')
    return task


def credits_url() -> str:
    """Where "Top up" goes: the credits page (there is no purchase page yet)."""
    return reverse('billing:credits-history')


def validation_lines(exc: ValidationError) -> ErrorLines:
    """`Label: message` per error of a service `ValidationError`, non-field errors plain."""
    return core_validation_lines(exc, FIELD_LABELS)


def form_error_lines(form) -> ErrorLines:
    """`Label: message` per error, non-field errors first (the design system's error format).
    The lines also carry each field's messages, which the slot's partial hands to the page."""
    return form_lines(form)


def message_response(
    request: HttpRequest,
    messages_list: list[str] | str,
    *,
    retarget: str = '#todo-msg',
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
        'todo/partials/messages.html',
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
    retarget: str = '#todo-msg',
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


def redirect_back(request: HttpRequest, fallback: str = '') -> HttpResponse:
    """Redirect to `next` (a POSTed same-site path), else the referring page, else `fallback`
    (default: Today) -- for the actions used without htmx."""
    for candidate in (request.POST.get('next', ''), request.headers.get('Referer', '')):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(candidate)
    return redirect(fallback or reverse('todo:today'))


def limited(request: AuthenticatedHttpRequest, rule: str, *, retarget: str = '#todo-msg'):
    """The 429 response when the user is over the `rule` rate limit, else None. Charges one hit."""
    result = ratelimit.hit_user(request.user, rule)
    if result.allowed:
        return None
    return ratelimit.web_response(request, result, retarget=retarget)
