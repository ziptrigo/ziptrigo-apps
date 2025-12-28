from django.conf import settings
from django.contrib.auth import authenticate
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from ..models import User
from ..schemas import LoginRequest, RefreshRequest, TokenResponse
from ..tokens import CustomAccessToken, CustomRefreshToken

router = Router()


@router.post('/login', response=TokenResponse, auth=None)
def login(request: HttpRequest, payload: LoginRequest) -> TokenResponse:
    """User login endpoint - returns JWT access and refresh tokens for valid credentials."""
    user: User | None = authenticate(request, email=payload.email, password=payload.password)

    if user is None:
        raise HttpError(400, 'Invalid credentials')

    if user.status != User.STATUS_ACTIVE:
        raise HttpError(403, 'User not active')

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
