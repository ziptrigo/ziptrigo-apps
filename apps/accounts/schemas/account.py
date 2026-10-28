from pydantic import BaseModel, EmailStr, Field, field_validator

from ..validators import UNKNOWN_TIMEZONE_MESSAGE, is_valid_timezone

#: `User.name`'s `max_length` (a test keeps the two equal).
NAME_MAX_LENGTH = 255


class AccountSchema(BaseModel):
    """The current user's account, as `GET /api/account` returns it."""

    id: str
    name: str
    email: str
    # An IANA name, or `""` while none is set (treated as UTC).
    timezone: str


class AccountUpdateRequest(BaseModel):
    """Schema for updating user account information."""

    # A longer value is a 422 here rather than a database error (a 500) on Postgres.
    name: str | None = Field(None, max_length=NAME_MAX_LENGTH)
    email: EmailStr | None = None
    # Required to change the email (a bearer token alone must not be able to move the account to
    # another address); ignored otherwise.
    current_password: str | None = None
    # An IANA zone name (`Europe/Lisbon`), or `""` to clear it. Not security-relevant, so it needs
    # no password. An unknown name is a 422.
    timezone: str | None = None

    @field_validator('timezone')
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_timezone(value):
            raise ValueError(UNKNOWN_TIMEZONE_MESSAGE)
        return value


class AccountUpdateResponse(BaseModel):
    """Response schema for account update."""

    message: str
    user: dict
