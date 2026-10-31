"""`/receipts/settings/`: the inbox address (with regenerate), accepted senders, the default
currency, the category list and the recent emails.

Everything that changes something calls `services.actions`. The inbox form and the category
editor are htmx partials (422 with the messages in their own slot); regenerating the address is a
plain POST that redirects with a flash message.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.field_errors import message_lines
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import CategoryNameForm, InboxSettingsForm
from ..models import InboundEmail, InboundEmailStatus, ReceiptCategory
from ..services import (
    create_category,
    delete_category,
    forwarding_confirmation,
    get_account,
    inbox_address_for,
    list_categories,
    regenerate_inbox_code,
    rename_category,
    reorder_categories,
    update_inbox_settings,
)
from ..services.currencies import ISO_4217_CODES
from .common import form_error_lines, message_response, render_page

_RECENT_EMAILS = 20

_OUTCOMES = {
    InboundEmailStatus.REJECTED_SENDER: 'sender not allowed',
    InboundEmailStatus.REJECTED_SPAM: 'flagged as spam',
    InboundEmailStatus.REJECTED_VIRUS: 'flagged as a virus',
    InboundEmailStatus.REJECTED_CAP: 'daily limit reached',
    InboundEmailStatus.REJECTED_TOO_LARGE: 'too large',
    InboundEmailStatus.NO_RECEIPT: 'no receipt found',
    InboundEmailStatus.FORWARDING_CONFIRMATION: 'forwarding confirmation',
}


def _recent_emails(user) -> list[InboundEmail]:
    emails = list(
        InboundEmail.objects.filter(user=user)
        .annotate(receipt_count=Count('receipts'))
        .order_by('-created_at')[:_RECENT_EMAILS]
    )
    for email in emails:
        email.accepted = email.status == InboundEmailStatus.ACCEPTED
        email.outcome = _OUTCOMES.get(email.status, '')
    return emails


def _inbox_form_context(user, form: InboxSettingsForm | None = None, *, saved: bool = False):
    account = get_account(user)
    if form is None:
        form = InboxSettingsForm(
            initial={
                'sender_mode': 'only' if account.sender_allowlist else 'any',
                'sender_allowlist': ', '.join(account.sender_allowlist),
                'default_currency': account.default_currency,
            }
        )
    return {
        'inbox_form': form,
        'currencies': sorted(ISO_4217_CODES | {account.default_currency}),
        'saved': saved,
    }


@login_required
@require_GET
@ensure_csrf_cookie
def settings_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    context = {
        'inbox_address': inbox_address_for(request.user),
        'categories': list_categories(request.user),
        'recent_emails': _recent_emails(request.user),
        'confirmation': forwarding_confirmation(request.user),
        **_inbox_form_context(request.user),
    }
    return render_page(request, 'receipts/settings.html', context)


# -- Address and accepted senders ---------------------------------------------------------------


@login_required
@require_POST
def regenerate_address(request: AuthenticatedHttpRequest) -> HttpResponse:
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_REGENERATE_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited)
    regenerate_inbox_code(request.user)
    messages.success(request, 'Your new address is ready. The old one no longer works.')
    return hx_redirect(request, reverse('receipts:settings'))


@login_required
@require_POST
def update_inbox(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = InboxSettingsForm(request.POST)
    if form.is_valid():
        try:
            update_inbox_settings(
                request.user,
                sender_allowlist=form.cleaned_data['sender_allowlist'],
                default_currency=form.cleaned_data['default_currency'],
            )
        except ValidationError as exc:
            lines = message_lines(exc)
        else:
            if not is_htmx(request):
                messages.success(request, 'Saved.')
                return redirect('receipts:settings')
            return render(
                request,
                'receipts/partials/settings_inbox_form.html',
                _inbox_form_context(request.user, saved=True),
            )
    else:
        lines = form_error_lines(form)

    if is_htmx(request):
        return message_response(request, lines, retarget='#inbox-settings-msg')
    for line in lines:
        messages.error(request, line)
    return redirect('receipts:settings')


# -- Categories ---------------------------------------------------------------------------------


def _categories_response(request: AuthenticatedHttpRequest, *, notice: str = '') -> HttpResponse:
    """The refreshed category editor for htmx; a redirect to the page otherwise."""
    if not is_htmx(request):
        if notice:
            messages.success(request, notice)
        return redirect('receipts:settings')
    return render(
        request,
        'receipts/partials/settings_categories.html',
        {'categories': list_categories(request.user), 'notice': notice},
    )


def _category_error(request: AuthenticatedHttpRequest, lines: list[str]) -> HttpResponse:
    if is_htmx(request):
        return message_response(request, lines, retarget='#category-msg')
    for line in lines:
        messages.error(request, line)
    return redirect('receipts:settings')


def _own_category(request: AuthenticatedHttpRequest, category_id: int) -> ReceiptCategory:
    return get_object_or_404(ReceiptCategory, pk=category_id, user=request.user)


@login_required
@require_POST
def category_create(request: AuthenticatedHttpRequest) -> HttpResponse:
    form = CategoryNameForm(request.POST)
    if not form.is_valid():
        return _category_error(request, form_error_lines(form))
    try:
        create_category(request.user, form.cleaned_data['name'])
    except ValidationError as exc:
        return _category_error(request, message_lines(exc))
    return _categories_response(request)


@login_required
@require_POST
def category_rename(request: AuthenticatedHttpRequest, category_id: int) -> HttpResponse:
    category = _own_category(request, category_id)
    form = CategoryNameForm(request.POST)
    if not form.is_valid():
        return _category_error(request, form_error_lines(form))
    try:
        rename_category(category, form.cleaned_data['name'])
    except ValidationError as exc:
        return _category_error(request, message_lines(exc))
    return _categories_response(request)


@login_required
@require_POST
def category_move(request: AuthenticatedHttpRequest, category_id: int) -> HttpResponse:
    """Move a category one place up or down (`direction=up|down`)."""
    category = _own_category(request, category_id)
    direction = request.POST.get('direction')
    if direction not in ('up', 'down'):
        return _category_error(request, ['Choose up or down.'])
    ordered = [c.pk for c in ReceiptCategory.objects.filter(user=request.user)]
    index = ordered.index(category.pk)
    target = index - 1 if direction == 'up' else index + 1
    if 0 <= target < len(ordered):
        ordered[index], ordered[target] = ordered[target], ordered[index]
        reorder_categories(request.user, ordered)
    return _categories_response(request)


@login_required
@require_POST
def category_delete(request: AuthenticatedHttpRequest, category_id: int) -> HttpResponse:
    category = _own_category(request, category_id)
    name = category.name
    delete_category(category)
    return _categories_response(
        request, notice=f'Deleted {name}. Its receipts are now uncategorized.'
    )
