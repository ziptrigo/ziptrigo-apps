"""`/todo/settings/`: the "Add tasks by email" section (issue #186) -- the address (with
regenerate), the accepted senders and the recent inbound emails.

Everything that changes something calls `services.accounts`. The senders form is an htmx partial
(422 with the messages in its own slot); regenerating the address is a plain POST that redirects
with a flash message.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.field_errors import message_lines
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import InboxSettingsForm
from ..models import InboundEmail, InboundEmailStatus
from ..services import get_account, inbox_address_for, regenerate_inbox_code, update_inbox_settings
from .common import form_error_lines, limited, message_response

RECENT_EMAILS = 20
SLOT = '#inbox-settings-msg'

_OUTCOMES = {
    InboundEmailStatus.REJECTED_SENDER: 'sender not allowed',
    InboundEmailStatus.REJECTED_SPAM: 'flagged as spam',
    InboundEmailStatus.REJECTED_VIRUS: 'flagged as a virus',
    InboundEmailStatus.REJECTED_CREDITS: 'not enough credits',
    InboundEmailStatus.REJECTED_TOO_LARGE: 'too large',
    InboundEmailStatus.REJECTED_TASK_LIMIT: 'too many open tasks',
    InboundEmailStatus.NO_CONTENT: 'nothing to add',
}


def recent_emails(user) -> list[InboundEmail]:
    """The user's latest `RECENT_EMAILS` inbound emails, each with `accepted` and `outcome`
    (the words for a rejection) for the template."""
    emails = list(
        InboundEmail.objects.filter(user=user)
        .select_related('task')
        .order_by('-created_at')[:RECENT_EMAILS]
    )
    for email in emails:
        email.accepted = email.status == InboundEmailStatus.ACCEPTED
        email.outcome = _OUTCOMES.get(email.status, '')
    return emails


def senders_form_context(user, form: InboxSettingsForm | None = None, *, saved: bool = False):
    account = get_account(user)
    if form is None:
        form = InboxSettingsForm(
            initial={
                'sender_mode': 'only' if account.sender_allowlist else 'any',
                'sender_allowlist': ', '.join(account.sender_allowlist),
            }
        )
    return {'inbox_form': form, 'saved': saved}


def inbox_context(user) -> dict:
    """What the settings page needs for the email section."""
    return {
        'inbox_address': inbox_address_for(user),
        'recent_emails': recent_emails(user),
        **senders_form_context(user),
    }


@login_required
@require_POST
def inbox_update(request: AuthenticatedHttpRequest) -> HttpResponse:
    """`POST /todo/settings/inbox/`: save the accepted senders."""
    fallback = reverse('todo:settings')
    form = InboxSettingsForm(request.POST)
    if form.is_valid():
        if (response := limited(request, 'TODO_EDIT_USER', retarget=SLOT)) is not None:
            return response
        try:
            update_inbox_settings(
                request.user, sender_allowlist=form.cleaned_data['sender_allowlist']
            )
        except ValidationError as exc:
            lines = message_lines(exc)
        else:
            if not is_htmx(request):
                messages.success(request, 'Saved.')
                return redirect(fallback)
            return render(
                request,
                'todo/partials/settings_inbox_form.html',
                senders_form_context(request.user, saved=True),
            )
    else:
        lines = form_error_lines(form)

    if is_htmx(request):
        return message_response(request, lines, retarget=SLOT)
    for line in lines:
        messages.error(request, line)
    return redirect(fallback)


@login_required
@require_POST
def inbox_regenerate(request: AuthenticatedHttpRequest) -> HttpResponse:
    """`POST /todo/settings/regenerate/`: retire the address and issue a new one."""
    result = ratelimit.hit_user(request.user, 'TODO_REGENERATE_USER')
    if not result.allowed:
        return ratelimit.web_response(request, result)
    regenerate_inbox_code(request.user)
    messages.success(request, 'Your new address is ready. The old one no longer works.')
    return hx_redirect(request, reverse('todo:settings'))
