"""Template helpers for Web Push (issue #239): the browser script every signed-in page loads and
the account page's "Notifications on this device" section. Both render nothing while push is not
configured (`apps.core.services.push.is_configured`)."""

from typing import Any

from django import template
from django.template import Context

from apps.core.services import push

register = template.Library()


@register.inclusion_tag('core/partials/push_script.html', takes_context=True)
def push_script(context: Context) -> dict[str, Any]:
    """`core/js/push.js` with its URLs, the public key and whether this session already saved a
    subscription (so the script posts it again only after a new sign-in)."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    enabled = bool(user is not None and user.is_authenticated and push.is_configured())
    session = getattr(request, 'session', None)
    synced = bool(enabled and session is not None and session.get(push.SESSION_KEY))
    return {'enabled': enabled, 'public_key': push.public_key(), 'synced': synced}


@register.inclusion_tag('core/partials/push_settings.html')
def push_settings() -> dict[str, Any]:
    """The account page's section; empty while push is not configured."""
    return {'enabled': push.is_configured()}
