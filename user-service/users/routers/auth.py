from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

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
from ..services.email_confirmation import get_email_confirmation_service
from ..services.password_reset import get_password_reset_service
from ..tokens import CustomAccessToken, CustomRefreshToken

router = Router()


@router.post('/signup', response={201: dict}, auth=None)
def signup(request: HttpRequest, payload: SignupRequest):
    """Create a new user account and send confirmation email."""
    # Check if user exists
    if User.objects.filter(email=payload.email).exists():
        raise HttpError(400, 'User with that email already exists.')

    # Create user
    try:
        user = User.objects.create_user(
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
    except ValidationError as e:
        raise HttpError(400, str(e))

    # Send confirmation email
    service = get_email_confirmation_service()
    service.send_confirmation_email(user)

    return 201, {'message': 'Account created! Please check your email to confirm your address.'}


@router.post('/confirm-email', response={200: dict}, auth=None)
def confirm_email(request: HttpRequest, payload: EmailConfirmRequest):
    """Confirm email using a valid token."""
    service = get_email_confirmation_service()
    user = service.validate_token(payload.token)

    if user is None:
        raise HttpError(400, 'Invalid or expired token.')

    service.confirm_email(user)
    return 200, {'message': 'Email has been confirmed.'}


@router.post('/resend-confirmation', response={200: dict}, auth=None)
def resend_confirmation(request: HttpRequest, payload: ResendConfirmationRequest):
    """Resend email confirmation link."""
    try:
        user = User.objects.get(email=payload.email)
        if not user.email_confirmed:
            service = get_email_confirmation_service()
            service.send_confirmation_email(user)
    except User.DoesNotExist:
        pass  # Don't reveal whether the email exists

    return 200, {
        'message': 'If the account exists and is not yet confirmed, '
        'a confirmation email will be sent.'
    }


@router.post('/forgot-password', response={200: dict}, auth=None)
def forgot_password(request: HttpRequest, payload: PasswordResetRequest):
    """Start password reset flow for the given email."""
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
    user: User | None = authenticate(request, email=payload.email, password=payload.password)

    if user is None:
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
        expires_in=int(settings.NINJA_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),  # type: ignore
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
        expires_in=int(settings.NINJA_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),  # type: ignore
    )
