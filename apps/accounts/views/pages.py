from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.core.models import CoreSettings

from ..forms import ProfileForm
from ..http import AuthenticatedHttpRequest
from ..services.email_confirmation import format_validity_minutes, get_email_confirmation_service
from ..services.password_reset import get_password_reset_service


@login_required
def account_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    return render(request, 'accounts/account.html', {'form': ProfileForm(instance=request.user)})


def register_page(request: HttpRequest) -> HttpResponse:
    """Render the register page."""
    return render(request, 'accounts/register.html')


def account_created_page(request: HttpRequest) -> HttpResponse:
    """Render the account created confirmation page."""
    validity_minutes = CoreSettings.load().email_verification_validity_minutes
    return render(
        request,
        'accounts/account_created.html',
        {'confirmation_validity': format_validity_minutes(validity_minutes)},
    )


def forgot_password_page(request: HttpRequest) -> HttpResponse:
    """Render the forgot password page."""
    return render(request, 'accounts/forgot_password.html')


def reset_password_page(request: HttpRequest, token: str) -> HttpResponse:
    """Render the reset password page or expired page based on token."""
    service = get_password_reset_service()
    user = service.validate_token(token)

    if user is None:
        return render(request, 'accounts/reset_password_expired.html')

    return render(request, 'accounts/reset_password.html', {'token': token})


def confirm_email_page(request: HttpRequest, token: str) -> HttpResponse:
    """Validate email confirmation token and redirect accordingly."""
    service = get_email_confirmation_service()
    user = service.confirm_token(token)

    if user is None:
        return render(request, 'accounts/email_confirmation_expired.html')

    return redirect('accounts:email-confirmed')


def email_confirmation_success(request: HttpRequest) -> HttpResponse:
    """Render the email confirmation success page."""
    return render(request, 'accounts/email_confirmation_success.html')
