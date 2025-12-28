"""Pydantic schemas for authentication endpoints."""

from pydantic import BaseModel, EmailStr, Field, field_validator


class SignupSchema(BaseModel):
    """Schema for user signup."""

    name: str = Field(..., min_length=1, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=6)

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password contains at least one digit."""
        if not any(char.isdigit() for char in v):
            raise ValueError('Password must contain at least one digit')
        return v


class LoginSchema(BaseModel):
    """Schema for user login."""

    email: EmailStr
    password: str


class TokenResponseSchema(BaseModel):
    """Schema for JWT token response."""

    access: str
    refresh: str


class EmailConfirmSchema(BaseModel):
    """Schema for email confirmation."""

    token: str


class PasswordResetRequestSchema(BaseModel):
    """Schema for password reset request."""

    email: EmailStr


class PasswordResetSchema(BaseModel):
    """Schema for password reset."""

    token: str
    password: str = Field(..., min_length=6)
    password_confirm: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password contains at least one digit."""
        if not any(char.isdigit() for char in v):
            raise ValueError('Password must contain at least one digit')
        return v

    def validate_passwords_match(self) -> bool:
        """Check if passwords match."""
        return self.password == self.password_confirm


class AccountUpdateSchema(BaseModel):
    """Schema for account update."""

    name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None


class PasswordChangeSchema(BaseModel):
    """Schema for password change."""

    current_password: str
    password: str = Field(..., min_length=6)
    password_confirm: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password contains at least one digit."""
        if not any(char.isdigit() for char in v):
            raise ValueError('Password must contain at least one digit')
        return v

    def validate_passwords_match(self) -> bool:
        """Check if passwords match."""
        return self.password == self.password_confirm


class UserResponseSchema(BaseModel):
    """Schema for user response."""

    id: int
    email: str
    name: str
    email_confirmed: bool
    credits: int

    model_config = {'from_attributes': True}
