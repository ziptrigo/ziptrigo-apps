"""The shared `Router` instance for `/api/receipts/`. Every endpoint module (`receipts.py`,
`uploads.py`, `export.py`, `categories.py`, `vendors.py`, `inbox.py`) registers onto this one
object -- the same shape as `apps.file_transfer.api.router` -- so the package stays one domain per
file.

Authentication is set once, here, for the whole router (JWT; `JWTAuth` also rejects a user who is
not `ACTIVE`), so an endpoint can't be added without it.
"""

from ninja import Router

from apps.accounts.auth import JWTAuth

router = Router(tags=['Receipts'], auth=JWTAuth())
