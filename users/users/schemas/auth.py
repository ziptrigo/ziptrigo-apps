from pydantic import BaseModel, EmailStr, field_validator

from ..validators import PasswordValidator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password meets requirements."""
        validator = PasswordValidator()
        validator.validate(v)
        return v


class EmailConfirmRequest(BaseModel):
    token: str


class ResendConfirmationRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    password: str
    password_confirm: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password meets requirements."""
        validator = PasswordValidator()
        validator.validate(v)
        return v

    def validate_passwords_match(self) -> bool:
        """Check if passwords match."""
        return self.password == self.password_confirm


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'Bearer'
    expires_in: int
