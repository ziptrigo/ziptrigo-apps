from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class UserCreateRequest(BaseModel):
    email: EmailStr
    name: str = ''
    password: str | None = None
    roles: list[str] = []
    permissions: list[str] = []


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    name: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    status: str
    inactive_at: datetime | None
    inactive_reason: str
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserDeactivateRequest(BaseModel):
    reason: str = ''


class UserServiceInfo(BaseModel):
    service_id: str
    service_name: str
    roles: list[str]
    permissions: list[str]


class UserServicesListResponse(BaseModel):
    services: list[UserServiceInfo]


class UserServiceAssignmentUpdate(BaseModel):
    roles: list[str] = []
    permissions: list[str] = []
