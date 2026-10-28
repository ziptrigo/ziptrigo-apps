"""`/todo/settings/`: the default due time, the default reminder, the free-tasks line and the price,
and the storage line.

Later sub-issues add their own sections (the email address, ...).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import SettingsForm
from ..forms.reminder import UNITS as REMINDER_UNITS
from ..models import ReminderChannel, TodoSettings
from ..services import (
    daily_storage_rate,
    describe_daily_rate,
    get_account,
    reminders,
    update_default_due_time,
)
from ..services.reminders import split_offset
from .common import (
    form_error_lines,
    free_line,
    limited,
    refuse,
    render_page,
    validation_lines,
)

SLOT = '#settings-msg'


def _form_context(request: AuthenticatedHttpRequest, *, saved: bool = False) -> dict:
    account = get_account(request.user)
    config = TodoSettings.load()
    minutes = account.default_reminder_minutes
    amount, unit = split_offset(minutes) if minutes is not None else (5, 'minutes')
    used, free = free_line(account, config)
    return {
        'account': account,
        'free_used': used,
        'free_total': free,
        'task_price': config.task_price,
        'stored_bytes': account.stored_bytes,
        'storage_daily_text': describe_daily_rate(daily_storage_rate(account.stored_bytes)),
        'storage_frozen': account.is_frozen,
        'saved': saved,
        'reminder_amount': amount,
        'reminder_unit': unit,
        'reminder_units': REMINDER_UNITS,
        'reminder_channels': ReminderChannel.choices,
    }


@login_required
@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
def settings_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the page. POST: save the default due time and the default reminder."""
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
    account = get_account(request.user)
    try:
        with transaction.atomic():  # both settings are saved, or neither
            update_default_due_time(account, form.cleaned_data['default_due_time'])
            if form.reminder_defaults is not None:
                reminders.set_defaults(
                    account, minutes=form.reminder_defaults[0], channel=form.reminder_defaults[1]
                )
    except ValidationError as exc:
        return refuse(request, validation_lines(exc), retarget=SLOT, fallback=fallback)
    if not is_htmx(request):
        messages.success(request, 'Saved.')
        return redirect(fallback)
    return render(request, 'todo/partials/settings_form.html', _form_context(request, saved=True))
