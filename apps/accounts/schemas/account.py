from pydantic import BaseModel, EmailStr, Field

#: `User.name`'s `max_length` (a test keeps the two equal).
NAME_MAX_LENGTH = 255


class AccountUpdateRequest(BaseModel):
    """Schema for updating user account information."""

    # A longer value is a 422 here rather than a database error (a 500) on Postgres.
    name: str | None = Field(None, max_length=NAME_MAX_LENGTH)
    email: EmailStr | None = None
    # Required to change the email (a bearer token alone must not be able to move the account to
    # another address); ignored otherwise.
    current_password: str | None = None


class AccountUpdateResponse(BaseModel):
    """Response schema for account update."""

    message: str
    user: dict
