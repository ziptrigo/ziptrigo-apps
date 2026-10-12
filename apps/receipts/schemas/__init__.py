"""Pydantic/ninja schemas for the receipts JWT API (`/api/receipts/`)."""

from .category import CategoryNameSchema, CategoryOrderSchema, CategorySchema
from .inbox import ForwardingConfirmationSchema, InboxSchema, InboxUpdateSchema
from .receipt import (
    CategoryRefSchema,
    FileUrlSchema,
    ReceiptListSchema,
    ReceiptSchema,
    ReceiptUpdateSchema,
)
from .upload import UploadStartSchema, UploadTicketSchema

__all__ = [
    'CategoryNameSchema',
    'CategoryOrderSchema',
    'CategoryRefSchema',
    'CategorySchema',
    'FileUrlSchema',
    'ForwardingConfirmationSchema',
    'InboxSchema',
    'InboxUpdateSchema',
    'ReceiptListSchema',
    'ReceiptSchema',
    'ReceiptUpdateSchema',
    'UploadStartSchema',
    'UploadTicketSchema',
]
