"""System check for Web Push (issue #239), run by `manage.py check`, `migrate` and `runserver`."""

from django.conf import settings
from django.core.checks import Warning, register

VAPID_SETTINGS = ('VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY', 'VAPID_SUBJECT')


@register()
def check_vapid_settings(*args, **kwargs):
    """Warn when push is configured in part only. Never an error (`migrate` runs the checks): with
    none set, push is simply off, as it is with some set."""
    missing = [name for name in VAPID_SETTINGS if not getattr(settings, name, '')]
    if not missing or len(missing) == len(VAPID_SETTINGS):
        problems = []
    else:
        problems = [
            Warning(
                f'Web Push is off: {", ".join(missing)} not set.',
                hint='Set all three of VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT '
                '(`./manage.py generate_vapid_keys`; see README, "Push notifications"), or none.',
                id='core.W020',
            )
        ]
    subject = getattr(settings, 'VAPID_SUBJECT', '')
    if subject and not subject.startswith(('mailto:', 'https://')):
        problems.append(
            Warning(
                'VAPID_SUBJECT must be a mailto: or https: URL; push services refuse other values.',
                hint='For example VAPID_SUBJECT=mailto:you@example.com.',
                id='core.W021',
            )
        )
    return problems
