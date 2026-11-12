"""The shared `Router` instance for `/api/notes/`. Every endpoint module (`notes.py`,
`versions.py`, `links.py`, `tags.py`, `filters.py`, `settings.py`, `shared.py`) registers onto this
one object -- the same shape as `apps.todo.api.router` -- so the package stays one domain per file.

Authentication is set once, here, for the whole router (JWT; `JWTAuth` also rejects a user who is
not `ACTIVE`), so an endpoint can't be added without it.
"""

from ninja import Router

from apps.accounts.auth import JWTAuth

router = Router(tags=['Notes'], auth=JWTAuth())
