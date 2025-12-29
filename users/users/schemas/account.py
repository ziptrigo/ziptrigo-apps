from pydantic import BaseModel, EmailStr


class AccountUpdateRequest(BaseModel):
    """Schema for updating user account information."""

    name: str | None = None
    email: EmailStr | None = None


class AccountUpdateResponse(BaseModel):
    """Response schema for account update."""

    message: str
    user: dict
