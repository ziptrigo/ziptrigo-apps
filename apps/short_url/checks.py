"""System checks for Short URLs, run by `manage.py check` and at `runserver` startup."""

import os

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_geoip_database(*args, **kwargs):
    """Warn when country statistics can't work: `GEOIP_PATH` unset, or not a file. Never an error:
    without it every click's country is just unknown, and the rest of the analytics are
    unaffected."""
    path = getattr(settings, 'GEOIP_PATH', '') or ''
    if path and os.path.isfile(path):
        return []
    reason = 'GEOIP_PATH is not set' if not path else f'GEOIP_PATH ({path}) is not a file'
    return [
        Warning(
            f'{reason}: Short URLs cannot tell which country a click came from.',
            hint='Download MaxMind GeoLite2-Country (a free account is needed) and point '
            'GEOIP_PATH at the .mmdb file; see README, "Short URLs". Until then the country '
            'statistics stay empty.',
            id='short_url.W001',
        )
    ]
