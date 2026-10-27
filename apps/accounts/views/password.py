"""The account page's "Change Password" form."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ChangePasswordForm
from ..http import AuthenticatedHttpRequest
from ..services.password_change import change_password
from .pages import account_page_context

RATE_LIMITED_MESSAGE = 'Too many attempts. Please wait a while and try again.'


@login_required
@require_POST
def password_change(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Change the password from the account page, and email the owner that it changed.

    Keeps this session signed in (`update_session_auth_hash`); every other session of the user is
    signed out, since Django checks a session against the password hash. For htmx, answers with
    the re-rendered form (422 when invalid, 429 when rate limited). Without htmx, redirects back to
    the account page, or re-renders it with the errors.

    Every attempt counts against `CHANGE_PASSWORD_USER`, before the current password is checked,
    so a stolen session can't brute-force the password -- not even with parallel requests, which
    a "check, then record only failures" counter lets through in a burst. Only the session's own
    user can spend this budget, so counting every attempt can't lock anyone else out.
    """
    user = request.user
    if not user.has_usable_password():
        # A "Sign in with Google" account has no password to change; the page doesn't show this
        # form for it (it links to forgot-password instead).
        return hx_redirect(request, reverse('accounts:account'))

    limited = ratelimit.hit_user(user, 'CHANGE_PASSWORD_USER')
    if not limited.allowed:
        # Unbound, so the form never checks the password while rate limited.
        form = ChangePasswordForm(user)
        form.cleaned_data = {}
        form.add_error(None, RATE_LIMITED_MESSAGE)
        response = _render(request, form, status=429)
        response['Retry-After'] = str(limited.retry_after)
        return response

    form = ChangePasswordForm(user, request.POST)
    if form.is_valid():
        change_password(user, form.cleaned_data['new_password'])
        update_session_auth_hash(request, user)
        if not is_htmx(request):
            messages.success(request, 'Your password has been changed.')
            return redirect('accounts:account')
        return _render(request, ChangePasswordForm(user), changed=True)

    return _render(request, form, status=422)


def _render(
    request: AuthenticatedHttpRequest,
    form: ChangePasswordForm,
    *,
    status: int = 200,
    changed: bool = False,
) -> HttpResponse:
    if is_htmx(request):
        context = {'password_form': form, 'password_changed': changed}
        return render(request, 'accounts/partials/password_form.html', context, status=status)
    context = account_page_context(request, password_form=form)
    return render(request, 'accounts/account.html', context, status=status)
