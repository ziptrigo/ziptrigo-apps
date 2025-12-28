from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ServiceCreate(BaseModel):
    name: str
    description: str = ''
    status: str = 'ACTIVE'


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    client_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class ServiceListResponse(BaseModel):
    services: list[ServiceResponse]
