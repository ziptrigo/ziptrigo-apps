from django.contrib.auth import authenticate, get_user_model, login
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from ..auth_serializers import (
    AccountUpdateSerializer,
    LoginSerializer,
    SignupSerializer,
)
from ..services.email_confirmation import get_email_confirmation_service
from ..services.password_reset import PasswordResetService, get_password_reset_service

User = get_user_model()


def _get_password_reset_service() -> PasswordResetService:
    return get_password_reset_service()


@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    """Signup endpoint: create user and send confirmation email."""
    serializer = SignupSerializer(data=request.data)
    if not serializer.is_valid():
        errors = serializer.errors
        if 'email' in errors:
            for err in errors['email']:
                if 'User with that email already exists.' in str(err):
                    return Response(
                        {'message': 'User with that email already exists.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    user = serializer.save()

    # Send confirmation email
    confirmation_service = get_email_confirmation_service()
    confirmation_service.send_confirmation_email(user)

    return Response(
        {'message': 'Account created! Please check your email to confirm your address.'},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """Login endpoint: authenticate and start session with optional remember flag."""
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    email = serializer.validated_data['email']
    password = serializer.validated_data['password']

    user = authenticate(request, username=email, password=password)
    if user is None:
        return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_400_BAD_REQUEST)

    # Check if email is confirmed
    if not user.email_confirmed:
        return Response(
            {
                'detail': (
                    'Please confirm your email address before logging in.\n'
                    'Check your inbox for the confirmation link.'
                )
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    login(request, user)

    remember_raw = str(request.data.get('remember', '')).lower()
    remember = remember_raw in {'1', 'true', 'on', 'yes'}
    request.session.set_expiry(1209600 if remember else 0)

    if not request.session.session_key:
        request.session.save()

    return Response(
        {
            'user': {'id': user.id, 'email': user.email, 'name': getattr(user, 'name', '')},
            'sessionid': request.session.session_key,
        }
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_password(request):
    """Start password reset flow for the given email.

    The response is always 200 with a generic message, regardless of whether the
    email exists.
    """

    email = str(request.data.get('email', '')).strip()
    if not email:
        return Response(
            {'email': ['This field is required.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    service = _get_password_reset_service()
    service.request_reset(email=email)

    return Response(
        {
            'detail': (
                'If the account exists, an email will be sent with a password ' 'reset link.'
            ),
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request):
    """Reset password using a valid token."""

    token = str(request.data.get('token', '')).strip()
    password = str(request.data.get('password', ''))
    password_confirm = str(request.data.get('password_confirm', ''))

    errors: dict[str, list[str]] = {}
    if not token:
        errors.setdefault('token', []).append('This field is required.')
    if not password:
        errors.setdefault('password', []).append('This field is required.')
    if not password_confirm:
        errors.setdefault('password_confirm', []).append('This field is required.')
    if password and password_confirm and password != password_confirm:
        errors.setdefault('password_confirm', []).append('Passwords do not match.')

    if errors:
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    service = _get_password_reset_service()
    user = service.validate_token(token)
    if user is None:
        return Response(
            {'detail': 'Invalid or expired token.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(password)
    user.save(update_fields=['password'])
    service.mark_used(user)

    return Response({'detail': 'Password has been reset.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([AllowAny])
def resend_confirmation(request):
    """Resend email confirmation link.

    The response is always 200 with a generic message, regardless of whether the
    email exists or is already confirmed.
    """

    email = str(request.data.get('email', '')).strip()
    if not email:
        return Response(
            {'email': ['This field is required.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(email=email)
        if not user.email_confirmed:
            service = get_email_confirmation_service()
            service.send_confirmation_email(user)
    except User.DoesNotExist:
        pass  # Don't reveal whether the email exists

    return Response(
        {
            'detail': (
                'If the account exists and is not yet confirmed, '
                'a confirmation email will be sent.'
            ),
        },
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def confirm_email(request):
    """Confirm email using a valid token."""

    token = str(request.data.get('token', '')).strip()

    if not token:
        return Response(
            {'token': ['This field is required.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    service = get_email_confirmation_service()
    user = service.validate_token(token)
    if user is None:
        return Response(
            {'detail': 'Invalid or expired token.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    service.confirm_email(user)

    return Response({'detail': 'Email has been confirmed.'}, status=status.HTTP_200_OK)


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def account_view(request):
    """Get or update current user's account information."""
    user = request.user

    if request.method == 'GET':
        return Response(
            {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'name': getattr(user, 'name', ''),
                'email_confirmed': getattr(user, 'email_confirmed', True),
                'credits': getattr(user, 'credits', 0),
            }
        )

    # PUT - Update account
    serializer = AccountUpdateSerializer(data=request.data, context={'user': user})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated_data = serializer.validated_data
    email_changed = False

    # Update name
    if 'name' in validated_data:
        user.name = validated_data['name']

    # Update email
    if 'email' in validated_data and validated_data['email'] != user.email:
        user.email = validated_data['email']
        user.username = validated_data['email']  # Keep username in sync
        user.email_confirmed = False
        user.email_confirmed_at = None
        email_changed = True

    # Update password
    if 'new_password' in validated_data:
        user.set_password(validated_data['new_password'])

    user.save()

    # Send confirmation email if email changed
    if email_changed:
        confirmation_service = get_email_confirmation_service()
        confirmation_service.send_confirmation_email(user)

    response_data = {
        'message': 'Account updated successfully.',
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'name': user.name,
            'email_confirmed': user.email_confirmed,
            'credits': getattr(user, 'credits', 0),
        },
    }

    if email_changed:
        response_data['email_changed'] = True  # type: ignore
        response_data['message'] += ' Please check your email to confirm your new address.'  # type: ignore

    return Response(response_data)
