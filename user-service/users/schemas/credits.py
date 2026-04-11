from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CreditTransactionRequest(BaseModel):
    """Request schema for creating a credit transaction."""

    transaction_type: str
    amount: int
    description: str = ''


class CreditTransactionResponse(BaseModel):
    """Response schema for credit transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    amount: int
    type: str
    description: str
    created_at: datetime


class UserCreditsResponse(BaseModel):
    """Response schema for user credits balance."""

    user_id: UUID
    credits: int
