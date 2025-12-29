"""Custom password validators for user authentication."""

from django.core.exceptions import ValidationError


class PasswordValidator:
    """Validate password requirements: minimum 6 chars and at least one digit."""

    def validate(self, password: str, user=None):
        """Validate the password against requirements.

        Args:
            password: The password to validate.
            user: Optional user object (not used).

        Raises:
            ValidationError: If password doesn't meet requirements.
        """
        if len(password) < 6:
            raise ValidationError('Password must be at least 6 characters long.')

        if not any(char.isdigit() for char in password):
            raise ValidationError('Password must contain at least one digit.')

    def get_help_text(self):
        """Return help text for password requirements."""
        return 'Password must be at least 6 characters long and contain at least one digit.'
