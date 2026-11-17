"""Pydantic/ninja schemas for the receipts JWT API (`/api/receipts/`)."""

from .category import CategoryNameSchema, CategoryOrderSchema, CategorySchema
from .export import (
    ReceiptExportCreateSchema,
    ReceiptExportFilterInfoSchema,
    ReceiptExportListSchema,
    ReceiptExportPartSchema,
    ReceiptExportPartUrlSchema,
    ReceiptExportQuoteSchema,
    ReceiptExportRequestSchema,
    ReceiptExportSchema,
)
from .inbox import ForwardingConfirmationSchema, InboxSchema, InboxUpdateSchema
from .receipt import (
    CategoryRefSchema,
    FileUrlSchema,
    ReceiptListSchema,
    ReceiptSchema,
    ReceiptUpdateSchema,
)
from .upload import UploadStartSchema, UploadTicketSchema
from .vendor import (
    MAX_MERGE_IDS,
    VendorConflictSchema,
    VendorListSchema,
    VendorMergeSchema,
    VendorSchema,
    VendorUpdateSchema,
)

__all__ = [
    'MAX_MERGE_IDS',
    'CategoryNameSchema',
    'CategoryOrderSchema',
    'CategoryRefSchema',
    'CategorySchema',
    'FileUrlSchema',
    'ForwardingConfirmationSchema',
    'InboxSchema',
    'InboxUpdateSchema',
    'ReceiptExportCreateSchema',
    'ReceiptExportFilterInfoSchema',
    'ReceiptExportListSchema',
    'ReceiptExportPartSchema',
    'ReceiptExportPartUrlSchema',
    'ReceiptExportQuoteSchema',
    'ReceiptExportRequestSchema',
    'ReceiptExportSchema',
    'ReceiptListSchema',
    'ReceiptSchema',
    'ReceiptUpdateSchema',
    'UploadStartSchema',
    'UploadTicketSchema',
    'VendorConflictSchema',
    'VendorListSchema',
    'VendorMergeSchema',
    'VendorSchema',
    'VendorUpdateSchema',
]
