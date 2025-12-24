import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import User as UserType

User = get_user_model()


class SignupSerializer(serializers.Serializer):
    """Serializer for user signup."""

    name = serializers.CharField(max_length=255, required=True)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True,
        required=True,
        help_text='Password must be at least 6 characters and contain at least one digit',
    )

    def validate_password(self, value: str) -> str:
        """Validate password strength: minimum 6 chars and at least one digit."""
        if len(value) < 6:
            raise serializers.ValidationError('Password must be at least 6 characters long.')
        if not re.search(r'\d', value):
            raise serializers.ValidationError('Password must contain at least one digit.')
        return value

    def validate_email(self, value: str) -> str:
        """Check if email already exists."""
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('User with that email already exists.')
        return value

    def create(self, validated_data: dict) -> UserType:
        """Create and return a new user."""
        user = User.objects.create_user(
            username=validated_data['email'],
            email=validated_data['email'],
            password=validated_data['password'],
            name=validated_data['name'],
        )
        return user


class LoginSerializer(serializers.Serializer):
    """Serializer for user login."""

    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)


class AccountUpdateSerializer(serializers.Serializer):
    """Serializer for updating user account information."""

    name = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False)
    current_password = serializers.CharField(write_only=True, required=False)
    new_password = serializers.CharField(write_only=True, required=False)
    new_password_confirm = serializers.CharField(write_only=True, required=False)

    def validate_email(self, value: str) -> str:
        """Check if email is available (excluding current user)."""
        user = self.context.get('user')
        if user and User.objects.filter(email=value).exclude(id=user.id).exists():
            raise serializers.ValidationError('User with that email already exists.')
        return value

    def validate_new_password(self, value: str) -> str:
        """Validate password strength: minimum 6 chars and at least one digit."""
        if len(value) < 6:
            raise serializers.ValidationError('Password must be at least 6 characters long.')
        if not re.search(r'\d', value):
            raise serializers.ValidationError('Password must contain at least one digit.')
        return value

    def validate(self, data: dict) -> dict:
        """Cross-field validation for password changes."""
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        new_password_confirm = data.get('new_password_confirm')

        # If any password field is provided, all must be provided
        password_fields = [current_password, new_password, new_password_confirm]
        if any(password_fields) and not all(password_fields):
            raise serializers.ValidationError(
                'To change password, provide current_password, new_password, and '
                'new_password_confirm.'
            )

        # Validate password confirmation matches
        if new_password and new_password_confirm and new_password != new_password_confirm:
            raise serializers.ValidationError({'new_password_confirm': 'Passwords do not match.'})

        # Validate current password
        if current_password:
            user = self.context.get('user')
            if not user or not user.check_password(current_password):
                raise serializers.ValidationError(
                    {'current_password': 'Current password is incorrect.'}
                )

        return data
