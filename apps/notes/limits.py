"""The limits of a note's, a tag's and a saved filter's fields, and the list's page size.

They live here, not in `services/limits.py`, because the models need them and a model can't import
from the `services` package (its `__init__` imports the models).
"""

from datetime import timedelta

TITLE_MAX = 200
#: Equal to `apps.core.markdown.MAX_MARKDOWN_CHARS`, so a stored note always renders.
CONTENT_MAX = 50_000
TAG_NAME_MAX = 50
TAG_DESCRIPTION_MAX = 255
LINK_LABEL_MAX = 100
SAVED_FILTER_NAME_MAX = 50
MAX_SAVED_FILTERS = 50
#: A save by the same editor within this long of the newest version overwrites it in place, so a
#: burst of saves (or of ticked boxes) doesn't use up the version cap.
VERSION_COALESCE = timedelta(minutes=10)
PAGE_SIZE = 50
#: The longest id list the note and tag reorder endpoints take.
MAX_REORDER_IDS = 500
#: How much of a note's text a list row previews.
EXCERPT_CHARS = 140
