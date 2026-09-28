"""The reset-password page (issue #52): session view replacing the old `reset_password.html`
`fetch()` flow to `POST /api/auth/reset-password` (CLAUDE.md's "HTMX form views" convention). No
rate limit here, matching that endpoint -- the token itself (`apps.accounts.tokens.PasswordResetToken`)
is the unguessable secret, unlike a login password or a short download-page password.
"""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ResetPasswordForm
from ..services.password_reset import get_password_reset_service


@require_http_methods(['GET', 'POST'])
def reset_password_page(request: HttpRequest, token: str) -> HttpResponse:
    """Show the reset-password form for a valid token, and set the new password with it.

    Re-validates the token on every request, not just the first `GET` -- a `POST` whose token has
    since expired redirects back to this same URL rather than rendering the expired-link page's
    full, `core/base.html`-extending markup as an htmx partial (which would insert a whole
    `<html>` document into the swap target).
    """
    service = get_password_reset_service()
    user = service.validate_token(token)
    if user is None:
        if request.method == 'POST':
            return hx_redirect(request, request.path)
        return render(request, 'accounts/reset_password_expired.html')

    status = 200
    if request.method == 'POST':
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            user.set_password(form.cleaned_data['password'])
            user.save(update_fields=['password'])
            messages.success(request, 'Your password has been reset. You can now log in.')
            return hx_redirect(request, reverse('accounts:login'))
        status = 422
    else:
        form = ResetPasswordForm()

    template = (
        'accounts/partials/reset_password_form.html'
        if request.method == 'POST' and is_htmx(request)
        else 'accounts/reset_password.html'
    )
    return render(request, template, {'form': form, 'token': token}, status=status)
