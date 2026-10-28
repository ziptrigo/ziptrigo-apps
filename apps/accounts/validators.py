"""Custom password validators for user authentication."""

from functools import cache
from zoneinfo import available_timezones

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


#: The message every surface (model, forms, API schema, endpoint) gives for an unknown time zone.
UNKNOWN_TIMEZONE_MESSAGE = 'Unknown time zone.'


@cache
def known_timezone_names() -> frozenset[str]:
    """Every IANA zone name `zoneinfo` knows. Read once: `available_timezones()` walks the tz
    database on every call."""
    return frozenset(available_timezones())


def is_valid_timezone(name: str) -> bool:
    """Whether `name` is an acceptable value for `User.timezone`: empty (not set yet) or a zone in
    `zoneinfo.available_timezones()`."""
    return name == '' or name in known_timezone_names()
