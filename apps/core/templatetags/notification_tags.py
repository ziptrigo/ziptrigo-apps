"""Template helpers for the notification bell and lists (issue #169)."""

from datetime import timedelta

from django import template
from django.template import Context
from django.utils import timezone
from django.utils.timesince import timesince

from apps.core.products import get_products
from apps.core.services import notifications

register = template.Library()

#: The badge stops counting here ("99+"): it is a nudge, not a number to read.
BADGE_CAP = 99


@register.simple_tag(takes_context=True)
def unread_notification_count(context: Context) -> int:
    """The signed-in user's unread count; 0 (with no query) for an anonymous visitor.

    A tag rather than a context processor so that only a page that actually renders the bell pays
    for the one indexed `COUNT`.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return 0
    return notifications.unread_count(user)


@register.filter
def badge_text(count: int) -> str:
    """`7`, or `99+` past the cap."""
    return f'{BADGE_CAP}+' if count > BADGE_CAP else str(count)


@register.filter
def ago(moment) -> str:
    """`just now`, `5 minutes ago`, `2 days ago` (one unit, like a chat app)."""
    if timezone.now() - moment < timedelta(minutes=1):
        return 'just now'
    return f'{timesince(moment, depth=1)} ago'


def _product(source: str):
    return next((product for product in get_products() if product.label == source), None)


@register.filter
def source_label(source: str) -> str:
    """The registered product's name for a notification's `source`, else the label made readable
    (`file_transfer` -> `File transfer`)."""
    product = _product(source)
    return product.name if product else source.replace('_', ' ').capitalize()


@register.filter
def source_icon(source: str) -> str:
    """The registered product's icon classes, else a generic bell."""
    product = _product(source)
    return product.icon if product else 'fas fa-bell'
