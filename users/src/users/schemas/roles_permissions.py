from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PermissionCreate(BaseModel):
    code: str
    description: str = ''
    type: str = 'SERVICE'
    service: UUID | None = None


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    description: str
    type: str
    service: UUID | None
    created_at: datetime
    updated_at: datetime


class PermissionListResponse(BaseModel):
    permissions: list[PermissionResponse]


class RoleCreate(BaseModel):
    name: str
    description: str = ''
    service: UUID | None = None
    permissions: list[str] = []


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    service: UUID | None
    created_at: datetime
    updated_at: datetime


class RoleListResponse(BaseModel):
    roles: list[RoleResponse]
