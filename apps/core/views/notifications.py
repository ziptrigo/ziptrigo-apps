"""The notification bell, its dropdown and the notifications page (issue #169).

Everything goes through `apps.core.services.notifications`, which scopes every query to the
requesting user: a notification that isn't theirs is a 404, never a 403. Mutating actions are
POSTs (CSRF-protected, htmx with a plain-redirect fallback); the two read-only fragments that
htmx fetches in the background (the bell's count, the dropdown) answer a signed-out session with an
empty 204 instead of a redirect, so the login page is never swapped into the header.

An action answers with the list it came from, chosen by the `scope` it posted: `dropdown` hands
back the dropdown's content, anything else the page's list (at the posted `page`). Either carries
the bell's badge out of band, so the count follows every change.
"""

from collections.abc import Callable
from functools import wraps
from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from ..htmx import is_htmx
from ..http import AuthenticatedHttpRequest
from ..models import Notification
from ..services import notifications
from ..services.notifications import is_safe_url

PAGE_SIZE = 20


def _background_login_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """`login_required`, except that an htmx request without a session gets an empty 204 (htmx
    swaps nothing on 204): these are fetched by timers and clicks in the background, and following
    the redirect would put the login page inside the header."""
    protected = login_required(view)

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = getattr(request, 'user', None)
        if is_htmx(request) and not (user and user.is_authenticated):
            return HttpResponse(status=204)
        return protected(request, *args, **kwargs)

    return wrapper


def _page_url(number: str | None = None) -> str:
    url = reverse('core:notifications')
    return f'{url}?page={number}' if number and number.isdigit() else url


def _dropdown_response(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The dropdown's content. Rendering it is what marks its notifications read: the ones listed
    that were new are passed to the template as `new_ids` (so they still look new for as long as
    the dropdown stays open) and are read by the time the response goes out."""
    user = request.user
    items = notifications.dropdown_notifications(user)
    new_ids = {n.pk for n in items if n.read_at is None}
    notifications.mark_read(user, new_ids)
    context = {
        'notifications': items,
        'new_ids': new_ids,
        'unread_count': notifications.unread_count(user),
    }
    return render(request, 'core/partials/notification_dropdown.html', context)


def _list_context(request: AuthenticatedHttpRequest, number: str | None) -> dict[str, Any]:
    user = request.user
    paginator = Paginator(notifications.page_notifications(user), PAGE_SIZE)
    page_obj = paginator.get_page(number)
    return {
        'page_obj': page_obj,
        'new_ids': {n.pk for n in page_obj if n.read_at is None},
        'unread_count': notifications.unread_count(user),
        'oob_badge': True,
    }


def _list_response(request: AuthenticatedHttpRequest, number: str | None) -> HttpResponse:
    return render(request, 'core/partials/notification_list.html', _list_context(request, number))


def _after_action(request: AuthenticatedHttpRequest) -> HttpResponse:
    """What a POST answers: the list it came from (htmx), or back to the page (no htmx)."""
    if not is_htmx(request):
        return redirect(_page_url(request.POST.get('page')))
    if request.POST.get('scope') == 'dropdown':
        return _dropdown_response(request)
    return _list_response(request, request.POST.get('page'))


@login_required
@require_GET
def notifications_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Every notification of the user that wasn't dismissed, newest first, paginated."""
    context = _list_context(request, request.GET.get('page'))
    context['oob_badge'] = False
    return render(request, 'core/notifications.html', context)


@login_required
@require_GET
def notification_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The page's list alone, for htmx pagination. Opened directly it goes to the full page."""
    if not is_htmx(request):
        return redirect(_page_url(request.GET.get('page')))
    return _list_response(request, request.GET.get('page'))


@_background_login_required
@require_GET
@never_cache
def notification_bell(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The bell's badge, polled by htmx: one indexed `COUNT`."""
    count = notifications.unread_count(request.user)
    return render(request, 'core/partials/notification_badge.html', {'count': count})


@_background_login_required
@require_POST
def notification_dropdown(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Open the dropdown: its content, and what it lists is marked read. A POST because it writes."""
    return _dropdown_response(request)


@login_required
@require_POST
def notification_read(request: AuthenticatedHttpRequest, notification_id: UUID) -> HttpResponse:
    try:
        notification = notifications.get_notification(request.user, notification_id)
    except Notification.DoesNotExist:
        raise Http404 from None
    notifications.mark_read(request.user, [notification.pk])
    return _after_action(request)


@login_required
@require_POST
def notification_read_all(request: AuthenticatedHttpRequest) -> HttpResponse:
    notifications.mark_all_read(request.user)
    return _after_action(request)


@login_required
@require_POST
def notification_dismiss(request: AuthenticatedHttpRequest, notification_id: UUID) -> HttpResponse:
    try:
        notifications.dismiss(request.user, notification_id)
    except Notification.DoesNotExist:
        raise Http404 from None
    return _after_action(request)


@login_required
@require_GET
def notification_open(request: AuthenticatedHttpRequest, notification_id: UUID) -> HttpResponse:
    """Follow a notification: mark it read and opened, then redirect to its `url`. A plain GET
    link, so middle-click works. The stored URL is checked again here: whatever put it in the row,
    a value that isn't a same-site path sends the user to the notifications page instead."""
    try:
        notification = notifications.mark_opened(request.user, notification_id)
    except Notification.DoesNotExist:
        raise Http404 from None
    if is_safe_url(notification.url):
        return redirect(notification.url)
    return redirect('core:notifications')
