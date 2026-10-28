"""The shared `Router` instance for `/api/todo/`. Every endpoint module (`tasks.py`, `labels.py`,
`comments.py`, `reminders.py`, `attachments.py`, `filters.py`, `settings.py`, `parse.py`)
registers onto this one object -- the same shape as `apps.receipts.api.router` -- so the package
stays one domain per file.

Authentication is set once, here, for the whole router (JWT; `JWTAuth` also rejects a user who is
not `ACTIVE`), so an endpoint can't be added without it.
"""

from ninja import Router

from apps.accounts.auth import JWTAuth

router = Router(tags=['To-do'], auth=JWTAuth())
