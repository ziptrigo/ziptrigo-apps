"""The shared `Router` instance for `/api/ft/` (spec section 14). `transfers.py` and `files.py`
each register their own endpoints onto this one object, so the router package stays one domain per
file (CLAUDE.md) without needing `Router.add_router` -- which only takes a static prefix, and can't
express a nested path parameter like `/transfers/{transfer_id}/files/...` -- to stitch them back
together. See `api/__init__.py` for how those two modules get imported (for that side effect) and
this `router` mounted into `config/api.py`.
"""

from ninja import Router

router = Router(tags=['File Transfer'])
