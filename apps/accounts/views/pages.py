from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..forms import ChangePasswordForm, ProfileForm, ResendConfirmationForm
from ..http import AuthenticatedHttpRequest
from ..services.email_confirmation import format_validity_minutes, get_email_confirmation_service


@login_required
def account_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    context = {
        'form': ProfileForm(instance=request.user),
        'password_form': ChangePasswordForm(request.user),
    }
    return render(request, 'accounts/account.html', context)


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
    so a scanner learns nothing either; an invalid or expired one is reported after the click.
    """
    if request.method == 'GET':
        return render(request, 'accounts/confirm_email.html', {'token': token})

    service = get_email_confirmation_service()
    user = service.confirm_token(token)

    if user is None:
        return render(
            request,
            'accounts/email_confirmation_expired.html',
            {'form': ResendConfirmationForm()},
        )

    return redirect('accounts:email-confirmed')


def email_confirmation_success(request: HttpRequest) -> HttpResponse:
    """Render the email confirmation success page."""
    return render(request, 'accounts/email_confirmation_success.html')
