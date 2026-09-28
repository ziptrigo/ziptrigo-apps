from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from ..forms import ProfileForm, ResendConfirmationForm
from ..http import AuthenticatedHttpRequest
from ..services.email_confirmation import format_validity_minutes, get_email_confirmation_service


@login_required
def account_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    return render(request, 'accounts/account.html', {'form': ProfileForm(instance=request.user)})


def account_created_page(request: HttpRequest) -> HttpResponse:
    """Render the account created confirmation page."""
    validity_minutes = settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS * 60
    return render(
        request,
        'accounts/account_created.html',
        {'confirmation_validity': format_validity_minutes(validity_minutes)},
    )


def confirm_email_page(request: HttpRequest, token: str) -> HttpResponse:
    """Validate email confirmation token and redirect accordingly."""
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
