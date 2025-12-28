"""Async authentication endpoints for Django Ninja."""

from asgiref.sync import sync_to_async
from django.contrib.auth import authenticate, get_user_model
from ninja import Router
from ninja_jwt.authentication import AsyncJWTAuth
from qr_code.schemas import (
    AccountUpdateSchema,
    EmailConfirmSchema,
    LoginSchema,
    PasswordChangeSchema,
    PasswordResetRequestSchema,
    PasswordResetSchema,
    SignupSchema,
    TokenResponseSchema,
    UserResponseSchema,
)
from qr_code.services.email_confirmation import get_email_confirmation_service
from qr_code.services.password_reset import get_password_reset_service

User = get_user_model()
router = Router()


@router.post('/signup', response={201: dict}, auth=None)
async def signup(request, payload: SignupSchema):
    """Create a new user account and send confirmation email."""
    # Check if user exists
    exists = await sync_to_async(User.objects.filter(email=payload.email).exists)()
    if exists:
        return 400, {'detail': 'User with that email already exists.'}

    # Create user
    user = await sync_to_async(User.objects.create_user)(
        username=payload.email,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )

    # Send confirmation email using JWT token
    service = get_email_confirmation_service()
    await service.send_confirmation_email(user)

    return 201, {'message': 'Account created! Please check your email to confirm your address.'}


@router.post('/login', response=TokenResponseSchema, auth=None)
async def login_view(request, payload: LoginSchema):
    """Authenticate user and return JWT tokens."""
    user = await sync_to_async(authenticate)(
        request, username=payload.email, password=payload.password
    )

    if user is None:
        return 400, {'detail': 'Invalid credentials.'}

    if not user.email_confirmed:
        return 403, {
            'detail': 'Please confirm your email address before logging in. '
            'Check your inbox for the confirmation link.'
        }

    # Generate JWT tokens using ninja-jwt
    from ninja_jwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)

    return {
        'access': str(refresh.access_token),  # type: ignore[attr-defined]
        'refresh': str(refresh),
    }


@router.get('/me', response=UserResponseSchema, auth=AsyncJWTAuth())
async def get_current_user(request):
    """Get current authenticated user info."""
    return await sync_to_async(lambda: request.auth)()


@router.post('/confirm-email', response={200: dict}, auth=None)
async def confirm_email(request, payload: EmailConfirmSchema):
    """Confirm email using a valid token."""
    service = get_email_confirmation_service()
    user = await service.validate_token(payload.token)

    if user is None:
        return 400, {'detail': 'Invalid or expired token.'}

    await service.confirm_email(user)
    return 200, {'message': 'Email has been confirmed.'}


@router.post('/resend-confirmation', response={200: dict}, auth=None)
async def resend_confirmation(request, email: str):
    """Resend email confirmation link."""
    if not email:
        return 400, {'detail': 'Email is required.'}

    try:
        user = await sync_to_async(User.objects.get)(email=email)
        if not user.email_confirmed:
            service = get_email_confirmation_service()
            await service.send_confirmation_email(user)
    except User.DoesNotExist:
        pass  # Don't reveal whether the email exists

    return 200, {
        'message': 'If the account exists and is not yet confirmed, '
        'a confirmation email will be sent.'
    }


@router.post('/forgot-password', response={200: dict}, auth=None)
async def forgot_password(request, payload: PasswordResetRequestSchema):
    """Start password reset flow for the given email."""
    service = get_password_reset_service()
    await service.request_reset(email=payload.email)

    return 200, {
        'message': 'If the account exists, an email will be sent with a password reset link.'
    }


@router.post('/reset-password', response={200: dict}, auth=None)
async def reset_password(request, payload: PasswordResetSchema):
    """Reset password using a valid token."""
    if not payload.validate_passwords_match():
        return 400, {'detail': 'Passwords do not match.'}

    service = get_password_reset_service()
    user = await service.validate_token(payload.token)

    if user is None:
        return 400, {'detail': 'Invalid or expired token.'}

    user.set_password(payload.password)
    await sync_to_async(user.save)(update_fields=['password'])

    return 200, {'message': 'Password has been reset.'}


@router.put('/account', response=UserResponseSchema, auth=AsyncJWTAuth())
async def update_account(request, payload: AccountUpdateSchema):
    """Update current user's account information."""
    user = request.auth

    if payload.name:
        user.name = payload.name

    if payload.email and payload.email != user.email:
        # Check if email already exists
        exists = await sync_to_async(
            User.objects.filter(email=payload.email).exclude(id=user.id).exists
        )()
        if exists:
            return 400, {'detail': 'Email already in use.'}

        user.email = payload.email
        user.email_confirmed = False  # Need to reconfirm new email

        # Send confirmation email to new address
        service = get_email_confirmation_service()
        await sync_to_async(user.save)()
        await service.send_confirmation_email(user)
    else:
        await sync_to_async(user.save)()
    return await sync_to_async(lambda: user)()


@router.post('/change-password', response={200: dict}, auth=AsyncJWTAuth())
async def change_password(request, payload: PasswordChangeSchema):
    """Change current user's password."""
    user = request.auth

    if not payload.validate_passwords_match():
        return 400, {'detail': 'Passwords do not match.'}

    # Verify current password
    if not user.check_password(payload.current_password):
        return 400, {'detail': 'Current password is incorrect.'}

    user.set_password(payload.password)
    await sync_to_async(user.save)(update_fields=['password'])

    return 200, {'message': 'Password has been changed.'}
