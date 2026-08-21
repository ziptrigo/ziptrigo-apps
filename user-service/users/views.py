from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .models import CreditTransaction, User
from .services.email_confirmation import get_email_confirmation_service
from .services.password_reset import get_password_reset_service


class AuthenticatedHttpRequest(HttpRequest):
    """`HttpRequest` typed with the `.user` attribute `AuthenticationMiddleware` adds at runtime.

    `HttpRequest` itself doesn't declare `.user` -- `ty` has no equivalent of django-stubs' plugin
    to know middleware adds it. Narrowed to the concrete `User` (not unioned with `AnonymousUser`)
    because `@login_required` redirects anonymous requests before the view body runs.
    """

    user: User


class MaybeAuthenticatedHttpRequest(HttpRequest):
    """Same as `AuthenticatedHttpRequest`, but for views that check `.is_authenticated` themselves
    instead of relying on `@login_required`, so `.user` may still be `AnonymousUser`.
    """

    user: User | AnonymousUser


@login_required
def account_page(request: HttpRequest) -> HttpResponse:
    """Render the account settings page for the authenticated user."""
    return render(request, 'account.html')


@login_required
def credits_history_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render the credits usage history page for the authenticated user."""
    user = request.user

    queryset = CreditTransaction.objects.filter(user=user).order_by('-created_at', '-id')
    paginator = Paginator(queryset, per_page=25)

    page_number = request.GET.get('page', '1')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'credits_balance': user.credits,
    }
    return render(request, 'credits_history.html', context)


def login_page(request: HttpRequest) -> HttpResponse:
    """Render the login page."""
    return render(request, 'login.html')


def register_page(request: HttpRequest) -> HttpResponse:
    """Render the register page."""
    return render(request, 'register.html')


def account_created_page(request: HttpRequest) -> HttpResponse:
    """Render the account created confirmation page."""
    return render(request, 'account_created.html')


def forgot_password_page(request: HttpRequest) -> HttpResponse:
    """Render the forgot password page."""
    return render(request, 'forgot_password.html')


def reset_password_page(request: HttpRequest, token: str) -> HttpResponse:
    """Render the reset password page or expired page based on token."""
    service = get_password_reset_service()
    user = service.validate_token(token)

    if user is None:
        return render(request, 'reset_password_expired.html')

    return render(request, 'reset_password.html', {'token': token})


def confirm_email_page(request: HttpRequest, token: str) -> HttpResponse:
    """Validate email confirmation token and redirect accordingly."""
    service = get_email_confirmation_service()
    user = service.validate_token(token)

    if user is None:
        return render(request, 'email_confirmation_expired.html')

    # Confirm the email
    service.confirm_email(user)
    return redirect('email-confirmation-success')


def email_confirmation_success(request: HttpRequest) -> HttpResponse:
    """Render the email confirmation success page."""
    return render(request, 'email_confirmation_success.html')


def logout_page(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
    """Log out the current user and redirect to the homepage."""
    if request.user.is_authenticated:
        auth_logout(request)
    return redirect('home')
