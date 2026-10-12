"""The receipts JWT API (`/api/receipts/`, issue #98 phase 4): list, read, edit, trash and restore
receipts, direct-to-S3 uploads, the CSV export, categories, vendors and the inbox -- logged-in
users only, calling the same `apps.receipts.services` the web pages do. See `router.py` for why the
endpoint modules share one `Router` instance, and `common.py` for the status codes.
"""

from . import (
    categories as _categories,  # noqa: F401 -- imported for its side effect (registers endpoints)
)
from . import export as _export  # noqa: F401 -- same
from . import inbox as _inbox  # noqa: F401 -- same
from . import receipts as _receipts  # noqa: F401 -- same
from . import uploads as _uploads  # noqa: F401 -- same
from . import vendors as _vendors  # noqa: F401 -- same
from .router import router

__all__ = ['router']
