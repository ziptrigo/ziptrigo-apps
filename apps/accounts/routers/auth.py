from django.conf import settings
from django.contrib.auth import authenticate
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.core import ratelimit

from ..models import User
from ..schemas import (
    EmailConfirmRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    ResendConfirmationRequest,
    SignupRequest,
    TokenResponse,
)
from ..services import login_throttle
from ..services.email_confirmation import get_email_confirmation_service
from ..services.password_reset import get_password_reset_service
from ..services.signup import EmailAlreadyRegistered, create_account
from ..tokens import CustomAccessToken, CustomRefreshToken

router = Router()


@router.post('/signup', response={201: dict}, auth=None)
def signup(request: HttpRequest, payload: SignupRequest):
    """Create a new user account and send confirmation email."""
    ratelimit.enforce(ratelimit.hit_ip(request, 'SIGNUP_IP'))

    try:
        create_account(name=payload.name, email=payload.email, password=payload.password)
    except EmailAlreadyRegistered:
        raise HttpError(400, 'User with that email already exists.')

    return 201, {'message': 'Account created! Please check your email to confirm your address.'}


@router.post('/confirm-email', response={200: dict}, auth=None)
def confirm_email(request: HttpRequest, payload: EmailConfirmRequest):
    """Confirm email using a valid token."""
    service = get_email_confirmation_service()
    user = service.confirm_token(payload.token)

    if user is None:
        raise HttpError(400, 'Invalid or expired token.')

    return 200, {'message': 'Email has been confirmed.'}


@router.post('/resend-confirmation', response={200: dict}, auth=None)
def resend_confirmation(request: HttpRequest, payload: ResendConfirmationRequest):
    """Resend email confirmation link."""
    ratelimit.enforce(ratelimit.hit_ip(request, 'RESEND_CONFIRMATION_IP'))
    # The per-email-per-day cap is enforced centrally, for every caller of `start()` regardless of
    # purpose -- see `apps.core.services.email_verification.EmailVerificationRateLimited`.

    get_email_confirmation_service().resend_if_unconfirmed(payload.email)

    return 200, {
        'message': 'If the account exists and is not yet confirmed, '
        'a confirmation email will be sent.'
    }


@router.post('/forgot-password', response={200: dict}, auth=None)
def forgot_password(request: HttpRequest, payload: PasswordResetRequest):
    """Start password reset flow for the given email."""
    # None of these checks leak whether the account exists (the response is identical either
    # way) -- see CLAUDE.md. Three rules, not a hard lockout (issue #53 code review): `_IP` throttles
    # scripted abuse regardless of target; `_EMAIL_IP`, strict, stops one IP from burning through a
    # specific victim's budget on its own; `_EMAIL`, looser but shared across every IP, is what
    # actually protects a victim's own ability to request a reset -- a third party now needs many
    # different IPs to exhaust it and block them, not just a handful of requests from one.
    ratelimit.enforce(ratelimit.hit_ip(request, 'FORGOT_PASSWORD_IP'))
    ratelimit.enforce(
        ratelimit.hit_ip_and_value(request, payload.email.lower(), 'FORGOT_PASSWORD_EMAIL_IP')
    )
    ratelimit.enforce(ratelimit.hit_value(payload.email.lower(), 'FORGOT_PASSWORD_EMAIL'))

    service = get_password_reset_service()
    service.request_reset(email=payload.email)

    return 200, {
        'message': 'If the account exists, an email will be sent with a password reset link.'
    }


@router.post('/reset-password', response={200: dict}, auth=None)
def reset_password(request: HttpRequest, payload: PasswordResetConfirm):
    """Reset password using a valid token."""
    if not payload.validate_passwords_match():
        raise HttpError(400, 'Passwords do not match.')

    service = get_password_reset_service()
    user = service.validate_token(payload.token)

    if user is None:
        raise HttpError(400, 'Invalid or expired token.')

    user.set_password(payload.password)
    user.save(update_fields=['password'])

    return 200, {'message': 'Password has been reset.'}


@router.post('/login', response=TokenResponse, auth=None)
def login(request: HttpRequest, payload: LoginRequest) -> TokenResponse:
    """User login endpoint - returns JWT access and refresh tokens for valid credentials."""
    # Same rules, and the same shared counters, as the session login view
    # (`apps.accounts.views.login.login_page`) -- see `apps.accounts.services.login_throttle`'s
    # module docstring for why these aren't a lockout.
    email = payload.email.lower()
    ratelimit.enforce(ratelimit.hit_ip(request, 'LOGIN_IP'))
    ratelimit.enforce(login_throttle.check_before_authenticate(request, email))

    user: User | None = authenticate(request, email=payload.email, password=payload.password)

    if user is None:
        login_throttle.record_failed_attempt(email)
        raise HttpError(400, 'Invalid credentials')

    if user.status != User.STATUS_ACTIVE:
        raise HttpError(403, 'User not active')

    # Check if email is confirmed
    if not user.email_confirmed:
        raise HttpError(
            403,
            'Please confirm your email address before logging in. '
            'Check your inbox for the confirmation link.',
        )

    # Generate tokens
    refresh = CustomRefreshToken.for_user(user)
    access = CustomAccessToken.for_user(user)

    return TokenResponse(
        access_token=str(access),
        refresh_token=str(refresh),
        token_type='Bearer',
        expires_in=int(settings.NINJA_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
    )


@router.post('/refresh', response=TokenResponse, auth=None)
def refresh_token(request: HttpRequest, payload: RefreshRequest) -> TokenResponse:
    """Token refresh endpoint - returns a new access token using refresh token."""
    try:
        refresh = CustomRefreshToken(payload.refresh_token)
    except Exception:
        raise HttpError(401, 'Invalid or expired refresh token')

    # Get user and validate status
    try:
        user = User.objects.get(id=refresh['sub'])
    except User.DoesNotExist:
        raise HttpError(401, 'User not found')

    if user.status != User.STATUS_ACTIVE:
        raise HttpError(403, 'User not active')

    # Generate a new access token
    access = CustomAccessToken.for_user(user)

    return TokenResponse(
        access_token=str(access),
        refresh_token=str(refresh),
        token_type='Bearer',
        expires_in=int(settings.NINJA_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
    )
