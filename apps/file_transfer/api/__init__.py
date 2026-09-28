"""The file_transfer JWT API (`/api/ft/`, spec section 14): create/list/get/update/delete a
transfer, finalize (send) it, manage its recipients, and upload files to a draft (including
resuming an interrupted upload) -- logged-in users only, mirroring the web send/dashboard flow
(anonymous sending stays web-only). See `router.py` for why this package's two endpoint modules
share one `Router` instance instead of nesting sub-routers.
"""

from . import files as _files  # noqa: F401 -- imported for its side effect (registers endpoints)
from . import transfers as _transfers  # noqa: F401 -- same
from .router import router

__all__ = ['router']
