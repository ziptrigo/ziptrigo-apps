from .account_settings import (
    category_create,
    category_delete,
    category_move,
    category_rename,
    regenerate_address,
    settings_page,
    update_inbox,
)
from .dashboard import dashboard, export_csv, receipt_list
from .detail import (
    body_view,
    detail,
    dismiss_duplicate_flag,
    download,
    reprocess_receipt,
    trash_receipt,
)
from .trash import delete_receipt_forever, restore_receipt, trash_page
from .uploads import upload_complete, upload_page, upload_start
from .vendors import (
    vendor_delete,
    vendor_list,
    vendor_merge,
    vendor_rename,
    vendor_suggest,
    vendor_vet,
    vendors_page,
)

__all__ = [
    'body_view',
    'category_create',
    'category_delete',
    'category_move',
    'category_rename',
    'dashboard',
    'delete_receipt_forever',
    'detail',
    'dismiss_duplicate_flag',
    'download',
    'export_csv',
    'receipt_list',
    'regenerate_address',
    'reprocess_receipt',
    'restore_receipt',
    'settings_page',
    'trash_page',
    'trash_receipt',
    'update_inbox',
    'upload_complete',
    'upload_page',
    'upload_start',
    'vendor_delete',
    'vendor_list',
    'vendor_merge',
    'vendor_rename',
    'vendor_suggest',
    'vendor_vet',
    'vendors_page',
]
