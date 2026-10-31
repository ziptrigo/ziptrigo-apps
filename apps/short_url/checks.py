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


@register()
def check_safe_browsing_key(*args, **kwargs):
    """Warn when destinations are not checked against Safe Browsing (`SAFE_BROWSING_API_KEY`
    unset). Never an error: the destination rules, the block list, reports and takedown remain."""
    if getattr(settings, 'SAFE_BROWSING_API_KEY', ''):
        return []
    return [
        Warning(
            'SAFE_BROWSING_API_KEY is not set: Short URL destinations are not checked against '
            'Google Safe Browsing.',
            hint='Create an API key for the Safe Browsing API in a Google Cloud project and set '
            'SAFE_BROWSING_API_KEY; see README, "Short URLs: Safe Browsing".',
            id='short_url.W002',
        )
    ]
