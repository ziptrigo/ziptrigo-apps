"""The shared `Router` instance for `/api/time/`. Every endpoint module (`tabs.py`,
`locations.py`, `grid.py`, `events.py`, `settings.py`) registers onto this one object, the same
shape as the other products' APIs.

Authentication is set once, here, for the whole router (JWT; `JWTAuth` also rejects a user who is
not `ACTIVE`), so an endpoint can't be added without it.
"""

from ninja import Router

from apps.accounts.auth import JWTAuth

router = Router(tags=['World Time'], auth=JWTAuth())
