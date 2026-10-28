"""`/todo/settings/`: the default due time, the free-tasks line and the price.

Later sub-issues add their own sections (reminders, the email address, ...).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import SettingsForm
from ..models import TodoSettings
from ..services import get_account, update_default_due_time
from .common import form_error_lines, free_line, limited, refuse, render_page

SLOT = '#settings-msg'


def _form_context(request: AuthenticatedHttpRequest, *, saved: bool = False) -> dict:
    account = get_account(request.user)
    config = TodoSettings.load()
    used, free = free_line(account, config)
    return {
        'account': account,
        'free_used': used,
        'free_total': free,
        'task_price': config.task_price,
        'saved': saved,
    }


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def settings_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page. POST: save the default due time."""
    if request.method == 'POST':
        return _save(request)
    return render_page(request, 'todo/settings.html', _form_context(request), nav='settings')


def _save(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = SettingsForm(request.POST)
    fallback = reverse('todo:settings')
    if not form.is_valid():
        return refuse(request, form_error_lines(form), retarget=SLOT, fallback=fallback)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
        return response
    update_default_due_time(get_account(request.user), form.cleaned_data['default_due_time'])
    if not is_htmx(request):
        messages.success(request, 'Saved.')
        return redirect(fallback)
    return render(request, 'todo/partials/settings_form.html', _form_context(request, saved=True))
