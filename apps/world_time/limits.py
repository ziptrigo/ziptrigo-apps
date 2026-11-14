"""The limits of World Time's fields. Here, not in `services/`, because the models need them and a
model can't import from the `services` package (its `__init__` imports the models)."""

TAB_NAME_MAX = 50
LOCATION_LABEL_MAX = 60
#: The longest text the location search reads.
SEARCH_QUERY_MAX = 60
#: How many cities, and how many time zones, one search offers.
SEARCH_CITY_RESULTS = 8
SEARCH_ZONE_RESULTS = 4
#: The longest id list the reorder endpoint takes.
MAX_REORDER_IDS = 200
#: How far from today the grid's date may be, in days either way.
MAX_DATE_DISTANCE_DAYS = 3660
