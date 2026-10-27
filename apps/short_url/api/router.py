"""The shared `Router` instance for `/api/s/`. The endpoint modules (`links.py`, `stats.py`)
register onto this one object, the same shape as `apps.receipts.api.router`, so the package stays
one domain per file.

Authentication is set once, here, for the whole router (JWT; `JWTAuth` also rejects a user who is
not `ACTIVE`), so an endpoint can't be added without it.
"""

from ninja import Router

from apps.accounts.auth import JWTAuth

router = Router(tags=['Short URLs'], auth=JWTAuth())
