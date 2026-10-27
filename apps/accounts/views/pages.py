from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.core import ratelimit

from ..forms import ChangePasswordForm, ProfileForm, ResendConfirmationForm, ThemeForm
from ..http import AuthenticatedHttpRequest
from ..services.email_confirmation import format_validity_minutes, get_email_confirmation_service


def account_page_context(request: AuthenticatedHttpRequest, **overrides: Any) -> dict[str, Any]:
    """The account settings page's context: one fresh form per section, `overrides` replacing any.

    The profile, password and theme views all re-render the whole page (when htmx isn't used, or to
    show errors), so the forms are built here once rather than each view naming the other
    sections' forms.
    """
    context: dict[str, Any] = {
        'form': ProfileForm(instance=request.user),
        'password_form': ChangePasswordForm(request.user),
        'theme_form': ThemeForm(),
    }
    context.update(overrides)
    return context


@login_required
def account_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    return render(request, 'accounts/account.html', account_page_context(request))


def account_created_page(request: HttpRequest) -> HttpResponse:
    """Render the "check your email" page every valid signup lands on -- the same page whether or
    not the address already had an account (issue #125)."""
    validity_minutes = settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS * 60
    return render(
        request,
        'accounts/account_created.html',
        {'confirmation_validity': format_validity_minutes(validity_minutes)},
    )


@require_http_methods(['GET', 'POST'])
def confirm_email_page(request: HttpRequest, token: str) -> HttpResponse:
    """The emailed confirmation link: GET shows a CSRF-protected button, only POST confirms.

    Mail security filters and link previewers fetch a link's URL before the recipient ever clicks
    it. If a bare GET confirmed, an attacker who registered someone else's address with a password
    of their own could have the victim's mail provider confirm that account with no human click
    (and resend the link at will). The page makes no state change and does not look at the token,
    so a scanner learns nothing either; an invalid or expired one is reported after the click,
    by redirecting (post/redirect/get) to `email_confirmation_expired`, so a refresh of that page
    never re-posts the token. Only the POST is rate limited (`CONFIRM_EMAIL_IP`).
    """
    if request.method == 'GET':
        return render(request, 'accounts/confirm_email.html', {'token': token})

    limited = ratelimit.hit_ip(request, 'CONFIRM_EMAIL_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    service = get_email_confirmation_service()
    user = service.confirm_token(token)

    if user is None:
        return redirect('accounts:email-confirmation-expired')

    return redirect('accounts:email-confirmed')


def email_confirmation_expired(request: HttpRequest) -> HttpResponse:
    """The "link expired or invalid" page, with the resend-confirmation form. It reads no token,
    so any visitor can open it; it reveals nothing."""
    return render(
        request,
        'accounts/email_confirmation_expired.html',
        {'form': ResendConfirmationForm()},
    )


def email_confirmation_success(request: HttpRequest) -> HttpResponse:
    """Render the email confirmation success page."""
    return render(request, 'accounts/email_confirmation_success.html')
