"""The World Time JWT API (`/api/time/`, issue #269 PR 3): tabs, their locations, the place search,
the grid of a day, every location at one instant, shared events and the caps -- logged-in users
only, calling the same `apps.world_time.services` the web pages do. See `router.py` for why the
endpoint modules share one `Router` instance, and `common.py` for the status codes.
"""

from . import events as _events  # noqa: F401 -- imported for its side effect (registers endpoints)
from . import grid as _grid  # noqa: F401 -- same
from . import locations as _locations  # noqa: F401 -- same
from . import settings as _settings  # noqa: F401 -- same
from . import tabs as _tabs  # noqa: F401 -- same
from .router import router

__all__ = ['router']
