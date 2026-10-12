"""The account page's "Change Password" form."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import ChangePasswordForm, ProfileForm
from ..http import AuthenticatedHttpRequest
from ..services.password_change import change_password

RATE_LIMITED_MESSAGE = 'Too many wrong passwords. Please wait a while and try again.'


@login_required
@require_POST
def password_change(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Change the password from the account page, and email the owner that it changed.

    Keeps this session signed in (`update_session_auth_hash`); every other session of the user is
    signed out, since Django checks a session against the password hash. For htmx, answers with
    the re-rendered form (422 when invalid, 429 when rate limited). Without htmx, redirects back to
    the account page, or re-renders it with the errors.

    A wrong *current* password counts against `CHANGE_PASSWORD_USER` (peeked first, recorded only
    on a failure, like login's `LOGIN_ACCOUNT`), so a stolen session can't be used to brute-force
    the password, while typos in the new password never use up the budget.
    """
    user = request.user
    if not user.has_usable_password():
        # A "Sign in with Google" account has no password to change; the page doesn't show this
        # form for it (it links to forgot-password instead).
        return redirect('accounts:account')

    limited = ratelimit.peek(str(user.pk), 'CHANGE_PASSWORD_USER')
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

    if form.current_password_invalid:
        ratelimit.hit_user(user, 'CHANGE_PASSWORD_USER')
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
    context = {'form': ProfileForm(instance=request.user), 'password_form': form}
    return render(request, 'accounts/account.html', context, status=status)
